# SPDX-License-Identifier: Apache-2.0

import os
import secrets
import subprocess
import sys
from uuid import uuid4

import keyring
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from test_signing import IMGTOOL, cli, http, profile_file, run, sign, synthetic_build
from zkeyring import ZkeyringError
from zkeyring.backends import LocalKeyringProvider, encoded, key_fingerprint, load_public_key
from zkeyring.cli import imgtool_environment


@pytest.fixture
def key():
    return ec.generate_private_key(ec.SECP256R1())


def private_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def sign_and_verify(tmp_path, public, env=None, **profile):
    build_dir = tmp_path / 'build'
    synthetic_build(build_dir, public)
    config = profile_file(
        tmp_path / 'zkeyring.toml', fingerprint=key_fingerprint(public), **profile
    )
    output = tmp_path / 'signed.bin'
    result = sign(config, build_dir, output, env)
    assert b'PRIVATE KEY' not in result.stdout + result.stderr
    run(sys.executable, IMGTOOL, 'verify', '-k', build_dir / 'zkeyring-public.pem', output)
    return config, build_dir


@pytest.mark.parametrize('encoding', ['pem', 'base64'])
def test_environment_provider(tmp_path, key, encoding):
    pem = private_pem(key)
    env = {
        **os.environ,
        'TEST_FIRMWARE_PEM': encoded(pem) if encoding == 'base64' else pem.decode(),
    }
    config, build_dir = sign_and_verify(
        tmp_path,
        key.public_key(),
        env,
        backend='environment',
        variable='TEST_FIRMWARE_PEM',
        encoding=encoding,
    )
    denied = tmp_path / 'denied.bin'
    assert sign(config, build_dir, denied, check=False).returncode == 1
    assert not denied.exists()


def test_imgtool_does_not_receive_credentials(monkeypatch):
    monkeypatch.setenv('TEST_FIRMWARE_PEM', 'canary-private-key')
    monkeypatch.setenv('TEST_TOKEN_PIN', 'canary-pin')
    monkeypatch.setenv('BAO_TOKEN', 'canary-token')
    environment = imgtool_environment(
        {'variable': 'TEST_FIRMWARE_PEM', 'pin_variable': 'TEST_TOKEN_PIN'}
    )
    assert not {'TEST_FIRMWARE_PEM', 'TEST_TOKEN_PIN', 'BAO_TOKEN'} & environment.keys()
    assert os.environ['TEST_FIRMWARE_PEM'] == 'canary-private-key'


def test_west_command(tmp_path, key):
    build_dir = tmp_path / 'build'
    synthetic_build(build_dir, key.public_key())
    public = build_dir / 'zkeyring-public.pem'
    fingerprint = run('west', 'zkeyring', 'fingerprint', public).stdout.decode().strip()
    assert fingerprint == key_fingerprint(key.public_key())
    config = profile_file(
        tmp_path / 'zkeyring.toml',
        backend='environment',
        variable='TEST_FIRMWARE_PEM',
        fingerprint=fingerprint,
    )
    profile = ('--config', config, '--profile', 'test')
    env = {**os.environ, 'TEST_FIRMWARE_PEM': private_pem(key).decode()}
    exported = run('west', 'zkeyring', 'public-key', *profile, env=env).stdout
    assert exported == public.read_bytes()
    command = ('west', 'zkeyring', 'sign', *profile, '--build-dir', build_dir, '--output')
    output = tmp_path / 'signed.bin'
    run(*command, output, env=env)
    run(sys.executable, IMGTOOL, 'verify', '-k', public, output)

    denied = tmp_path / 'denied.bin'
    result = run(*command, denied, check=False)
    assert result.returncode == 1
    assert b'set TEST_FIRMWARE_PEM' in result.stderr
    assert not denied.exists()


