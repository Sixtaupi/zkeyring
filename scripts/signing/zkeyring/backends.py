# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Romain Pelletant

import base64
import hashlib
import os
import re
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import requests
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa, utils
from keyring.errors import KeyringError

from zkeyring import ZkeyringError, error_causes
from zkeyring.auth import LoginSession, native_keyrings

TIMEOUT = 30
TRANSPORT_ERRORS = (
    (requests.exceptions.ProxyError, 'proxy connection failed'),
    (requests.exceptions.SSLError, 'TLS handshake or certificate verification failed'),
    (requests.exceptions.ConnectTimeout, 'connection timed out'),
    (requests.exceptions.ReadTimeout, 'response timed out'),
    (requests.exceptions.Timeout, 'request timed out'),
    (ConnectionRefusedError, 'connection refused'),
    (socket.gaierror, 'hostname lookup failed'),
)
PublicKey = ec.EllipticCurvePublicKey | rsa.RSAPublicKey | ed25519.Ed25519PublicKey
SIGNATURE_TYPES = ('ecdsa-p256', 'rsa-2048', 'rsa-3072', 'ed25519')
RSA_PSS = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32)
PREHASHED_SHA256 = utils.Prehashed(hashes.SHA256())


class Signer(Protocol):
    def public_key(self) -> PublicKey: ...

    def sign_digest(self, digest: bytes) -> bytes: ...


def signature_type(key: PublicKey) -> str:
    if isinstance(key, ec.EllipticCurvePublicKey) and isinstance(key.curve, ec.SECP256R1):
        return 'ecdsa-p256'
    if isinstance(key, rsa.RSAPublicKey) and key.key_size in (2048, 3072):
        return f'rsa-{key.key_size}'
    if isinstance(key, ed25519.Ed25519PublicKey):
        return 'ed25519'
    raise ZkeyringError('the signing key must be ECDSA P-256, RSA-2048, RSA-3072 or Ed25519')


def verify_signature(key: PublicKey, signature: bytes, digest: bytes) -> None:
    if isinstance(key, rsa.RSAPublicKey):
        key.verify(signature, digest, RSA_PSS, PREHASHED_SHA256)
    elif isinstance(key, ed25519.Ed25519PublicKey):
        key.verify(signature, digest)
    else:
        key.verify(signature, digest, ec.ECDSA(PREHASHED_SHA256))


def key_fingerprint(key: PublicKey) -> str:
    der = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()


