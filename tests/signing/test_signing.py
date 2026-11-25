# SPDX-License-Identifier: Apache-2.0

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from zkeyring import ZkeyringError
from zkeyring.backends import (
    InfisicalProvider,
    OpenBaoProvider,
    SigningService,
    VaultTransitProvider,
    key_fingerprint,
    load_public_key,
    public_pem,
)
from zkeyring.cli import read_metadata
from zkeyring.cli import sign as sign_image

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = Path(os.environ.get('ZKEYRING_TEST_WORKSPACE', ROOT.parent))
IMGTOOL = WORKSPACE / 'bootloader/mcuboot/scripts/imgtool.py'


def run(*args, env=None, check=True, timeout=120):
    result = subprocess.run(
        args, cwd=ROOT, env=env, capture_output=True, check=False, timeout=timeout
    )
    if check:
        assert result.returncode == 0, (result.stdout + result.stderr).decode(errors='replace')
    return result


def cli(*args, env=None, check=True):
    return run(sys.executable, '-m', 'zkeyring.cli', *args, env=env, check=check)


def profile_file(path, /, **settings):
    path.write_text(
        '[profiles.test]\n'
        + ''.join(f'{name} = {json.dumps(value)}\n' for name, value in settings.items()),
        encoding='utf-8',
    )
    return path


def synthetic_build(directory, public_key):
    image = directory / 'zephyr/zephyr.bin'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'\x00' * 512 + b'zkeyring round trip' * 32)
    (directory / 'zkeyring-public.pem').write_bytes(public_pem(public_key))
    (directory / 'zkeyring-sign.json').write_text(
        json.dumps(
            {
                'schema': 1,
                'image': image.name,
                'version': '1.2.3+4',
                'header_size': 512,
                'slot_size': 8192,
                'align': 4,
                'overwrite_only': False,
            }
        ),
        encoding='utf-8',
    )
    return image


def sign(config, build, output, env=None, check=True):
    return cli(
        'sign',
        '--config',
        config,
        '--profile',
        'test',
        '--build-dir',
        build,
        '--output',
        output,
        env=env,
        check=check,
    )


def http(method, url, *, headers=None, data=None, expected=(200,)):
    response = requests.request(method, url, headers=headers, json=data, timeout=15)
    assert response.status_code in expected, f'{method} returned HTTP {response.status_code}'
    if response.content:
        return response.json()
    return {}


@pytest.mark.parametrize('value', ['../secret.bin', 'firmware.hex', 'firmware.bin/../a.bin'])
def test_rejects_invalid_metadata_path(tmp_path, value):
    image = synthetic_build(tmp_path, ec.generate_private_key(ec.SECP256R1()).public_key())
    metadata = tmp_path / 'zkeyring-sign.json'
    data = json.loads(metadata.read_text())
    data['image'] = value
    metadata.write_text(json.dumps(data))
    with pytest.raises(ZkeyringError, match='invalid image parameters'):
        read_metadata(tmp_path)
    assert image.is_file()


def test_rejects_non_loopback_plain_http():
    with pytest.raises(ZkeyringError, match='HTTPS or loopback'):
        SigningService('http://signer.example', {})


def test_rejects_service_redirect(monkeypatch):
    def redirected(*args, **kwargs):
        assert kwargs['allow_redirects'] is False
        response = requests.Response()
        response.status_code = 302
        return response

    monkeypatch.setattr(requests, 'request', redirected)
    with pytest.raises(ZkeyringError, match='HTTP 302'):
        SigningService('https://signer.example', {}).request('/sign', {})


def test_service_timeout_is_reported_without_credentials(monkeypatch):
    def timed_out(*args, **kwargs):
        raise requests.Timeout('secret token must not appear in the error')

    monkeypatch.setattr(requests, 'request', timed_out)
    with pytest.raises(ZkeyringError, match=r'^could not reach the signing service$'):
        SigningService('https://signer.example', {}).request('/sign', {})


