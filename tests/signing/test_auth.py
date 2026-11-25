# SPDX-License-Identifier: Apache-2.0

import base64
import subprocess
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, utils
from test_signing import IMGTOOL, profile_file, synthetic_build
from zkeyring import ZkeyringError
from zkeyring.backends import encoded, key_fingerprint, login_session, public_pem, signing_service
from zkeyring.cli import main


class MemoryKeyring:
    def __init__(self):
        self.entries = {}

    def get_password(self, service, account):
        return self.entries.get((service, account))

    def set_password(self, service, account, token):
        self.entries[service, account] = token

    def delete_password(self, service, account):
        del self.entries[service, account]


@pytest.mark.parametrize(
    ('backend', 'token_variable', 'algorithm'),
    [
        ('vault-transit', 'VAULT_TOKEN', 'ECDSA_SHA_256'),
        ('openbao', 'BAO_TOKEN', 'ECDSA_SHA_256'),
        ('infisical', 'INFISICAL_TOKEN', 'ECDSA_SHA_256'),
        ('infisical', 'INFISICAL_TOKEN', 'ED25519'),
    ],
)
def test_login_sign_logout(tmp_path, monkeypatch, capsys, backend, token_variable, algorithm):
    key = (
        ed25519.Ed25519PrivateKey.generate()
        if algorithm == 'ED25519'
        else ec.generate_private_key(ec.SECP256R1())
    )
    pem = public_pem(key.public_key()).decode()
    settings = {
        'backend': backend,
        'url': 'https://signing.example',
        'key': 'firmware',
        'version': 1,
        'fingerprint': key_fingerprint(key.public_key()),
    }
    config = profile_file(tmp_path / 'zkeyring.yaml', **settings)
    build = tmp_path / 'build'
    image = synthetic_build(build, key.public_key())
    store = MemoryKeyring()
    monkeypatch.setattr('zkeyring.auth.native_keyrings', lambda: [store])
    monkeypatch.delenv('VAULT_TOKEN', raising=False)
    monkeypatch.delenv('BAO_TOKEN', raising=False)
    monkeypatch.delenv('INFISICAL_TOKEN', raising=False)
    for variable in ('VAULT_ADDR', 'BAO_ADDR', 'INFISICAL_API_URL'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv('VAULT_NAMESPACE', 'firmware-dev')
    monkeypatch.setenv('BAO_NAMESPACE', 'firmware-dev')

    session = login_session(settings, config, 'test')
    token = 'test-issued-access-token'
    real_run = subprocess.run
    vendor_calls = []

    def run_vendor(command, **kwargs):
        if command[0] not in ('vault', 'bao', 'infisical'):
            return real_run(command, **kwargs)
        vendor_calls.append(command)
        env = kwargs['env']
        assert token not in command
        if backend in ('vault-transit', 'openbao'):
            prefix = 'VAULT' if backend == 'vault-transit' else 'BAO'
            assert env[f'{prefix}_ADDR'] == settings['url']
            assert env[f'{prefix}_NAMESPACE'] == 'firmware-dev'
            if command[1] == 'token':
                assert env[f'{prefix}_TOKEN'] == token
        assert kwargs['stdout'] == subprocess.PIPE
        return subprocess.CompletedProcess(
            command, 0, token + '\n' if command[1] == 'login' else ''
        )

    def request(method, url, *, headers, json, **kwargs):
        assert url.startswith(settings['url'] + '/')
        assert kwargs['allow_redirects'] is False
        if backend in ('vault-transit', 'openbao'):
            assert headers == {'X-Vault-Token': token, 'X-Vault-Namespace': 'firmware-dev'}
            if method == 'GET':
                result = {
                    'data': {
                        'type': 'ecdsa-p256',
                        'exportable': False,
                        'allow_plaintext_backup': False,
                        'keys': {'1': {'public_key': pem}},
                    }
                }
            else:
                digest = base64.b64decode(json['input'])
                signature = key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
                result = {'data': {'signature': f'vault:v1:{encoded(signature)}'}}
        else:
            assert headers == {'Authorization': f'Bearer {token}'}
            if url.endswith('/public-key'):
                result = {'publicKey': pem}
            elif method == 'GET':
                result = {
                    'key': {
                        'id': 'firmware',
                        'isExportable': False,
                        'keyUsage': 'sign-verify',
                        'version': 1,
                    }
                }
            else:
                digest = base64.b64decode(json['data'])
                assert json['signingAlgorithm'] == algorithm
                assert json['isDigest'] == (algorithm != 'ED25519')
                if algorithm == 'ED25519':
                    signature = key.sign(digest)
                else:
                    signature = key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
                result = {
                    'keyId': 'firmware',
                    'signingAlgorithm': algorithm,
                    'signature': encoded(signature),
                }
        return SimpleNamespace(status_code=200, json=lambda: result)

    def invoke(action, *args):
        monkeypatch.setattr(
            'sys.argv',
            ['zkeyring', action, '--config', str(config), '--profile', 'test', *map(str, args)],
        )
        return main()

    monkeypatch.setattr('zkeyring.auth.subprocess.run', run_vendor)
    monkeypatch.setattr('zkeyring.backends.requests.request', request)
    options = ['--role', 'developer', '--auth-path', 'sso'] if backend != 'infisical' else []
    assert invoke('login', *options) == 0
    assert token not in capsys.readouterr().out

    with monkeypatch.context() as overrides:
        overrides.setenv(token_variable, 'ci-credential')
        headers = signing_service(settings, config, 'test').headers
        assert any(value.endswith('ci-credential') for value in headers.values())
    with pytest.raises(ZkeyringError, match=token_variable):
        signing_service(settings, config, 'another-profile')
    with pytest.raises(ZkeyringError, match=token_variable):
        signing_service({**settings, 'url': 'https://other.example'}, config, 'test')

    args = ('--build-dir', build, '--imgtool', IMGTOOL)
    assert invoke('sign', *args) == 0
    assert image.with_suffix('.signed.bin').is_file()
    assert invoke('logout') == 0
    assert invoke('logout') == 0
    assert invoke('sign', *args) == 1
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert token_variable in captured.err
    assert not store.entries

    if backend in ('vault-transit', 'openbao'):
        executable = 'vault' if backend == 'vault-transit' else 'bao'
        assert vendor_calls == [
            [executable, 'login', '-method=oidc', '-token-only', '-path=sso', 'role=developer'],
            [executable, 'token', 'revoke', '-self'],
        ]
    else:
        assert len(session.vendor_profile) <= 64
        assert vendor_calls == [
            [
                'infisical',
                'login',
                '--method=user',
                '--plain',
                '--silent',
                f'--domain={session.address}',
                f'--save-as={session.vendor_profile}',
            ],
            [
                'infisical',
                'logout',
                f'--profile={session.vendor_profile}',
                f'--domain={session.address}',
            ],
        ]


@pytest.mark.parametrize(
    ('backend', 'executable'), [('vault-transit', 'vault'), ('openbao', 'bao')]
)
def test_login_settings_from_profile(tmp_path, monkeypatch, backend, executable):
    settings = {
        'backend': backend,
        'url': 'https://signing.example',
        'namespace': 'firmware',
        'role': 'developer',
        'auth_path': 'entra',
        'key': 'firmware',
        'version': 1,
    }
    config = profile_file(tmp_path / 'zkeyring.yaml', **settings)
    store = MemoryKeyring()
    monkeypatch.setattr('zkeyring.auth.native_keyrings', lambda: [store])
    for variable in ('VAULT_ADDR', 'BAO_ADDR', 'VAULT_NAMESPACE', 'BAO_NAMESPACE'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv('VAULT_TOKEN', raising=False)
    monkeypatch.delenv('BAO_TOKEN', raising=False)
    vendor_calls = []

    def run_vendor(command, **kwargs):
        prefix = 'VAULT' if executable == 'vault' else 'BAO'
        assert kwargs['env'][f'{prefix}_NAMESPACE'] == 'firmware'
        vendor_calls.append(command)
        return subprocess.CompletedProcess(command, 0, 'token\n')

    def invoke(*args):
        monkeypatch.setattr(
            'sys.argv',
            ['zkeyring', 'login', '--config', str(config), '--profile', 'test', *args],
        )
        return main()

    monkeypatch.setattr('zkeyring.auth.subprocess.run', run_vendor)
    assert invoke() == 0
    assert invoke('--role', 'release', '--auth-path', 'oidc') == 0
    assert vendor_calls == [
        [executable, 'login', '-method=oidc', '-token-only', '-path=entra', 'role=developer'],
        [executable, 'login', '-method=oidc', '-token-only', '-path=oidc', 'role=release'],
    ]
    headers = signing_service(settings, config, 'test').headers
    assert headers == {'X-Vault-Token': 'token', 'X-Vault-Namespace': 'firmware'}


@pytest.mark.parametrize(
    ('backend', 'prefix'),
    [('vault-transit', 'VAULT'), ('vault-kv', 'VAULT'), ('openbao', 'BAO'), ('infisical', None)],
)
def test_environment_overrides_profile_service(tmp_path, monkeypatch, backend, prefix):
    address = 'INFISICAL_API_URL' if prefix is None else f'{prefix}_ADDR'
    settings = {'backend': backend, 'url': 'https://profile.example'}
    if prefix is not None:
        settings['namespace'] = 'profile'
    config = tmp_path / 'zkeyring.yaml'
    monkeypatch.delenv(address, raising=False)
    if prefix is not None:
        monkeypatch.delenv(f'{prefix}_NAMESPACE', raising=False)

    session = login_session(settings, config, 'test')
    assert (session.address, session.namespace) == (
        'https://profile.example',
        '' if prefix is None else 'profile',
    )

    monkeypatch.setenv(address, 'https://environment.example')
    if prefix is not None:
        monkeypatch.setenv(f'{prefix}_NAMESPACE', 'environment')
    session = login_session(settings, config, 'test')
    assert (session.address, session.namespace) == (
        'https://environment.example',
        '' if prefix is None else 'environment',
    )

    monkeypatch.delenv(address)
    with pytest.raises(ZkeyringError, match=f'set {address} or the profile url'):
        login_session({'backend': backend}, config, 'test')


@pytest.mark.parametrize('field', ['namespace', 'role', 'auth_path'])
def test_infisical_rejects_vault_login_settings(tmp_path, field):
    settings = {'backend': 'infisical', 'url': 'https://signing.example', field: 'value'}
    with pytest.raises(ZkeyringError, match='apply to Vault and OpenBao'):
        login_session(settings, tmp_path / 'zkeyring.yaml', 'test')


@pytest.fixture
def vault_session(tmp_path, monkeypatch):
    """A Vault Transit profile with an empty keyring and a fake vault CLI."""
    settings = {
        'backend': 'vault-transit',
        'url': 'https://signing.example',
        'role': 'developer',
        'key': 'firmware',
        'version': 1,
    }
    config = profile_file(tmp_path / 'zkeyring.yaml', **settings)
    store = MemoryKeyring()
    monkeypatch.setattr('zkeyring.auth.native_keyrings', lambda: [store])
    for variable in ('VAULT_TOKEN', 'VAULT_ADDR', 'VAULT_NAMESPACE', 'CI'):
        monkeypatch.delenv(variable, raising=False)
    logins = []

    def run_vendor(command, **kwargs):
        logins.append(command)
        return subprocess.CompletedProcess(command, 0, f'token-{len(logins)}\n')

    monkeypatch.setattr('zkeyring.auth.subprocess.run', run_vendor)
    return SimpleNamespace(settings=settings, config=config, store=store, logins=logins)


def test_interactive_build_logs_in_automatically(vault_session, monkeypatch, capsys):
    monkeypatch.setattr('zkeyring.auth.interactive', lambda: True)
    service = signing_service(vault_session.settings, vault_session.config, 'test')
    assert service.headers == {'X-Vault-Token': 'token-1'}
    assert vault_session.logins == [
        ['vault', 'login', '-method=oidc', '-token-only', 'role=developer']
    ]
    assert list(vault_session.store.entries.values()) == ['token-1']
    assert 'logging in to https://signing.example' in capsys.readouterr().err


@pytest.mark.parametrize('ci', ['true', None])
def test_ci_never_logs_in(vault_session, monkeypatch, ci):
    if ci is None:
        # No CI variable, but no terminal either.
        monkeypatch.setattr('sys.stdin', SimpleNamespace(isatty=lambda: False))
    else:
        monkeypatch.setenv('CI', ci)
        monkeypatch.setattr('sys.stdin', SimpleNamespace(isatty=lambda: True))
    with pytest.raises(
        ZkeyringError, match='run zkeyring login for this profile or set VAULT_TOKEN'
    ):
        signing_service(vault_session.settings, vault_session.config, 'test')
    assert vault_session.logins == []


@pytest.mark.parametrize('from_environment', [False, True])
def test_refused_session_is_renewed_once(vault_session, monkeypatch, from_environment):
    monkeypatch.setattr('zkeyring.auth.interactive', lambda: True)
    if from_environment:
        monkeypatch.setenv('VAULT_TOKEN', 'ci-credential')
    else:
        session = login_session(vault_session.settings, vault_session.config, 'test')
        vault_session.store.set_password('zkeyring-session', session.account, 'expired')
    sent = []

    def request(method, url, *, headers, **kwargs):
        sent.append(headers['X-Vault-Token'])
        status = 200 if headers['X-Vault-Token'] == 'token-1' else 403
        return SimpleNamespace(status_code=status, json=lambda: {'data': {}})

    monkeypatch.setattr('zkeyring.backends.requests.request', request)
    service = signing_service(vault_session.settings, vault_session.config, 'test')
    if from_environment:
        # A token from the environment belongs to the caller: never replace it.
        with pytest.raises(ZkeyringError, match='HTTP 403'):
            service.request('/v1/transit/keys/firmware')
        assert sent == ['ci-credential']
        assert vault_session.logins == []
    else:
        assert service.request('/v1/transit/keys/firmware') == {'data': {}}
        assert sent == ['expired', 'token-1']
        assert len(vault_session.logins) == 1
        # A second refusal is reported instead of opening another login.
        service.headers = {'X-Vault-Token': 'revoked'}
        with pytest.raises(ZkeyringError, match='HTTP 403'):
            service.request('/v1/transit/keys/firmware')
        assert len(vault_session.logins) == 1