def public_pem(key: PublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def load_public_key(value: bytes) -> PublicKey:
    try:
        if value.startswith((b'-----BEGIN PUBLIC KEY-----', b'-----BEGIN RSA PUBLIC KEY-----')):
            key = serialization.load_pem_public_key(value)
        else:
            key = serialization.load_der_public_key(value)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise ZkeyringError('invalid signing public key') from exc
    signature_type(key)
    return key


def required_text(values: dict, field: str) -> str:
    value = values.get(field)
    if not isinstance(value, str) or not value:
        raise ZkeyringError(f'missing or invalid {field}')
    return value


def encoded(value: bytes) -> str:
    return base64.b64encode(value).decode('ascii')


def decoded(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ZkeyringError('invalid base64 in signing service response') from exc


class PemProvider:
    def __init__(self, pem: bytes):
        try:
            key = serialization.load_pem_private_key(pem, password=None)
        except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
            raise ZkeyringError('expected an unencrypted PEM signing key') from exc
        signature_type(key.public_key())
        self._key = key

    def public_key(self) -> PublicKey:
        return self._key.public_key()

    def sign_digest(self, digest: bytes) -> bytes:
        if isinstance(self._key, rsa.RSAPrivateKey):
            return self._key.sign(digest, RSA_PSS, PREHASHED_SHA256)
        if isinstance(self._key, ed25519.Ed25519PrivateKey):
            return self._key.sign(digest)
        return self._key.sign(digest, ec.ECDSA(PREHASHED_SHA256))


class LocalKeyringProvider(PemProvider):
    def __init__(self, service: str, account: str):
        try:
            native = native_keyrings()
            if not native:
                raise ZkeyringError('no supported OS credential store is available')
            for candidate in native:
                if value := candidate.get_password(service, account):
                    super().__init__(value.encode())
                    return
        except (KeyringError, RuntimeError) as exc:
            raise ZkeyringError('could not read from the OS credential store') from exc
        raise ZkeyringError('signing key was not found in the OS credential store')


def pem_value(value: str, encoding: str) -> bytes:
    if encoding == 'pem':
        return value.encode()
    if encoding == 'base64':
        try:
            return base64.b64decode(value.strip(), validate=True)
        except ValueError as exc:
            raise ZkeyringError('invalid base64 signing key') from exc
    raise ZkeyringError('key encoding must be pem or base64')


class EnvironmentProvider(PemProvider):
    def __init__(self, variable: str, encoding: str):
        super().__init__(pem_value(environment(variable), encoding))


class GitLabSecretsProvider(PemProvider):
    def __init__(self, variable: str):
        path = Path(environment(variable))
        try:
            if not path.is_absolute() or not path.is_file():
                raise ZkeyringError('the GitLab secret variable must point to an absolute file')
            pem = path.read_bytes()
        except OSError as exc:
            raise ZkeyringError('could not read the GitLab secret file') from exc
        super().__init__(pem)


class SopsProvider(PemProvider):
    def __init__(self, path: Path):
        try:
            result = subprocess.run(
                [
                    'sops',
                    'decrypt',
                    '--input-type',
                    'binary',
                    '--output-type',
                    'binary',
                    '--',
                    path,
                ],
                capture_output=True,
                check=False,
                timeout=TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ZkeyringError('could not run SOPS') from exc
        if result.returncode:
            raise ZkeyringError('SOPS could not decrypt the keyring with the available identities')
        super().__init__(result.stdout)


def service_address(address: str) -> str:
    server = urlsplit(address)
    if (
        not server.hostname
        or server.username is not None
        or server.query
        or server.fragment
        or (
            server.scheme != 'https'
            and not (
                server.scheme == 'http' and server.hostname in ('localhost', '127.0.0.1', '::1')
            )
        )
    ):
        raise ZkeyringError('signing service URL must use HTTPS or loopback HTTP')
    return address.rstrip('/')


class SigningService:
    def __init__(
        self,
        address: str,
        headers: dict[str, str],
        refresh: Callable[[], dict[str, str]] | None = None,
    ):
        self.address = service_address(address)
        self.headers = headers
        # Called once to replace a session that the service refuses.
        self.refresh = refresh

    def request(self, path: str, data: dict | None = None) -> dict:
        response = self.send(path, data)
        if response.status_code in (401, 403) and self.refresh is not None:
            refresh, self.refresh = self.refresh, None
            self.headers = refresh()
            response = self.send(path, data)
        if response.status_code != 200:
            raise ZkeyringError(f'signing service returned HTTP {response.status_code}')
        try:
            result = response.json()
        except ValueError as exc:
            raise ZkeyringError('invalid JSON from signing service') from exc
        if not isinstance(result, dict):
            raise ZkeyringError('invalid signing service response')
        return result

    def send(self, path: str, data: dict | None):
        try:
            return requests.request(
                'GET' if data is None else 'POST',
                self.address + path,
                headers=self.headers,
                json=data,
                timeout=TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            detail = next(
                (
                    message
                    for cause in (exc, *error_causes(exc))
                    for kind, message in TRANSPORT_ERRORS
                    if isinstance(cause, kind)
                ),
                type(exc).__name__,
            )
            raise ZkeyringError(f'could not reach the signing service: {detail}') from exc


class VaultKVProvider(PemProvider):
    def __init__(
        self,
        service: SigningService,
        mount: str,
        path: str,
        field: str,
        version: int,
        encoding: str,
    ):
        name = '/v1/' + quote(mount, safe='/') + '/data/' + quote(path, safe='/')
        response = service.request(f'{name}?version={version}').get('data')
        if not isinstance(response, dict) or not isinstance(response.get('data'), dict):
            raise ZkeyringError('Vault KV v2 returned no signing key')
        metadata = response.get('metadata')
        if not isinstance(metadata, dict) or metadata.get('version') != version:
            raise ZkeyringError('Vault KV version does not match the profile')
        super().__init__(pem_value(required_text(response['data'], field), encoding))


class VaultTransitProvider:
    name = 'Vault Transit'

    def __init__(self, service: SigningService, mount: str, key: str, version: int):
        self.service = service
        self.mount = '/v1/' + quote(mount, safe='/')
        self.key = quote(key, safe='')
        self.version = version
        self.algorithm = None

    def public_key(self) -> PublicKey:
        data = self.service.request(f'{self.mount}/keys/{self.key}').get('data')
        if not isinstance(data, dict) or data.get('type') not in SIGNATURE_TYPES:
            raise ZkeyringError(f'{self.name} key must use a supported MCUboot signature type')
        if data.get('exportable') is not False or data.get('allow_plaintext_backup') is not False:
            raise ZkeyringError(
                f'{self.name} requires a non-exportable key without plaintext backup'
            )
        try:
            pem = required_text(data['keys'][str(self.version)], 'public_key')
        except (KeyError, TypeError, AttributeError) as exc:
            raise ZkeyringError(f'{self.name} key version has no public key') from exc
        if data['type'] == 'ed25519' and not pem.startswith('-----BEGIN '):
            try:
                key = ed25519.Ed25519PublicKey.from_public_bytes(decoded(pem))
            except ValueError as exc:
                raise ZkeyringError(f'invalid {self.name} Ed25519 public key') from exc
        else:
            key = load_public_key(pem.encode())
        algorithm = signature_type(key)
        if algorithm != data['type']:
            raise ZkeyringError(f'{self.name} public key does not match its declared type')
        self.algorithm = algorithm
        return key

    def sign_digest(self, digest: bytes) -> bytes:
        if self.algorithm is None:
            self.public_key()
        path = f'{self.mount}/sign/{self.key}'
        request = {'input': encoded(digest), 'key_version': self.version}
        if self.algorithm != 'ed25519':
            path += '/sha2-256'
            request['prehashed'] = True
            if self.algorithm in ('rsa-2048', 'rsa-3072'):
                request.update(signature_algorithm='pss', salt_length='hash')
            else:
                request['marshaling_algorithm'] = 'asn1'
        data = self.service.request(path, request).get('data')
        if not isinstance(data, dict):
            raise ZkeyringError(f'invalid {self.name} signing response')
        signature = required_text(data, 'signature')
        prefix = f'vault:v{self.version}:'
        if not signature.startswith(prefix):
            raise ZkeyringError(f'{self.name} returned a different signing key version')
        return decoded(signature.removeprefix(prefix))


class OpenBaoProvider(VaultTransitProvider):
    name = 'OpenBao Transit'


class InfisicalProvider:
    def __init__(self, service: SigningService, key_id: str, version: int):
        self.service = service
        self.key_id = key_id
        self.version = version
        self.path = '/api/v1/kms/keys/' + quote(key_id, safe='')
        self.algorithm = None

    def public_key(self) -> PublicKey:
        key = self.service.request(self.path).get('key')
        if not isinstance(key, dict) or key.get('id') != self.key_id:
            raise ZkeyringError('invalid Infisical key metadata')
        if key.get('isExportable') is not False or key.get('keyUsage') != 'sign-verify':
            raise ZkeyringError('Infisical signing requires a non-exportable sign/verify key')
        if key.get('version') != self.version:
            raise ZkeyringError('Infisical current key version does not match the profile')
        response = self.service.request(f'{self.path}/public-key')
        value = required_text(response, 'publicKey')
        key = load_public_key(value.encode() if value.startswith('-----BEGIN ') else decoded(value))
        self.algorithm = {'ecdsa-p256': 'ECDSA_SHA_256', 'ed25519': 'ED25519'}.get(
            signature_type(key)
        )
        if self.algorithm is None:
            raise ZkeyringError('Infisical requires an ECDSA P-256 or Ed25519 signing key')
        return key

    def sign_digest(self, digest: bytes) -> bytes:
        if self.algorithm is None:
            self.public_key()
        response = self.service.request(
            f'{self.path}/sign',
            {
                'data': encoded(digest),
                'signingAlgorithm': self.algorithm,
                'isDigest': self.algorithm != 'ED25519',
            },
        )
        if (
            response.get('keyId') != self.key_id
            or response.get('signingAlgorithm') != self.algorithm
        ):
            raise ZkeyringError('Infisical returned an unexpected key or signing algorithm')
        return decoded(required_text(response, 'signature'))


def environment(name: str) -> str:
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
        raise ZkeyringError('invalid environment variable name')
    value = os.environ.get(name)
    if not value:
        raise ZkeyringError(f'set {name} to use the signing backend')
    return value


def required_version(profile: dict) -> int:
    version = profile.get('version')
    if type(version) is not int or version <= 0:
        raise ZkeyringError('remote profiles require a positive integer version')
    return version


def service_path(path: str, field: str) -> str:
    if any(part in ('', '.', '..') for part in path.split('/')):
        raise ZkeyringError(f'invalid {field}')
    return path


def optional_text(values: dict, field: str) -> str | None:
    return required_text(values, field) if field in values else None


def service_url(profile: dict, variable: str) -> str:
    # The environment overrides the profile, so one profile can target several services.
    address = os.environ.get(variable) or optional_text(profile, 'url')
    if not address:
        raise ZkeyringError(f'set {variable} or the profile url to use the signing backend')
    return service_address(address)


def login_session(profile: dict, config: Path, name: str) -> LoginSession:
    backend = required_text(profile, 'backend')
    if backend in ('vault-transit', 'vault-kv'):
        vendor, prefix = 'vault', 'VAULT'
    elif backend == 'openbao':
        vendor, prefix = 'bao', 'BAO'
    elif backend == 'infisical':
        if any(field in profile for field in ('namespace', 'role', 'auth_path')):
            raise ZkeyringError('namespace, role and auth_path apply to Vault and OpenBao profiles')
        return LoginSession(
            'infisical', service_url(profile, 'INFISICAL_API_URL'), '', config, name
        )
    else:
        raise ZkeyringError('login and logout require a Vault, OpenBao or Infisical profile')
    namespace = os.environ.get(f'{prefix}_NAMESPACE') or optional_text(profile, 'namespace') or ''
    return LoginSession(
        vendor,
        service_url(profile, f'{prefix}_ADDR'),
        namespace,
        config,
        name,
        role=optional_text(profile, 'role'),
        auth_path=optional_text(profile, 'auth_path'),
    )


def signing_service(profile: dict, config: Path, name: str) -> SigningService:
    session = login_session(profile, config, name)
    refresh = session.relogin if session.renewable() else None
    return SigningService(session.address, session.headers(), refresh)


def mount_path(profile: dict, default: str) -> str:
    path = required_text(profile, 'mount') if 'mount' in profile else default
    return service_path(path.strip('/'), 'secret engine mount path')


def get_signer(profile: dict, config: Path, name: str) -> Signer:
    backend = required_text(profile, 'backend')
    config_dir = config.parent
    if backend == 'local-keyring':
        return LocalKeyringProvider(
            required_text(profile, 'service'), required_text(profile, 'key')
        )
    if backend == 'sops':
        return SopsProvider((config_dir / required_text(profile, 'keyring')).resolve())
    if backend == 'environment':
        return EnvironmentProvider(
            required_text(profile, 'variable'), profile.get('encoding', 'pem')
        )
    if backend == 'gitlab-secrets':
        return GitLabSecretsProvider(required_text(profile, 'variable'))
    if backend == 'pkcs11':
        from zkeyring.pkcs11 import PKCS11Provider

        return PKCS11Provider(
            (config_dir / required_text(profile, 'module')).resolve(),
            required_text(profile, 'token'),
            required_text(profile, 'key'),
            environment(required_text(profile, 'pin_variable')),
        )
    if backend == 'vault-kv':
        path = service_path(required_text(profile, 'path'), 'Vault KV secret path')
        return VaultKVProvider(
            signing_service(profile, config, name),
            mount_path(profile, 'secret'),
            path,
            required_text(profile, 'field'),
            required_version(profile),
            profile.get('encoding', 'pem'),
        )
    if backend in ('vault-transit', 'openbao', 'infisical'):
        version = required_version(profile)
        key = required_text(profile, 'key')
        if not re.fullmatch(r'[\w-]+', key):
            raise ZkeyringError('invalid signing key name or ID')
        service = signing_service(profile, config, name)
        if backend == 'infisical':
            return InfisicalProvider(service, key, version)
        provider = OpenBaoProvider if backend == 'openbao' else VaultTransitProvider
        return provider(service, mount_path(profile, 'transit'), key, version)
    raise ZkeyringError(f'unknown signing backend {backend!r}')
