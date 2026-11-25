# SPDX-License-Identifier: Apache-2.0

import base64
import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import keyring
import requests
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from keyring.backends.chainer import ChainerBackend
from keyring.errors import KeyringError

from zkeyring import ZkeyringError

TIMEOUT = 30
ALGORITHM = 'ECDSA_SHA_256'


class Signer(Protocol):
    def public_key(self) -> ec.EllipticCurvePublicKey: ...

    def sign_digest(self, digest: bytes) -> bytes: ...


def key_fingerprint(key: ec.EllipticCurvePublicKey) -> str:
    der = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()


def public_pem(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def load_public_key(value: bytes) -> ec.EllipticCurvePublicKey:
    try:
        if value.startswith(b'-----BEGIN PUBLIC KEY-----'):
            key = serialization.load_pem_public_key(value)
        else:
            key = serialization.load_der_public_key(value)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise ZkeyringError('invalid signing public key') from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ZkeyringError('the signing key must be ECDSA P-256')
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
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise ZkeyringError('the signing key must be ECDSA P-256')
        self._key = key

    def public_key(self) -> ec.EllipticCurvePublicKey:
        return self._key.public_key()

    def sign_digest(self, digest: bytes) -> bytes:
        return self._key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))


class LocalKeyringProvider(PemProvider):
    NATIVE_BACKENDS = {
        'keyring.backends.SecretService',
        'keyring.backends.Windows',
        'keyring.backends.kwallet',
        'keyring.backends.libsecret',
        'keyring.backends.macOS',
    }

    def __init__(self, service: str, account: str):
        try:
            backend = keyring.get_keyring()
            available = backend.backends if isinstance(backend, ChainerBackend) else (backend,)
            native = [
                candidate
                for candidate in available
                if type(candidate).__module__ in self.NATIVE_BACKENDS
            ]
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


class SigningService:
    def __init__(self, address: str, headers: dict[str, str]):
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
        self.address = address.rstrip('/')
        self.headers = headers

    def request(self, path: str, data: dict | None = None) -> dict:
        try:
            response = requests.request(
                'GET' if data is None else 'POST',
                self.address + path,
                headers=self.headers,
                json=data,
                timeout=TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise ZkeyringError('could not reach the signing service') from exc
        if response.status_code != 200:
            raise ZkeyringError(f'signing service returned HTTP {response.status_code}')
        try:
            result = response.json()
        except ValueError as exc:
            raise ZkeyringError('invalid JSON from signing service') from exc
        if not isinstance(result, dict):
            raise ZkeyringError('invalid signing service response')
        return result


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

    def public_key(self) -> ec.EllipticCurvePublicKey:
        data = self.service.request(f'{self.mount}/keys/{self.key}').get('data')
        if not isinstance(data, dict) or data.get('type') != 'ecdsa-p256':
            raise ZkeyringError(f'{self.name} key must be of type ecdsa-p256')
        if data.get('exportable') is not False or data.get('allow_plaintext_backup') is not False:
            raise ZkeyringError(
                f'{self.name} requires a non-exportable key without plaintext backup'
            )
        try:
            pem = required_text(data['keys'][str(self.version)], 'public_key')
        except (KeyError, TypeError, AttributeError) as exc:
            raise ZkeyringError(f'{self.name} key version has no public key') from exc
        return load_public_key(pem.encode())

    def sign_digest(self, digest: bytes) -> bytes:
        data = self.service.request(
            f'{self.mount}/sign/{self.key}/sha2-256',
            {
                'input': encoded(digest),
                'prehashed': True,
                'marshaling_algorithm': 'asn1',
                'key_version': self.version,
            },
        ).get('data')
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

    def public_key(self) -> ec.EllipticCurvePublicKey:
        key = self.service.request(self.path).get('key')
        if not isinstance(key, dict) or key.get('id') != self.key_id:
            raise ZkeyringError('invalid Infisical key metadata')
        if key.get('isExportable') is not False or key.get('keyUsage') != 'sign-verify':
            raise ZkeyringError('Infisical signing requires a non-exportable sign/verify key')
        if key.get('version') != self.version:
            raise ZkeyringError('Infisical current key version does not match the profile')
        response = self.service.request(f'{self.path}/public-key')
        value = required_text(response, 'publicKey')
        if not value.startswith('-----BEGIN PUBLIC KEY-----'):
            return load_public_key(decoded(value))
        return load_public_key(value.encode())

    def sign_digest(self, digest: bytes) -> bytes:
        response = self.service.request(
            f'{self.path}/sign',
            {'data': encoded(digest), 'signingAlgorithm': ALGORITHM, 'isDigest': True},
        )
        if response.get('keyId') != self.key_id or response.get('signingAlgorithm') != ALGORITHM:
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


def vault_service(profile: dict, *, openbao: bool) -> SigningService:
    prefix = 'BAO' if openbao else 'VAULT'
    address = required_text(profile, 'url') if 'url' in profile else environment(f'{prefix}_ADDR')
    headers = {'X-Vault-Token': environment(f'{prefix}_TOKEN')}
    if namespace := os.environ.get(f'{prefix}_NAMESPACE'):
        headers['X-Vault-Namespace'] = namespace
    return SigningService(address, headers)


def mount_path(profile: dict, default: str) -> str:
    path = required_text(profile, 'mount') if 'mount' in profile else default
    return service_path(path.strip('/'), 'secret engine mount path')


def get_signer(profile: dict, config_dir: Path) -> Signer:
    backend = required_text(profile, 'backend')
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
            vault_service(profile, openbao=False),
            mount_path(profile, 'secret'),
            path,
            required_text(profile, 'field'),
            required_version(profile),
            profile.get('encoding', 'pem'),
        )
    if backend in ('vault', 'vault-transit', 'openbao', 'infisical'):
        version = required_version(profile)
        key = required_text(profile, 'key')
        if not re.fullmatch(r'[\w-]+', key):
            raise ZkeyringError('invalid signing key name or ID')
    if backend in ('vault', 'vault-transit', 'openbao'):
        provider = OpenBaoProvider if backend == 'openbao' else VaultTransitProvider
        return provider(
            vault_service(profile, openbao=backend == 'openbao'),
            mount_path(profile, 'transit'),
            key,
            version,
        )
    if backend == 'infisical':
        address = (
            required_text(profile, 'url') if 'url' in profile else environment('INFISICAL_API_URL')
        )
        headers = {'Authorization': f"Bearer {environment('INFISICAL_TOKEN')}"}
        return InfisicalProvider(SigningService(address, headers), key, version)
    raise ZkeyringError(f'unknown signing backend {backend!r}')
