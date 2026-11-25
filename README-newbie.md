# zkeyring from A to Z

This tutorial starts from nothing and ends with a signed Zephyr firmware that
boots, and a modified one that is refused. You need no board: the firmware runs
in the QEMU emulator. Plan about an hour, mostly for downloads.

The commands are for Linux and macOS. Run them in the same terminal, in order.

## A. Understand the idea

| Word | Meaning |
| --- | --- |
| **MCUboot** | The bootloader. It starts first and decides whether to run your firmware. |
| **Private key** | Signs the firmware. Keep it secret: whoever has it can sign firmware for your devices. |
| **Public key** | Checks the signature. It is not secret, and it is built into MCUboot. |
| **Backend** | The place that holds the private key, such as your OS keyring or Vault. |
| **Profile** | A block in `zkeyring.toml` that names a backend and a key. |
| **Fingerprint** | A short identifier of a public key. It proves that you use the right one. |

Normally, the Zephyr build reads the private key from a file. With zkeyring:

1. The build uses the public key only.
2. zkeyring asks the backend to sign the firmware.
3. MCUboot checks the signature each time the firmware starts.

## B. Install the tools

Install the packages from the official
[Zephyr guide](https://docs.zephyrproject.org/latest/develop/getting_started/index.html#install-dependencies).
Use Python 3.12 or later. You also need `openssl` and QEMU.

On Ubuntu:

```sh
sudo apt install qemu-system-arm openssl
```

On macOS:

```sh
brew install qemu openssl
```

## C. Create the workspace

A Zephyr **workspace** is a folder that contains Zephyr, MCUboot and your code.
The virtual environment, `.venv`, keeps the Python packages separate from the
rest of your system.

```sh
mkdir -p ~/zkeyring-tutorial
cd ~/zkeyring-tutorial
python3 -m venv .venv
. .venv/bin/activate
pip install west
```

Download zkeyring, Zephyr and the Python packages they need.

```sh
git clone <zkeyring repository> zkeyring
west init -l --mf west-dev.yml zkeyring
west update --narrow -o=--depth=1
west packages pip --install
```

Install the compiler.

```sh
west sdk install -t arm-zephyr-eabi
```

Check that zkeyring answers.

```sh
west zkeyring --help
```

When you open a new terminal later, run this first:

```sh
cd ~/zkeyring-tutorial
. .venv/bin/activate
```

## D. Create an application

```sh
mkdir -p my_app/src my_app/sysbuild my_app/keys
cd my_app
```

The program:

```sh
cat > src/main.c <<'END'
#include <stdio.h>

int main(void)
{
	printf("Hello from signed firmware %s\n", CONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION);

	return 0;
}
END

cat > CMakeLists.txt <<'END'
cmake_minimum_required(VERSION 3.28.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(my_app)

target_sources(app PRIVATE src/main.c)
END
```

Enable zkeyring and set the firmware version:

```sh
cat > prj.conf <<'END'
CONFIG_ZSIGNING=y
CONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION="1.0.0"
END
```

Add MCUboot, and make it check the firmware at every start:

```sh
cat > sysbuild.conf <<'END'
SB_CONFIG_BOOTLOADER_MCUBOOT=y
SB_CONFIG_BOOT_SIGNATURE_TYPE_ECDSA_P256=y
SB_CONFIG_MCUBOOT_MODE_OVERWRITE_ONLY=y
END

cat > sysbuild/mcuboot.conf <<'END'
CONFIG_BOOT_VALIDATE_SLOT0=y
END
```

Tell MCUboot which public key to accept. You create this file in step F.

```sh
cat > Kconfig.sysbuild <<'END'
config BOOT_SIGNATURE_KEY_FILE
	string
	default "$(APP_DIR)/keys/public.pem"

source "share/sysbuild/Kconfig"
END
```

## E. Create your private key

This creates a key and stores it in the keyring of your operating system. The
key is not written to a file.

```sh
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 |
    python -m keyring set zkeyring-tutorial firmware
```

The keyring identifies it by two names: the service, `zkeyring-tutorial`, and
the account, `firmware`.

> On a Linux server without a desktop session, there is usually no keyring. Use
> the [SOPS backend](doc/backends/sops.rst) instead.

## F. Configure zkeyring

Describe the key in a profile. The file contains no secret, so you can commit it.

```sh
cat > zkeyring.toml <<'END'
[profiles.default]
backend = "local-keyring"
service = "zkeyring-tutorial"
key = "firmware"
END
```

Export the public key. Your OS may ask for permission to read the keyring.

```sh
west zkeyring public-key --profile default > keys/public.pem
```

Add the fingerprint of this key to the profile. From now on, zkeyring refuses to
sign with any other key.

```sh
fingerprint=$(west zkeyring fingerprint keys/public.pem)
echo "fingerprint = \"$fingerprint\"" >> zkeyring.toml
cat zkeyring.toml
```

## G. Build and sign

```sh
west build -b qemu_cortex_m0 -p --sysbuild
```

One command builds MCUboot with your public key, builds your application and
signs it. The signed firmware is `build/my_app/zephyr/zephyr.signed.bin`.

Check the signature yourself:

```sh
imgtool verify -k keys/public.pem build/my_app/zephyr/zephyr.signed.bin
```

It prints `Image was correctly validated`.

## H. Boot it

```sh
qemu-system-arm -machine microbit -nographic \
    -kernel build/mcuboot/zephyr/zephyr.elf \
    -device loader,file=build/my_app/zephyr/zephyr.signed.bin,addr=0x8000,force-raw=on
```

You see `Hello from signed firmware 1.0.0`. **To exit QEMU, press Ctrl-A, then
X.**

## I. Attack it

Change one bit of the signed firmware, as an attacker could:

```sh
python - <<'END'
from pathlib import Path

image = bytearray(Path('build/my_app/zephyr/zephyr.signed.bin').read_bytes())
image[600] ^= 1
Path('build/tampered.bin').write_bytes(image)
END
```

Boot the modified file:

```sh
qemu-system-arm -machine microbit -nographic \
    -kernel build/mcuboot/zephyr/zephyr.elf \
    -device loader,file=build/tampered.bin,addr=0x8000,force-raw=on
```

MCUboot prints `Image in the primary slot is not valid!` and does not start it.
Exit with Ctrl-A, then X.

## J. Use another backend

You can have several profiles and choose one for each build. Let us add a key
that a team can share, encrypted with [SOPS](https://getsops.io) and
[age](https://age-encryption.org). Install both tools first. On macOS, use
`brew install sops age`; on Linux, see their websites.

Create your personal age identity. It decrypts the shared key, so never commit
it.

```sh
age-keygen -o ~/zkeyring-tutorial/age-identity.txt
export SOPS_AGE_KEY_FILE=~/zkeyring-tutorial/age-identity.txt
recipient=$(age-keygen -y "$SOPS_AGE_KEY_FILE")
```

Create the encrypted key. You can commit `keys/team.sops`.

```sh
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 |
    sops encrypt --age "$recipient" \
        --input-type binary --output-type binary /dev/stdin > keys/team.sops
```

Add the profile, then export and pin its public key, as in step F.

```sh
cat >> zkeyring.toml <<'END'

[profiles.team]
backend = "sops"
keyring = "keys/team.sops"
END

west zkeyring public-key --profile team > keys/team-public.pem
fingerprint=$(west zkeyring fingerprint keys/team-public.pem)
echo "fingerprint = \"$fingerprint\"" >> zkeyring.toml
```

Build with it. Two settings must refer to the same key:

- `ZKEYRING_PROFILE` selects the key that **signs**. To make the choice
  permanent, set `CONFIG_ZSIGNING_PROFILE="team"` in `prj.conf` instead.
- `SB_CONFIG_BOOT_SIGNATURE_KEY_FILE` is the public key that MCUboot **accepts**.

```sh
west build -b qemu_cortex_m0 -p --sysbuild -- \
    -DZKEYRING_PROFILE=team \
    "-DSB_CONFIG_BOOT_SIGNATURE_KEY_FILE=\"$PWD/keys/team-public.pem\""
imgtool verify -k keys/team-public.pem build/my_app/zephyr/zephyr.signed.bin
```

For the other backends, see [Choose a backend](doc/backends/index.rst).

## K. Go further

- **A real board.** Replace `qemu_cortex_m0` with your board. Before you flash,
  read [Flash the signed image](doc/integration.rst#flash-the-signed-image):
  some tools flash the unsigned `.hex` file by default.
- **Your own workspace.** To use zkeyring in an existing project, see
  [Install](README.md#install).
- **CI.** To build without any credential and sign in a protected job, see
  [Sign in a separate CI job](doc/integration.rst#sign-in-a-separate-ci-job).
- **Releases.** Use a separate key in Vault Transit, OpenBao, Infisical or an
  HSM, so that nobody can copy it.

## When something goes wrong

| Message | What to do |
| --- | --- |
| `unknown command "zkeyring"` | Run `west` inside the workspace, after step C. |
| `missing Python package` | Activate `.venv`, then run `west packages pip --install`. |
| `no supported OS credential store is available` | Your system has no keyring. Use SOPS. |
| `signing key was not found in the OS credential store` | Compare `service` and `key` with step E. |
| `zkeyring requires a public-only MCUboot PEM` | `keys/public.pem` is missing or holds a private key. Repeat step F. |
| `unknown signing profile 'default'` | Add `[profiles.default]` or set `CONFIG_ZSIGNING_PROFILE`. |
| `profile requires a lowercase SHA-256 public key fingerprint` | Add the fingerprint, as in step F. |
| `build public key does not match the profile fingerprint` | The two settings of step J refer to different keys. |
| `SOPS could not decrypt the keyring` | Set `SOPS_AGE_KEY_FILE` again in this terminal. |
| `Unable to find bootable image` | MCUboot refused the firmware. Use the `.signed.bin` file and the same key. |

See [Troubleshooting](doc/troubleshooting.rst) for more.

## Z. Clean up

Remove the key from the keyring, then delete the tutorial folder. This also
deletes the age identity.

```sh
python -m keyring del zkeyring-tutorial firmware
deactivate
cd ~
rm -rf ~/zkeyring-tutorial
```
