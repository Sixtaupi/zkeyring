# Signed firmware

This sample signs a MCUboot image with zkeyring and prints its version.

## Choose the backend

You choose a backend by choosing a **profile**. Each profile in
[zkeyring.yaml](zkeyring.yaml) uses one backend:

| Profile | Backend | Where the key is |
| --- | --- | --- |
| `default`, `local` | [`local-keyring`](../../doc/backends/local-keyring.rst) | OS keyring |
| `dev` | [`sops`](../../doc/backends/sops.rst) | Encrypted file in the repository |
| `ci` | [`environment`](../../doc/backends/environment.rst) | CI secret variable |
| `gitlab` | [`gitlab-secrets`](../../doc/backends/gitlab-secrets.rst) | GitLab secret file |
| `kv` | [`vault-kv`](../../doc/backends/vault-kv.rst) | Vault, read by the signing job |
| `vault` | [`vault-transit`](../../doc/backends/vault-transit.rst) | Vault, never leaves it |
| `openbao` | [`openbao`](../../doc/backends/openbao.rst) | OpenBao, never leaves it |
| `infisical` | [`infisical`](../../doc/backends/infisical.rst) | Infisical, never leaves it |
| `hsm` | [`pkcs11`](../../doc/backends/pkcs11.rst) | HSM or smart card, never leaves it |

Follow the backend page to create your key and set its credential, then adapt
the profile. The pinned `default`, `local` and `dev` profiles belong to the
sample's development key; remove their `fingerprint` to use your own.

## Pin your key

Run these commands from the repository root, with `profile` set to your choice.

```sh
profile=vault
work=$(mktemp -d)
west zkeyring public-key --config samples/signed_firmware/zkeyring.yaml \
    --profile "$profile" > "$work/signing-public.pem"
west zkeyring fingerprint "$work/signing-public.pem"
```

Confirm the key with a trusted source, then set `fingerprint` in the profile to
the printed value and set `public_key` to the path of the exported PEM. You can
use an absolute path or a path relative to `zkeyring.yaml`.

## Build and boot

The sample enables `SB_CONFIG_ZKEYRING_PROFILE_PUBLIC_KEY`. Select the profile
in `prj.conf` with `CONFIG_ZSIGNING_PROFILE`, or with `ZKEYRING_PROFILE` on the
command line. The profile supplies both the signing backend and the PEM that
MCUboot uses to verify the signature.

Use `west build -d <build-directory> -t sysbuild_menuconfig` to select another
signature algorithm in the zkeyring module menu. The selected profile must use
a key of that type.

```sh
west build -b qemu_cortex_m0 --sysbuild \
    samples/signed_firmware -d "$work/build" -- \
    "-DZKEYRING_PROFILE=$profile" \
    '-Dsigned_firmware_CONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION="1.0.0"'

qemu-system-arm -machine microbit -nographic \
    -kernel "$work/build/mcuboot/zephyr/zephyr.elf" \
    -device "loader,file=$work/build/signed_firmware/zephyr/zephyr.signed.bin,addr=0x8000,force-raw=on"
```

The firmware prints `zkeyring demo firmware 1.0.0`. Press Ctrl-A then X to exit
QEMU.