def test_previous_build_metadata(tmp_path, key):
    build_dir = tmp_path / 'build'
    synthetic_build(build_dir, key.public_key())
    (build_dir / 'zkeyring-sign.json').rename(build_dir / 'zkeys-sign.json')
    (build_dir / 'zkeyring-public.pem').rename(build_dir / 'zkeys-public.pem')
    config = profile_file(
        tmp_path / 'zkeys.toml',
        backend='environment',
        variable='TEST_FIRMWARE_PEM',
        fingerprint=key_fingerprint(key.public_key()),
    )
    env = {**os.environ, 'TEST_FIRMWARE_PEM': private_pem(key).decode()}
    result = subprocess.run(
        [sys.executable, '-m', 'zkeyring.cli', 'public-key', '--profile', 'test'],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert load_public_key(result.stdout).public_numbers() == key.public_key().public_numbers()
    output = tmp_path / 'signed.bin'
    sign(config, build_dir, output, env)
    run(sys.executable, IMGTOOL, 'verify', '-k', build_dir / 'zkeys-public.pem', output)


def test_gitlab_secret_file(tmp_path, key):
    secret = tmp_path / 'runner-secret'
    secret.write_bytes(private_pem(key))
    env = {**os.environ, 'GITLAB_TEST_SIGNING_KEY': str(secret)}
    config, build_dir = sign_and_verify(
        tmp_path,
        key.public_key(),
        env,
        backend='gitlab-secrets',
        variable='GITLAB_TEST_SIGNING_KEY',
    )
    assert secret.read_bytes() == private_pem(key)
    secret.unlink()
    denied = tmp_path / 'denied.bin'
    result = sign(config, build_dir, denied, env, check=False)
    assert b'must point to an absolute file' in result.stderr
    assert not denied.exists()


def test_local_keyring_rejects_unrecognized_backend(monkeypatch):
    monkeypatch.setattr(keyring, 'get_keyring', object)
    with pytest.raises(ZkeyringError, match='no supported OS credential store'):
        LocalKeyringProvider('firmware', 'signing')


@pytest.mark.skipif(
    not os.environ.get('ZKEYRING_TEST_NATIVE_KEYRING'), reason='needs an isolated OS keyring'
)
def test_local_keyring_signs(tmp_path, key):
    service = f'zkeyring-test-{uuid4().hex}'
    keyring.set_password(service, 'firmware', private_pem(key).decode())
    try:
        config, build_dir = sign_and_verify(
            tmp_path, key.public_key(), backend='local-keyring', service=service, key='firmware'
        )
    finally:
        keyring.delete_password(service, 'firmware')
    denied = tmp_path / 'denied.bin'
    assert sign(config, build_dir, denied, check=False).returncode == 1
    assert not denied.exists()


def test_vault_kv_reads_pinned_version(tmp_path, key):
    url = os.environ.get('ZKEYRING_TEST_VAULT_URL')
    if not url:
        pytest.skip('needs an ephemeral Vault')
    admin = {'X-Vault-Token': os.environ['ZKEYRING_TEST_VAULT_TOKEN']}
    mount = f'zkeyring-kv-{uuid4().hex}'
    path = 'releases/test.key'
    http(
        'POST',
        f'{url}/v1/sys/mounts/{mount}',
        headers=admin,
        data={'type': 'kv', 'options': {'version': '2'}},
        expected=(204,),
    )
    for version_key in (key, ec.generate_private_key(ec.SECP256R1())):
        http(
            'POST',
            f'{url}/v1/{mount}/data/{path}',
            headers=admin,
            data={'data': {'pem': private_pem(version_key).decode()}},
        )
    policy = f'path "{mount}/data/{path}" {{ capabilities = ["read"] }}'
    http(
        'PUT',
        f'{url}/v1/sys/policies/acl/{mount}',
        headers=admin,
        data={'policy': policy},
        expected=(204,),
    )
    token = http(
        'POST',
        f'{url}/v1/auth/token/create',
        headers=admin,
        data={'policies': [mount], 'no_default_policy': True, 'ttl': '15m'},
    )['auth']['client_token']
    env = {**os.environ, 'VAULT_TOKEN': token}
    config, build_dir = sign_and_verify(
        tmp_path,
        key.public_key(),
        env,
        backend='vault-kv',
        url=url,
        mount=mount,
        path=path,
        field='pem',
        version=1,
    )
    http(
        'POST', f'{url}/v1/auth/token/revoke', headers=admin, data={'token': token}, expected=(204,)
    )
    denied = tmp_path / 'denied.bin'
    result = sign(config, build_dir, denied, env, check=False)
    assert b'HTTP 403' in result.stderr
    assert not denied.exists()


def test_pkcs11_signs_with_non_extractable_key(tmp_path, monkeypatch):
    module = os.environ.get('ZKEYRING_TEST_PKCS11_MODULE')
    if not module:
        pytest.skip('needs a SoftHSM module')

    import pkcs11
    from pkcs11.util.ec import encode_named_curve_parameters

    hsm_config = tmp_path / 'softhsm2.conf'
    hsm_config.write_text(f'directories.tokendir = {tmp_path / "tokens"}\n')
    (tmp_path / 'tokens').mkdir()
    monkeypatch.setenv('SOFTHSM2_CONF', str(hsm_config))
    pin = str(10000000 + secrets.randbelow(90000000))
    run(
        'softhsm2-util',
        '--init-token',
        '--free',
        '--label',
        'zkeyring-test',
        '--so-pin',
        '76543210',
        '--pin',
        pin,
    )
    token = pkcs11.lib(module).get_token(token_label='zkeyring-test')
    with token.open(user_pin=pin, rw=True) as session:
        params = session.create_domain_parameters(
            pkcs11.KeyType.EC,
            {pkcs11.Attribute.EC_PARAMS: encode_named_curve_parameters('secp256r1')},
            local=True,
        )
        for key_id, exportable in ((b'\x01', False), (b'\x02', True)):
            params.generate_keypair(
                id=key_id,
                store=True,
                private_template={
                    pkcs11.Attribute.SENSITIVE: True,
                    pkcs11.Attribute.EXTRACTABLE: exportable,
                },
            )

    env = {**os.environ, 'TEST_HSM_PIN': pin}
    profile = {
        'backend': 'pkcs11',
        'module': module,
        'token': 'zkeyring-test',
        'key': '01',
        'pin_variable': 'TEST_HSM_PIN',
    }
    config = profile_file(tmp_path / 'bootstrap.toml', **profile)
    public = cli('public-key', '--config', config, '--profile', 'test', env=env).stdout
    sign_and_verify(tmp_path, load_public_key(public), env, **profile)

    profile['key'] = '02'
    profile_file(config, **profile)
    public = cli('public-key', '--config', config, '--profile', 'test', env=env).stdout
    key = load_public_key(public)
    unprotected = tmp_path / 'unprotected'
    synthetic_build(unprotected, key)
    profile_file(config, fingerprint=key_fingerprint(key), **profile)
    denied = tmp_path / 'denied.bin'
    result = sign(config, unprotected, denied, env, check=False)
    assert b'non-extractable key' in result.stderr
    assert not denied.exists()
