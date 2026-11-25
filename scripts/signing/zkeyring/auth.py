# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Romain Pelletant

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import keyring
from keyring.backend import KeyringBackend
from keyring.backends.chainer import ChainerBackend
from keyring.errors import KeyringError

from zkeyring import ZkeyringError

NATIVE_BACKENDS = {
    'keyring.backends.SecretService',
    'keyring.backends.Windows',
    'keyring.backends.kwallet',
    'keyring.backends.libsecret',
    'keyring.backends.macOS',
}
SESSION_SERVICE = 'zkeyring-session'


def interactive() -> bool:
    # A developer runs the build from a terminal; CI systems set CI or have no terminal.
    if os.environ.get('CI', '').lower() not in ('', '0', 'false'):
        return False
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def native_keyrings() -> list[KeyringBackend]:
    backend = keyring.get_keyring()
    available = backend.backends if isinstance(backend, ChainerBackend) else (backend,)
    return [item for item in available if type(item).__module__ in NATIVE_BACKENDS]


class LoginSession:
    def __init__(
        self,
        backend: str,
        address: str,
        namespace: str,
        config: Path,
        profile: str,
        role: str | None = None,
        auth_path: str | None = None,
    ):
        self.backend = backend
        self.address = address
        self.namespace = namespace
        # Defaults from the profile; the login options override them.
        self.role = role
        self.auth_path = auth_path
        identity = json.dumps([backend, address, namespace, str(config.resolve()), profile])
        self.account = hashlib.sha256(identity.encode()).hexdigest()
        # The Infisical CLI limits profile names to 64 characters.
        self.vendor_profile = f'zkeyring-{self.account[:48]}'

    def stored(self) -> tuple[KeyringBackend, str] | None:
        try:
            for store in native_keyrings():
                if token := store.get_password(SESSION_SERVICE, self.account):
                    return store, token
        except (KeyringError, RuntimeError) as exc:
            raise ZkeyringError('could not read the zkeyring session from the OS keyring') from exc
        return None

    def token_variable(self) -> str:
        return {'vault': 'VAULT_TOKEN', 'bao': 'BAO_TOKEN', 'infisical': 'INFISICAL_TOKEN'}[
            self.backend
        ]

    def renewable(self) -> bool:
        """Whether an expired session may be replaced by an automatic login."""
        return not os.environ.get(self.token_variable()) and interactive()

    def headers(self) -> dict[str, str]:
        variable = self.token_variable()
        token = os.environ.get(variable)
        if not token:
            stored = self.stored()
            if stored is not None:
                _, token = stored
            elif interactive():
                token = self.auto_login('no session for this profile')
            else:
                raise ZkeyringError(f'run zkeyring login for this profile or set {variable}')
        return self.token_headers(token)

    def relogin(self) -> dict[str, str]:
        return self.token_headers(self.auto_login('the session was refused'))

    def auto_login(self, reason: str) -> str:
        print(f'zkeyring: {reason}; logging in to {self.address}', file=sys.stderr, flush=True)
        return self.login(None, None)

    def token_headers(self, token: str) -> dict[str, str]:
        if self.backend == 'infisical':
            return {'Authorization': f'Bearer {token}'}
        headers = {'X-Vault-Token': token}
        if self.namespace:
            headers['X-Vault-Namespace'] = self.namespace
        return headers

    def run_cli(self, *args: str, token: str = '', timeout: int = 30) -> str:
        env = os.environ.copy()
        for name in ('VAULT_TOKEN', 'BAO_TOKEN', 'INFISICAL_TOKEN', 'INFISICAL_PROFILE'):
            env.pop(name, None)
        if self.backend in ('vault', 'bao'):
            prefix = 'VAULT' if self.backend == 'vault' else 'BAO'
            env[f'{prefix}_ADDR'] = self.address
            env[f'{prefix}_NAMESPACE'] = self.namespace
            if token:
                env[f'{prefix}_TOKEN'] = token
        try:
            result = subprocess.run(
                [self.backend, *args],
                env=env,
                stdout=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ZkeyringError(f'install the {self.backend} CLI to manage this session') from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ZkeyringError(f'could not run {self.backend} {args[0]}') from exc
        if result.returncode:
            raise ZkeyringError(f'{self.backend} {args[0]} failed with status {result.returncode}')
        return result.stdout.strip()

    def login(self, role: str | None, auth_path: str | None) -> str:
        role = self.role if role is None else role
        auth_path = self.auth_path if auth_path is None else auth_path
        if self.backend == 'infisical' and (role is not None or auth_path is not None):
            raise ZkeyringError('--role and --auth-path apply to Vault and OpenBao profiles')
        try:
            stores = native_keyrings()
        except (KeyringError, RuntimeError) as exc:
            raise ZkeyringError('could not open the OS keyring for the zkeyring session') from exc
        if not stores:
            raise ZkeyringError('no supported OS credential store is available')

        if self.backend in ('vault', 'bao'):
            args = ['login', '-method=oidc', '-token-only']
            if auth_path is not None:
                args.append(f'-path={auth_path}')
            if role is not None:
                args.append(f'role={role}')
        else:
            args = [
                'login',
                '--method=user',
                '--plain',
                '--silent',
                f'--domain={self.address}',
                f'--save-as={self.vendor_profile}',
            ]
        token = self.run_cli(*args, timeout=300)
        if not token or not token.isascii() or not token.isprintable() or ' ' in token:
            raise ZkeyringError(f'{self.backend} did not return an access token')
        try:
            stores[0].set_password(SESSION_SERVICE, self.account, token)
        except (KeyringError, RuntimeError) as exc:
            raise ZkeyringError('could not store the zkeyring session in the OS keyring') from exc
        return token

    def logout(self) -> bool:
        stored = self.stored()
        if stored is None:
            return False
        store, token = stored
        try:
            store.delete_password(SESSION_SERVICE, self.account)
        except (KeyringError, RuntimeError) as exc:
            raise ZkeyringError('could not remove the session from the OS keyring') from exc
        try:
            if self.backend in ('vault', 'bao'):
                self.run_cli('token', 'revoke', '-self', token=token)
            else:
                self.run_cli(
                    'logout', f'--profile={self.vendor_profile}', f'--domain={self.address}'
                )
        except ZkeyringError as exc:
            raise ZkeyringError(f'local session removed, but server logout failed ({exc})') from exc
        return True
