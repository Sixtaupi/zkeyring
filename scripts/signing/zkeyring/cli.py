# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Romain Pelletant

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from cryptography.exceptions import InvalidSignature
from west.manifest import MalformedManifest, Manifest
from west.util import WestNotFound, west_topdir

from zkeyring import ZkeyringError, error_causes
from zkeyring.backends import (
    encoded,
    get_signer,
    key_fingerprint,
    load_public_key,
    login_session,
    public_pem,
    required_text,
    signature_type,
    verify_signature,
)

METADATA_FILE = 'zkeyring-sign.json'
PUBLIC_KEY_FILE = 'zkeyring-public.pem'
ALIGNMENTS = {1, 2, 4, 8, 16, 32, 64, 128}


def metadata_path(build_dir: Path) -> Path:
    path = build_dir / METADATA_FILE
    if path.is_file():
        return path
    candidates = list(build_dir.glob(f'*/{METADATA_FILE}'))
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise ZkeyringError(
            'select a build directory with exactly one zkeyring-enabled application'
        )
    raise ZkeyringError('no zkeyring signing metadata found in the build directory')


def read_metadata(build_dir: Path) -> tuple[Path, list[str], Path]:
    path = metadata_path(build_dir)
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema') != 1:
        raise ZkeyringError('unsupported build metadata schema')
    image = data.get('image')
    version = data.get('version')
    header_size = data.get('header_size')
    slot_size = data.get('slot_size')
    align = data.get('align')
    overwrite_only = data.get('overwrite_only')
    if (
        not isinstance(image, str)
        or not re.fullmatch(r'[\w-]+\.bin', image)
        or not isinstance(version, str)
        or not re.fullmatch(r'\d+\.\d+\.\d+(\+\d+)?', version)
        or type(slot_size) is not int
        or type(header_size) is not int
        or not 32 <= header_size < slot_size <= 0xFFFFFFFF
        or type(align) is not int
        or align not in ALIGNMENTS
        or type(overwrite_only) is not bool
    ):
        raise ZkeyringError('invalid image parameters in build metadata')
    options = [
        '--version',
        version,
        '--header-size',
        str(header_size),
        '--slot-size',
        str(slot_size),
        '--align',
        str(align),
        '--sha',
        '256',
    ]
    if overwrite_only:
        options.append('--overwrite-only')
    return path.parent / 'zephyr' / image, options, path.parent / PUBLIC_KEY_FILE


def resolve_imgtool(path: Path | None) -> Path:
    if path is None:
        try:
            manifest = Manifest.from_topdir(west_topdir())
            project = manifest.get_projects(['mcuboot'])[0]
            path = Path(project.abspath) / 'scripts' / 'imgtool.py'
        except (WestNotFound, ValueError, MalformedManifest, OSError) as exc:
            raise ZkeyringError('could not find MCUboot in west; use --imgtool') from exc
    if not path.is_file():
        raise ZkeyringError(f'imgtool does not exist: {path}')
    return path.resolve()


def imgtool_environment(profile: dict) -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        'VAULT_TOKEN',
        'BAO_TOKEN',
        'INFISICAL_TOKEN',
        'SOPS_AGE_KEY',
        'SOPS_AGE_KEY_FILE',
    ):
        env.pop(name, None)
    for field in ('variable', 'pin_variable'):
        name = profile.get(field)
        if isinstance(name, str):
            env.pop(name, None)
    return env


