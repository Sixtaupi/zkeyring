# SPDX-License-Identifier: Apache-2.0

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from west.manifest import MalformedManifest, Manifest
from west.util import WestNotFound, west_topdir

from zkeyring import ZkeyringError
from zkeyring.backends import encoded, get_signer, key_fingerprint, load_public_key, public_pem

METADATA_FILE = 'zkeyring-sign.json'
PUBLIC_KEY_FILE = 'zkeyring-public.pem'
LEGACY_METADATA_FILE = 'zkeys-sign.json'
LEGACY_PUBLIC_KEY_FILE = 'zkeys-public.pem'
ALIGNMENTS = {1, 2, 4, 8, 16, 32, 64, 128}


def metadata_path(build_dir: Path) -> Path:
    for name in (METADATA_FILE, LEGACY_METADATA_FILE):
        path = build_dir / name
        if path.is_file():
            return path
        candidates = sorted(build_dir.glob(f'*/{name}'))
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
    key_file = PUBLIC_KEY_FILE if path.name == METADATA_FILE else LEGACY_PUBLIC_KEY_FILE
    return path.parent / 'zephyr' / image, options, path.parent / key_file


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


def sign(args: argparse.Namespace, profile: dict) -> None:
    image, options, build_key_file = read_metadata(args.build_dir)
    output = args.output or image.with_suffix('.signed.bin')
    if output.suffix != '.bin' or output.resolve() == image.resolve():
        raise ZkeyringError('output must be a separate .bin file')
    fingerprint = profile.get('fingerprint')
    if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
        raise ZkeyringError('profile requires a lowercase SHA-256 public key fingerprint')
    build_key = load_public_key(build_key_file.read_bytes())
    if key_fingerprint(build_key) != fingerprint:
        raise ZkeyringError('build public key does not match the profile fingerprint')
    imgtool = resolve_imgtool(args.imgtool)
    signer = get_signer(profile, args.config.parent)
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
            public_key.verify(signature, digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
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
    commands = parser.add_subparsers(dest='action', required=True)
    signing = commands.add_parser('sign', help='sign a zkeyring-enabled build')
    signing.add_argument('--build-dir', type=Path, required=True)
    signing.add_argument('--output', type=Path)
    signing.add_argument('--imgtool', type=Path, help='MCUboot scripts/imgtool.py for this build')
    export = commands.add_parser('public-key', help='write the selected public PEM to stdout')
    for command in (signing, export):
        command.add_argument('--config', type=Path, help='profile file (default: zkeyring.toml)')
        command.add_argument('--profile', required=True)
    fingerprint = commands.add_parser('fingerprint', help='print the SHA-256 fingerprint of a PEM')
    fingerprint.add_argument('pem', type=Path)


def run(args: argparse.Namespace) -> int:
    try:
        if args.action == 'fingerprint':
            print(key_fingerprint(load_public_key(args.pem.read_bytes())))
            return 0
        if args.config is None:
            args.config = Path('zkeyring.toml')
            if not args.config.is_file() and Path('zkeys.toml').is_file():
                args.config = Path('zkeys.toml')
        with args.config.open('rb') as config:
            profiles = tomllib.load(config).get('profiles', {})
        if not isinstance(profiles, dict) or not isinstance(profiles.get(args.profile), dict):
            raise ZkeyringError(f'unknown signing profile {args.profile!r}')
        profile = profiles[args.profile]
        if args.action == 'public-key':
            signer = get_signer(profile, args.config.parent)
            public_key = signer.public_key()
            if 'fingerprint' in profile and key_fingerprint(public_key) != profile['fingerprint']:
                raise ZkeyringError('backend public key does not match the profile fingerprint')
            sys.stdout.buffer.write(public_pem(public_key))
        else:
            sign(args, profile)
    except (ZkeyringError, OSError, ValueError) as exc:
        print(f'zkeyring: {exc}', file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Sign Zephyr MCUboot firmware')
    add_commands(parser)
    return run(parser.parse_args())


if __name__ == '__main__':
    sys.exit(main())
