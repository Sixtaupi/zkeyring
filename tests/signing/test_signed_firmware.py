# SPDX-License-Identifier: Apache-2.0

import os
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from test_signing import IMGTOOL, ROOT, boot_output, profile_file, provision_transit, run
from zkeyring.backends import (
    SigningService,
    VaultTransitProvider,
    encoded,
    key_fingerprint,
    public_pem,
)

SAMPLE = ROOT / 'samples/signed_firmware'


def test_signed_firmware_with_vault(tmp_path):
    backend, _, url, _, token, mount = provision_transit('vault-transit')
    signer = VaultTransitProvider(
        SigningService(url, {'X-Vault-Token': token}), mount, 'firmware', 1
    )
    signing_key = signer.public_key()
    signing_public = tmp_path / 'signing-public.pem'
    signing_public.write_bytes(public_pem(signing_key))
    config = profile_file(
        tmp_path / 'signing profiles.toml',
        backend=backend,
        url=url,
        mount=mount,
        key='firmware',
        version=1,
        fingerprint=key_fingerprint(signing_key),
    )

    sign_env = {**os.environ, 'VAULT_TOKEN': token}
    build_env = os.environ.copy()
    build_env.pop('VAULT_TOKEN', None)

    application_config = tmp_path / 'app-config'
    application_config.mkdir()
    (application_config / 'prj.conf').write_text(
        (SAMPLE / 'prj.conf').read_text(encoding='utf-8')
        + f'CONFIG_ZSIGNING_PROFILE_FILE="{os.path.relpath(config, SAMPLE)}"\n',
        encoding='utf-8',
    )
    build_dir = tmp_path / 'build'
    run(
        'west',
        'build',
        '-b',
        'qemu_cortex_m0',
        '--sysbuild',
        SAMPLE,
        '-d',
        build_dir,
        '--',
        f'-DSB_CONFIG_BOOT_SIGNATURE_KEY_FILE="{signing_public}"',
        f'-Dsigned_firmware_APPLICATION_CONFIG_DIR={application_config}',
        '-DZKEYRING_PROFILE=test',
        '-Dsigned_firmware_CONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION="1.0.0"',
        env=sign_env,
        timeout=300,
    )

    image_build = build_dir / 'signed_firmware'
    cache = (image_build / 'CMakeCache.txt').read_text(encoding='utf-8')
    assert f'APPLICATION_CONFIG_DIR:PATH={application_config}' in cache
    image_dir = image_build / 'zephyr'
    signed = image_dir / 'zephyr.signed.bin'
    assert signed.is_file()
    assert 'bin_file: zephyr.signed.bin' in (image_dir / 'runners.yaml').read_text()
    output = boot_output(build_dir / 'mcuboot/zephyr/zephyr.elf', signed)
    assert b'zkeyring demo firmware 1.0.0' in output, output

    rebuild = ('west', 'build', '--sysbuild', '-d', build_dir)
    timestamp = signed.stat().st_mtime_ns
    run(*rebuild, env=build_env, timeout=300)
    assert signed.stat().st_mtime_ns == timestamp

    signed.unlink()
    result = run(*rebuild, env=build_env, check=False, timeout=300)
    assert result.returncode != 0
    assert b'VAULT_TOKEN' in result.stdout + result.stderr
    assert not signed.exists()
    run(*rebuild, env=sign_env, timeout=300)
    assert signed.is_file()

    override = config.rename(tmp_path / 'ci profiles.toml')
    override_path = os.path.relpath(override, SAMPLE)
    signed.unlink()
    run(*rebuild, '--', f'-DZKEYRING_CONFIG={override_path}', env=sign_env, timeout=300)
    assert signed.is_file()


def test_build_signs_with_ephemeral_key(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = tmp_path / 'public.pem'
    public.write_bytes(public_pem(key.public_key()))
    config = profile_file(
        tmp_path / 'zkeyring.toml',
        backend='environment',
        variable='TEST_FIRMWARE_PEM',
        encoding='base64',
        fingerprint=key_fingerprint(key.public_key()),
    )
    release = tmp_path / 'release.conf'
    release.write_text('CONFIG_ZSIGNING_PROFILE="test"\n', encoding='utf-8')
    env = {**os.environ, 'TEST_FIRMWARE_PEM': encoded(pem)}
    build_dir = tmp_path / 'build'
    run(
        'west',
        'build',
        '-b',
        'qemu_cortex_m0',
        '--sysbuild',
        SAMPLE,
        '-d',
        build_dir,
        '--',
        f'-DSB_CONFIG_BOOT_SIGNATURE_KEY_FILE="{public.as_posix()}"',
        f'-DZKEYRING_CONFIG={config.as_posix()}',
        f'-DEXTRA_CONF_FILE={release.as_posix()}',
        env=env,
        timeout=600,
    )
    signed = build_dir / 'signed_firmware/zephyr/zephyr.signed.bin'
    run(sys.executable, IMGTOOL, 'verify', '-k', public, signed)

    override = '-DZKEYRING_PROFILE=missing'
    result = run(
        *('west', 'build', '--sysbuild', '-d', build_dir, '--', override),
        env=env,
        check=False,
        timeout=600,
    )
    assert result.returncode != 0
    assert b"unknown signing profile 'missing'" in result.stdout + result.stderr