def run_imgtool(imgtool: Path, command: str, *args: str | Path, env: dict[str, str]) -> None:
    try:
        result = subprocess.run(
            [sys.executable, imgtool, command, *args],
            env=env,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ZkeyringError(f'could not run imgtool {command}') from exc
    if result.returncode:
        message = result.stderr.decode(errors='replace').strip()
        if not message:
            message = result.stdout.decode(errors='replace').strip()
        raise ZkeyringError(f'imgtool {command} failed: {message}')


def signing_fingerprint(profile: dict) -> str:
    fingerprint = profile.get('fingerprint')
    if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
        raise ZkeyringError('profile requires a lowercase SHA-256 public key fingerprint')
    return fingerprint


def sign(args: argparse.Namespace, profile: dict) -> None:
    image, options, build_key_file = read_metadata(args.build_dir)
    output = args.output or image.with_suffix('.signed.bin')
    if output.suffix != '.bin' or output.resolve() == image.resolve():
        raise ZkeyringError('output must be a separate .bin file')
    fingerprint = signing_fingerprint(profile)
    build_key = load_public_key(build_key_file.read_bytes())
    if key_fingerprint(build_key) != fingerprint:
        raise ZkeyringError('build public key does not match the profile fingerprint')
    imgtool = resolve_imgtool(args.imgtool)
    signer = get_signer(profile, args.config, args.profile)
    public_key = signer.public_key()
    if key_fingerprint(public_key) != fingerprint:
        raise ZkeyringError('backend public key does not match the profile fingerprint')

    env = imgtool_environment(profile)
    with TemporaryDirectory(prefix='.zkeyring-', dir=output.parent) as staging:
        work = Path(staging)
        unsigned = work / 'unsigned.bin'
        shutil.copyfile(image, unsigned)
        digest_file = work / 'digest'
        run_imgtool(
            imgtool, 'sign', *options, '--vector-to-sign', 'digest', unsigned, digest_file, env=env
        )
        digest = digest_file.read_bytes()
        if len(digest) != 32:
            raise ZkeyringError('imgtool did not produce a SHA-256 digest')
        signature = signer.sign_digest(digest)
        try:
            verify_signature(public_key, signature, digest)
        except InvalidSignature as exc:
            raise ZkeyringError(
                'backend signature does not match the digest and public key'
            ) from exc
        sig_file = work / 'signature'
        sig_file.write_text(encoded(signature), encoding='ascii')
        public_file = work / 'public.pem'
        public_file.write_bytes(public_pem(public_key))
        signed = work / 'signed.bin'
        run_imgtool(
            imgtool,
            'sign',
            *options,
            '--fix-sig',
            sig_file,
            '--fix-sig-pubkey',
            public_file,
            unsigned,
            signed,
            env=env,
        )
        run_imgtool(imgtool, 'verify', '-k', public_file, signed, env=env)
        os.replace(signed, output)
    print(output)


def add_commands(parser: argparse.ArgumentParser) -> None:
    diagnostics_help = 'show details about the causes of errors'
    parser.add_argument('--verbose', action='store_true', help=diagnostics_help)
    commands = parser.add_subparsers(dest='action', required=True)
    signing = commands.add_parser('sign', help='sign a zkeyring-enabled build')
    signing.add_argument('--build-dir', type=Path, required=True)
    signing.add_argument('--output', type=Path)
    signing.add_argument('--imgtool', type=Path, help='MCUboot scripts/imgtool.py for this build')
    signing.add_argument('--show-backend', action='store_true', help='log the signing backend')
    export = commands.add_parser('public-key', help='write the selected public PEM to stdout')
    profile_key = commands.add_parser('profile-key', help='read the verified local public key')
    login = commands.add_parser('login', help='open a local Vault, OpenBao or Infisical session')
    login.add_argument('--role', help='Vault or OpenBao OIDC role')
    login.add_argument('--auth-path', help='Vault or OpenBao OIDC auth mount path')
    logout = commands.add_parser('logout', help='close a local Vault, OpenBao or Infisical session')
    for command in (signing, export, profile_key, login, logout):
        command.add_argument('--config', type=Path, help='profile file (default: zkeyring.yaml)')
        command.add_argument(
            '--profile', default='default', help='signing profile (default: default)'
        )
    fingerprint = commands.add_parser('fingerprint', help='print the SHA-256 fingerprint of a PEM')
    fingerprint.add_argument('pem', type=Path)
    key_type = commands.add_parser('key-type', help='print the MCUboot signing key type')
    key_type.add_argument('pem', type=Path)
    for command in (signing, export, profile_key, login, logout, fingerprint, key_type):
        command.add_argument(
            '--verbose', action='store_true', default=argparse.SUPPRESS, help=diagnostics_help
        )


def cause_detail(cause: BaseException) -> str:
    # Exception messages from libraries can contain request URLs or input data.
    kind = type(cause).__name__
    if isinstance(cause, OSError) and isinstance(cause.errno, int):
        return f'{kind} (errno {cause.errno})'
    if isinstance(cause, subprocess.TimeoutExpired):
        return f'{kind} (after {cause.timeout} seconds)'
    if isinstance(cause, yaml.MarkedYAMLError) and cause.problem_mark is not None:
        mark = cause.problem_mark
        return f'{kind} (line {mark.line + 1}, column {mark.column + 1})'
    return kind


def run(args: argparse.Namespace) -> int:
    try:
        if args.action == 'fingerprint':
            print(key_fingerprint(load_public_key(args.pem.read_bytes())))
            return 0
        if args.action == 'key-type':
            print(signature_type(load_public_key(args.pem.read_bytes())))
            return 0
        if args.config is None:
            args.config = next(
                (path for path in (Path('zkeyring.yaml'), Path('zkeyring.yml')) if path.is_file()),
                Path('zkeyring.yaml'),
            )
        try:
            with args.config.open(encoding='utf-8') as config:
                document = yaml.safe_load(config)
        except yaml.YAMLError as exc:
            raise ZkeyringError(f'invalid YAML profile file: {args.config}') from exc
        profiles = document.get('profiles', {}) if isinstance(document, dict) else {}
        if not isinstance(profiles, dict) or not isinstance(profiles.get(args.profile), dict):
            raise ZkeyringError(f'unknown signing profile {args.profile!r}')
        profile = profiles[args.profile]
        if args.action in ('login', 'logout'):
            session = login_session(profile, args.config, args.profile)
            if args.action == 'login':
                session.login(args.role, args.auth_path)
                print(f'Logged in for profile {args.profile!r}.')
            elif session.logout():
                print(f'Logged out of profile {args.profile!r}.')
            else:
                print(f'No local session for profile {args.profile!r}.')
        elif args.action == 'profile-key':
            if 'public_key' in profile:
                key_file = (args.config.parent / required_text(profile, 'public_key')).absolute()
                pem = key_file.read_bytes()
                if not pem.startswith(
                    (b'-----BEGIN PUBLIC KEY-----', b'-----BEGIN RSA PUBLIC KEY-----')
                ):
                    raise ZkeyringError('profile public_key must be a public PEM')
                key = load_public_key(pem)
                if key_fingerprint(key) != signing_fingerprint(profile):
                    raise ZkeyringError('profile public key does not match the profile fingerprint')
                print(
                    json.dumps(
                        {
                            'path': str(key_file),
                            'pem': public_pem(key).decode('ascii'),
                            'signature_type': signature_type(key),
                        }
                    )
                )
        elif args.action == 'public-key':
            signer = get_signer(profile, args.config, args.profile)
            public_key = signer.public_key()
            if 'fingerprint' in profile and key_fingerprint(public_key) != profile['fingerprint']:
                raise ZkeyringError('backend public key does not match the profile fingerprint')
            sys.stdout.buffer.write(public_pem(public_key))
        else:
            if args.show_backend:
                backend = required_text(profile, 'backend')
                print(f'zkeyring: signing backend = {backend!r}', file=sys.stderr)
            sign(args, profile)
    except (ZkeyringError, OSError, ValueError) as exc:
        print(f'zkeyring: {exc}', file=sys.stderr)
        if args.verbose:
            for cause in error_causes(exc):
                print(f'zkeyring: caused by {cause_detail(cause)}', file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Sign Zephyr MCUboot firmware')
    add_commands(parser)
    return run(parser.parse_args())


if __name__ == '__main__':
    sys.exit(main())