def test_rejects_double_hash_and_keeps_previous_output(tmp_path, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    synthetic_build(tmp_path, key.public_key())
    output = tmp_path / 'signed.bin'
    output.write_bytes(b'previous artifact')
    args = SimpleNamespace(
        build_dir=tmp_path, output=output, imgtool=IMGTOOL, config=tmp_path / 'zkeyring.toml'
    )

    def sign_with_second_hash(digest):
        return key.sign(digest, ec.ECDSA(hashes.SHA256()))

    signer = SimpleNamespace(public_key=key.public_key, sign_digest=sign_with_second_hash)
    monkeypatch.setattr('zkeyring.cli.get_signer', lambda *_: signer)
    profile = {'fingerprint': key_fingerprint(key.public_key())}
    with pytest.raises(ZkeyringError, match='signature does not match'):
        sign_image(args, profile)
    assert output.read_bytes() == b'previous artifact'
    assert not list(tmp_path.glob('.zkeyring-*'))


def test_rejects_invalid_build_key_before_signing(tmp_path):
    synthetic_build(tmp_path, ec.generate_private_key(ec.SECP256R1()).public_key())
    config = profile_file(tmp_path / 'zkeyring.toml', backend='sops', fingerprint='0' * 64)
    output = tmp_path / 'signed.bin'
    result = sign(config, tmp_path, output, check=False)
    assert result.returncode == 1
    assert b'build public key does not match' in result.stderr
    assert not output.exists()


def age_identity(home):
    identity_file = home / f'{uuid4()}.age'
    run('age-keygen', '-o', identity_file)
    public = run('age-keygen', '-y', identity_file).stdout.decode().strip()
    private = identity_file.read_text()
    identity_file.unlink()
    return public, private


@pytest.mark.skipif(
    not shutil.which('sops') or not shutil.which('age-keygen'), reason='needs SOPS/age'
)
def test_sops_recipients_and_native_imgtool(tmp_path):
    dev, dev_identity = age_identity(tmp_path)
    ci, ci_identity = age_identity(tmp_path)
    _, stranger = age_identity(tmp_path)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    image = synthetic_build(tmp_path / 'build', key.public_key())
    keyring = tmp_path / 'current.sops'
    encrypted = subprocess.run(
        [
            'sops',
            'encrypt',
            '--age',
            f'{dev},{ci}',
            '--input-type',
            'binary',
            '--output-type',
            'binary',
            '/dev/stdin',
        ],
        input=pem,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    keyring.write_bytes(encrypted)
    historical = tmp_path / 'historical.sops'
    historical.write_bytes(encrypted)
    config = profile_file(
        tmp_path / 'zkeyring.toml',
        backend='sops',
        keyring=keyring.name,
        fingerprint=key_fingerprint(key.public_key()),
    )
    dev_env = {**os.environ, 'SOPS_AGE_KEY': dev_identity}
    ci_env = {**os.environ, 'SOPS_AGE_KEY': ci_identity}
    unknown_env = {**os.environ, 'SOPS_AGE_KEY': stranger}
    paths = [tmp_path / 'dev.bin', tmp_path / 'ci.bin']
    with ThreadPoolExecutor(max_workers=2) as pool:
        dev_result = pool.submit(sign, config, image.parent.parent, paths[0], dev_env)
        ci_result = pool.submit(sign, config, image.parent.parent, paths[1], ci_env)
        assert b'PRIVATE KEY' not in dev_result.result().stdout + ci_result.result().stdout
    verification_key = image.parent.parent / 'zkeyring-public.pem'
    for output in paths:
        run(sys.executable, IMGTOOL, 'verify', '-k', verification_key, output)

    denied = tmp_path / 'denied.bin'
    result = sign(config, image.parent.parent, denied, unknown_env, check=False)
    assert result.returncode == 1
    assert b'could not decrypt' in result.stderr
    assert not denied.exists()

    rules = tmp_path / 'recipients.yaml'
    rules.write_text(f'creation_rules:\n  - age: {ci}\n')
    run('sops', '--config', rules, 'updatekeys', '-y', keyring, env=ci_env)
    run(
        'sops',
        'rotate',
        '--input-type',
        'binary',
        '--output-type',
        'binary',
        '--in-place',
        keyring,
        env=ci_env,
    )
    result = sign(config, image.parent.parent, denied, dev_env, check=False)
    assert b'could not decrypt' in result.stderr
    assert not denied.exists()
    sign(config, image.parent.parent, paths[1], ci_env)
    result = run(
        'sops',
        'decrypt',
        '--input-type',
        'binary',
        '--output-type',
        'binary',
        historical,
        env=dev_env,
    )
    assert result.stdout == pem
    assert not list(tmp_path.rglob('.zkeyring-*'))
    for artifact in tmp_path.rglob('*'):
        if artifact.is_file():
            assert b'PRIVATE KEY-----' not in artifact.read_bytes()


def provision_transit(backend):
    prefix = 'BAO' if backend == 'openbao' else 'VAULT'
    url = os.environ.get(f'ZKEYRING_TEST_{prefix}_URL')
    if not url:
        pytest.skip(f'needs an ephemeral {backend}')
    root = {'X-Vault-Token': os.environ[f'ZKEYRING_TEST_{prefix}_TOKEN']}
    mount = f'zkeyring-{uuid4().hex}'
    http(
        'POST',
        f'{url}/v1/sys/mounts/{mount}',
        headers=root,
        data={'type': 'transit'},
        expected=(204,),
    )
    http(
        'POST',
        f'{url}/v1/{mount}/keys/firmware',
        headers=root,
        data={'type': 'ecdsa-p256', 'exportable': False},
        expected=(200, 204),
    )
    policy = f'''
        path "{mount}/keys/firmware" {{ capabilities = ["read"] }}
        path "{mount}/sign/firmware/sha2-256" {{ capabilities = ["update"] }}
    '''
    http(
        'PUT',
        f'{url}/v1/sys/policies/acl/{mount}',
        headers=root,
        data={'policy': policy},
        expected=(204,),
    )
    token = http(
        'POST',
        f'{url}/v1/auth/token/create',
        headers=root,
        data={'policies': [mount], 'no_default_policy': True, 'ttl': '15m'},
    )['auth']['client_token']
    return backend, prefix, url, root, token, mount


@pytest.fixture(params=['vault-transit', 'openbao'])
def transit(request):
    return provision_transit(request.param)


def compile_qemu(tmp_path, public_key):
    (tmp_path / 'public.pem').write_bytes(public_pem(public_key))
    signing_config = tmp_path / 'zsigning.conf'
    signing_config.write_text('CONFIG_ZSIGNING=y\nCONFIG_ZSIGNING_DEFERRED=y\n', encoding='utf-8')
    build_dir = tmp_path / 'build'
    env = os.environ.copy()
    for name in (
        'VAULT_TOKEN',
        'BAO_TOKEN',
        'INFISICAL_TOKEN',
        'ZKEYRING_TEST_VAULT_TOKEN',
        'ZKEYRING_TEST_BAO_TOKEN',
        'SOPS_AGE_KEY',
    ):
        env.pop(name, None)
    run(
        'west',
        'build',
        '--sysbuild',
        '-b',
        'qemu_cortex_m0',
        '-d',
        build_dir,
        WORKSPACE / 'zephyr/samples/hello_world',
        '--',
        '-DSB_CONFIG_BOOTLOADER_MCUBOOT=y',
        '-DSB_CONFIG_BOOT_SIGNATURE_TYPE_ECDSA_P256=y',
        '-DSB_CONFIG_MCUBOOT_MODE_SINGLE_APP=y',
        '-Dmcuboot_CONFIG_BOOT_VALIDATE_SLOT0=y',
        f'-DSB_CONFIG_BOOT_SIGNATURE_KEY_FILE="{tmp_path / "public.pem"}"',
        f'-Dhello_world_EXTRA_CONF_FILE={signing_config}',
        env=env,
        timeout=300,
    )
    return build_dir


def boot_output(bootloader, image):
    try:
        result = run(
            'qemu-system-arm',
            '-machine',
            'microbit',
            '-nographic',
            '-kernel',
            bootloader,
            '-device',
            f'loader,file={image},addr=0x8000,force-raw=on',
            check=False,
            timeout=4,
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        return (exc.stdout or b'') + (exc.stderr or b'')


def signing_inputs(build_dir, destination):
    unsigned, _, public = read_metadata(build_dir)
    image_build = unsigned.parent.parent
    (destination / 'zephyr').mkdir(parents=True)
    for path in (unsigned, image_build / 'zkeyring-sign.json', public):
        shutil.copyfile(path, destination / path.relative_to(image_build))
    return destination


def test_transit_signs_qemu_and_revokes_access(tmp_path, transit):
    backend, prefix, url, root, token, mount = transit
    provider = OpenBaoProvider if backend == 'openbao' else VaultTransitProvider
    signer = provider(SigningService(url, {'X-Vault-Token': token}), mount, 'firmware', 1)
    key = signer.public_key()
    build_dir = compile_qemu(tmp_path, key)
    config = profile_file(
        tmp_path / 'zkeyring.toml',
        backend=backend,
        url=url,
        mount=mount,
        key='firmware',
        version=1,
        fingerprint=key_fingerprint(key),
    )
    signed = tmp_path / 'signed.bin'
    env = {**os.environ, f'{prefix}_TOKEN': token}
    assert not list(build_dir.glob('**/*.signed.bin'))
    inputs = signing_inputs(build_dir, tmp_path / 'signing-inputs')
    sign(config, inputs, signed, env)
    bootloader = build_dir / 'mcuboot/zephyr/zephyr.elf'
    output = boot_output(bootloader, signed)
    assert b'Hello World!' in output, output

    corrupted = tmp_path / 'corrupted.bin'
    mutated = bytearray(signed.read_bytes())
    header_size = int.from_bytes(mutated[8:10], 'little')
    mutated[header_size + 16] ^= 1
    corrupted.write_bytes(mutated)
    output = boot_output(bootloader, corrupted)
    assert b'Hello World!' not in output and b'Unable to find bootable image' in output, output

    unsigned, options, _ = read_metadata(build_dir)
    wrong_key = tmp_path / 'wrong-key.pem'
    run(sys.executable, IMGTOOL, 'keygen', '-t', 'ecdsa-p256', '-k', wrong_key)
    wrong_image = tmp_path / 'wrong-key.bin'
    run(sys.executable, IMGTOOL, 'sign', *options, '-k', wrong_key, unsigned, wrong_image)
    wrong_key.unlink()
    output = boot_output(bootloader, wrong_image)
    assert b'Hello World!' not in output and b'Unable to find bootable image' in output, output

    http('GET', f'{url}/v1/{mount}/export/signing-key/firmware', headers=root, expected=(400,))
    http(
        'POST', f'{url}/v1/{mount}/keys/firmware/rotate', headers=root, data={}, expected=(200, 204)
    )
    assert signer.public_key().public_numbers() == key.public_numbers()
    http(
        'POST', f'{url}/v1/auth/token/revoke', headers=root, data={'token': token}, expected=(204,)
    )
    with pytest.raises(ZkeyringError, match='HTTP 403'):
        signer.sign_digest(b'\x00' * 32)
    denied = tmp_path / 'denied.bin'
    assert sign(config, build_dir, denied, env, check=False).returncode == 1
    assert not denied.exists()


def test_public_key_roundtrip():
    key = ec.generate_private_key(ec.SECP256R1()).public_key()
    assert key_fingerprint(load_public_key(public_pem(key))) == key_fingerprint(key)


@pytest.fixture
def infisical():
    url = os.environ.get('ZKEYRING_TEST_INFISICAL_URL')
    if not url:
        pytest.skip('needs an ephemeral Infisical instance')
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if requests.get(f'{url}/api/status', timeout=2).ok:
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    else:
        pytest.fail('Infisical did not become ready')

    admin = http(
        'POST',
        f'{url}/api/v1/admin/signup',
        data={
            'email': 'signing-tests@example.com',
            'password': secrets.token_urlsafe(18) + 'Aa@9',
            'firstName': 'Signing',
            'organizationName': 'zkeyring tests',
        },
    )
    org_id = admin['organization']['id']
    selected = http(
        'POST',
        f'{url}/api/v3/auth/select-organization',
        headers={'Authorization': f"Bearer {admin['token']}"},
        data={'organizationId': org_id},
    )
    headers = {'Authorization': f"Bearer {selected['token']}"}
    project = http(
        'POST',
        f'{url}/api/v1/projects',
        headers=headers,
        data={'projectName': 'Firmware signing', 'type': 'kms'},
    )['project']
    project_api = f"{url}/api/v1/projects/{project['id']}"
    key = http(
        'POST',
        f'{url}/api/v1/kms/keys',
        headers=headers,
        data={
            'projectId': project['id'],
            'name': 'firmware-signing',
            'keyUsage': 'sign-verify',
            'algorithm': 'ECC_NIST_P256',
            'isExportable': False,
        },
    )['key']
    identity = http(
        'POST',
        f'{url}/api/v1/identities',
        headers=headers,
        data={'name': 'signing-runner', 'organizationId': org_id},
    )['identity']
    roles = http('GET', f'{project_api}/roles', headers=headers)['roles']
    operator = next(role['slug'] for role in roles if role['name'] == 'Cryptographic Operator')
    http(
        'POST',
        f"{project_api}/memberships/identities/{identity['id']}",
        headers=headers,
        data={'roles': [{'role': 'viewer'}, {'role': operator}]},
    )
    auth_path = f"{url}/api/v1/auth/token-auth/identities/{identity['id']}"
    http(
        'POST',
        auth_path,
        headers=headers,
        data={'accessTokenTTL': 900, 'accessTokenMaxTTL': 900},
    )
    token = http('POST', f'{auth_path}/tokens', headers=headers, data={'name': 'test'})
    return url, headers, key, token


def test_infisical_signs_qemu_and_revokes_access(tmp_path, infisical):
    url, admin, key, token = infisical
    api = f"{url}/api/v1/kms/keys/{key['id']}"
    token_headers = {'Authorization': f"Bearer {token['accessToken']}"}
    signer = InfisicalProvider(SigningService(url, token_headers), key['id'], key['version'])
    public = signer.public_key()
    config = profile_file(
        tmp_path / 'zkeyring.toml',
        backend='infisical',
        url=url,
        key=key['id'],
        version=key['version'],
        fingerprint=key_fingerprint(public),
    )
    environment = {**os.environ, 'INFISICAL_TOKEN': token['accessToken']}
    build_dir = compile_qemu(tmp_path, public)
    signed = tmp_path / 'signed.bin'
    sign(config, build_dir, signed, environment)
    output = boot_output(build_dir / 'mcuboot/zephyr/zephyr.elf', signed)
    assert b'Hello World!' in output, output

    http('GET', f'{api}/private-key', headers=admin, expected=(400, 403))
    next_version = InfisicalProvider(
        SigningService(url, token_headers), key['id'], key['version'] + 1
    )
    with pytest.raises(ZkeyringError, match='version does not match'):
        next_version.public_key()
    http(
        'POST',
        f"{url}/api/v1/auth/token-auth/tokens/{token['tokenData']['id']}/revoke",
        headers=admin,
        data={},
    )
    with pytest.raises(ZkeyringError, match=r'HTTP (401|403)'):
        signer.sign_digest(b'\x00' * 32)
    denied = tmp_path / 'denied.bin'
    assert sign(config, build_dir, denied, environment, check=False).returncode == 1
    assert not denied.exists()
