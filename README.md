# zkeyring

Sign Zephyr firmware for MCUboot without putting the private key in the build.
The build only needs a public key. The private key stays in the backend you
choose:

- **Local or CI secrets:** OS keyring, SOPS, environment variable, GitLab,
  Vault KV
- **Remote signing, the key never leaves the service:** Vault Transit, OpenBao,
  Infisical, PKCS#11

zkeyring signs `.bin` images with ECDSA P-256.

> **Status:** draft/experimental. zkeyring is passing verification steps
> for vault backend.

## Install

Add the module to your `west.yml`:

```yaml
- name: zkeyring
  url: https://github.com/sixtaupi/zkeyring.git
  revision: main
  west-commands: scripts/west-commands.yml
```

```sh
west update
west packages pip --install
```

## Configure a backend

First choose where the key lives:

- **On your machine:** `local-keyring`
- **Shared with a small team through Git:** `sops`
- **In a CI secret:** `environment`, or `gitlab-secrets` on GitLab
- **For releases or dev teams with PKI:** `vault-transit`, `openbao`, `infisical` or `pkcs11`,
  because the key cannot be copied
- **An existing key already stored in Vault KV:** `vault-kv`

You select it with a **profile** in `zkeyring.toml`, in the application
directory. The `backend` line of the profile names the backend, and
`CONFIG_ZSIGNING_PROFILE` chooses the profile for the build. The file contains
no secret.

The steps below use the OS keyring. Run them from the application directory.

**1. Create the key in the backend.** This replaces an existing entry with the
same names.

```sh
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 |
    python -m keyring set my-product firmware-dev
```

**2. Describe it in `zkeyring.toml`.**

```toml
[profiles.default]
backend = "local-keyring"
service = "my-product"
key = "firmware-dev"
```

**3. Export its public key.**

```sh
west zkeyring public-key --profile default > signing-public.pem
west zkeyring fingerprint signing-public.pem
```

**4. Pin the key.** Add the printed value to the profile. Signing fails if the
backend or the build uses another key.

```toml
fingerprint = "<printed value>"
```

For another backend, change the profile fields and provide its credential in
the environment:

| `backend` | Profile fields | Credential |
| --- | --- | --- |
| [`local-keyring`](doc/backends/local-keyring.rst) | `service`, `key` | OS session |
| [`sops`](doc/backends/sops.rst) | `keyring` | SOPS identity, such as `SOPS_AGE_KEY_FILE` |
| [`environment`](doc/backends/environment.rst) | `variable` | The named variable |
| [`gitlab-secrets`](doc/backends/gitlab-secrets.rst) | `variable` | The named file variable |
| [`vault-kv`](doc/backends/vault-kv.rst) | `path`, `field`, `version` | `VAULT_ADDR`, `VAULT_TOKEN` |
| [`vault-transit`](doc/backends/vault-transit.rst) | `key`, `version` | `VAULT_ADDR`, `VAULT_TOKEN` |
| [`openbao`](doc/backends/openbao.rst) | `key`, `version` | `BAO_ADDR`, `BAO_TOKEN` |
| [`infisical`](doc/backends/infisical.rst) | `key`, `version` | `INFISICAL_API_URL`, `INFISICAL_TOKEN` |
| [`pkcs11`](doc/backends/pkcs11.rst) | `module`, `token`, `key`, `pin_variable` | The named PIN variable |

Each backend page explains how to create the key. The
[sample `zkeyring.toml`](samples/signed_firmware/zkeyring.toml) has a profile
for every backend.

## Build and sign

In `prj.conf`:

```cfg
CONFIG_ZSIGNING=y
```

In `sysbuild.conf`:

```cfg
SB_CONFIG_BOOTLOADER_MCUBOOT=y
SB_CONFIG_BOOT_SIGNATURE_TYPE_ECDSA_P256=y
```

The build uses `[profiles.default]`. To select another profile, set
`CONFIG_ZSIGNING_PROFILE="<name>"` in `prj.conf`, or pass
`-DZKEYRING_PROFILE=<name>` for one build. To switch between development and
release, see
[Development and release configurations](doc/integration.rst#development-and-release-configurations).

Build with the public key:

```sh
west build -b <board> --sysbuild -- \
    "-DSB_CONFIG_BOOT_SIGNATURE_KEY_FILE=\"$PWD/signing-public.pem\""
```

Flash `zephyr.signed.bin`. Some runners flash the unsigned `.hex` by default;
see [Flash the signed image](doc/integration.rst#flash-the-signed-image).

## Documentation

- [From A to Z, for beginners](README-newbie.md)
- [Getting started](doc/getting-started.rst)
- [Concepts](doc/concepts.rst)
- [Choose a backend](doc/backends/index.rst)
- [Build integration](doc/integration.rst)
- [Sample](samples/signed_firmware/README.md)

## Develop

```sh
west init -l --mf west-dev.yml .
west update
west packages pip --install
scripts/test-signing    # requires Docker
```

## License

[Apache-2.0](LICENSE)

## Assitance

Assisted by ChatGPT-6.2 Sol and Claude Opus-5.5
