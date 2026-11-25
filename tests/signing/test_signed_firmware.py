# SPDX-License-Identifier: Apache-2.0

import os
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from test_signing import IMGTOOL, ROOT, boot_output, profile_file, provision_transit, run
from zkeyring.auth import SESSION_SERVICE, native_keyrings
from zkeyring.backends import (
    SigningService,
    VaultTransitProvider,
    encoded,
    key_fingerprint,
    login_session,
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
        tmp_path / 'signing profiles.yaml',
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
        + f'\nCONFIG_ZSIGNING_PROFILE_FILE="{os.path.relpath(config, SAMPLE)}"\n',
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
    if os.environ.get('ZKEYRING_TEST_NATIVE_KEYRING'):
        session = login_session({'backend': backend, 'url': url}, config, 'test')
        store = native_keyrings()[0]
        store.set_password(SESSION_SERVICE, session.account, token)
        try:
            run(*rebuild, env=build_env, timeout=300)
        finally:
            store.delete_password(SESSION_SERVICE, session.account)
    else:
        run(*rebuild, env=sign_env, timeout=300)
    assert signed.is_file()

    override = config.rename(tmp_path / 'ci profiles.yaml')
    override_path = os.path.relpath(override, SAMPLE)
    signed.unlink()
    run(*rebuild, '--', f'-DZKEYRING_CONFIG={override_path}', env=sign_env, timeout=300)
    assert signed.is_file()


@pytest.mark.parametrize('algorithm', ['ecdsa-p256', 'rsa-2048', 'rsa-3072', 'ed25519'])
def test_build_signs_with_ephemeral_key(tmp_path, algorithm):
    board = 'qemu_cortex_m0'
    address = 0x8000
    if algorithm.startswith('rsa-'):
        # Mbed TLS RSA requires more RAM than QEMU's Cortex-M0 board has.
        board = 'nrf52840dk/nrf52840'
        size = int(algorithm.removeprefix('rsa-'))
        key = rsa.generate_private_key(public_exponent=65537, key_size=size)
        signature_options = (f'-DSB_CONFIG_ZKEYRING_SIGNATURE_RSA_{size}=y',)
    elif algorithm == 'ed25519':
        key = ed25519.Ed25519PrivateKey.generate()
        # The Ed25519 bootloader needs a larger flash partition on this board.
        address = 0x10000
        layout = tmp_path / 'partitions.overlay'
        layout.write_text(
            '''/delete-node/ &slot0_partition;
/delete-node/ &slot1_partition;
&boot_partition { reg = <0x0 0x10000>; };
&flash0 {
    partitions {
        slot0_partition: partition@10000 {
            compatible = "zephyr,mapped-partition";
            label = "image-0";
            reg = <0x10000 0x16000>;
        };
        slot1_partition: partition@26000 {
            compatible = "zephyr,mapped-partition";
            label = "image-1";
            reg = <0x26000 0x16000>;
        };
    };
};
''',
            encoding='utf-8',
        )
        signature_options = (
            '-DSB_CONFIG_ZKEYRING_SIGNATURE_ED25519=y',
            f'-Dmcuboot_EXTRA_DTC_OVERLAY_FILE={layout}',
            f'-Dsigned_firmware_EXTRA_DTC_OVERLAY_FILE={layout}',
        )
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        signature_options = ()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = tmp_path / 'public.pem'
    public.write_bytes(public_pem(key.public_key()))
    config = profile_file(
        tmp_path / 'zkeyring.yaml',
        backend='environment',
        variable='TEST_FIRMWARE_PEM',
        encoding='base64',
        public_key=public.name,
        fingerprint=key_fingerprint(key.public_key()),
    )
    release = tmp_path / 'release.conf'
    release.write_text('CONFIG_ZSIGNING_PROFILE="test"\n', encoding='utf-8')
    env = {**os.environ, 'TEST_FIRMWARE_PEM': encoded(pem)}
    build_dir = tmp_path / 'build'
    result = run(
        'west',
        'build',
        '-b',
        board,
        '--sysbuild',
        SAMPLE,
        '-d',
        build_dir,
        '--',
        f'-DZKEYRING_CONFIG={config.as_posix()}',
        f'-DEXTRA_CONF_FILE={release.as_posix()}',
        *signature_options,
        env=env,
        timeout=600,
    )
    assert (result.stdout + result.stderr).count(b"zkeyring: signing backend = 'environment'") == 1
    signed = build_dir / 'signed_firmware/zephyr/zephyr.signed.bin'
    run(sys.executable, IMGTOOL, 'verify', '-k', public, signed)
    if board == 'qemu_cortex_m0':
        output = boot_output(build_dir / 'mcuboot/zephyr/zephyr.elf', signed, address)
        assert b'zkeyring demo firmware' in output, output

    if algorithm != 'ecdsa-p256':
        return

    public.write_bytes(public_pem(ec.generate_private_key(ec.SECP256R1()).public_key()))
    rebuild = ('west', 'build', '--sysbuild', '-d', build_dir)
    result = run(*rebuild, env=env, check=False, timeout=600)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert b'profile public key does not match the profile fingerprint' in output
    public.write_bytes(public_pem(key.public_key()))

    override = '-DZKEYRING_PROFILE=missing'
    result = run(
        *rebuild,
        '--',
        override,
        env=env,
        check=False,
        timeout=600,
    )
    assert result.returncode != 0
    assert b"unknown signing profile 'missing'" in result.stdout + result.stderr
