# Sign Zephyr firmware with zkeyring

zkeyring signs Zephyr MCUboot `.bin` images with ECDSA P-256, RSA-PSS 2048 or
3072, or Ed25519 over a SHA-256 image digest. The build consumes a public PEM.
The signing profile selects a provider.

| Priority | Provider | Profile `backend` | Where signing happens |
| --- | --- | --- | --- |
| P0 | `LocalKeyringProvider` | `local-keyring` | In the process, with a PEM from the OS credential store |
| P0 | `SopsProvider` | `sops` | In the process, with a PEM decrypted by SOPS |
| P0 | `EnvironmentProvider` | `environment` | In the process, with a PEM supplied by CI |
| P1 | `VaultKVProvider` | `vault-kv` | In the process, with a PEM from a versioned KV v2 secret |
| P1 | `VaultTransitProvider` | `vault-transit` | Vault Transit |
| P1 | `OpenBaoProvider` | `openbao` | OpenBao Transit |
| P1 | `InfisicalProvider` | `infisical` | Infisical KMS |
| P2 | `GitLabSecretsProvider` | `gitlab-secrets` | In the process, with the PEM file supplied by GitLab Runner |
| P2 | `PKCS11Provider` | `pkcs11` | The PKCS#11 token or HSM |

PEM providers and Vault or OpenBao Transit support all four key types.
PKCS#11 requires the token to support the matching signing mechanism.
Infisical KMS supports ECDSA P-256 and Ed25519 in this integration. Ed25519
requires an Infisical version that supports Ed25519 signing keys.

## Install and configure

From the repository root, initialize west if this checkout is outside a
workspace, then fetch the manifest's pinned revisions.

```sh
west init -l --mf west-dev.yml .
west update --narrow -o=--depth=1
```

In the Python virtual environment of your west workspace, install zkeyring
from this checkout and the dependencies of Zephyr and its modules.

```sh
python -m pip install -e .
west packages pip --install
```

The `west zkeyring` command and the build run the zkeyring sources from the
workspace, at the revision selected by the west manifest. To use zkeyring in
another workspace, add it to that workspace's manifest. `west-commands` is
needed unless the entry imports the zkeyring manifest.

```yaml
projects:
  - name: zkeyring
    url: <zkeyring repository>
    revision: <commit>
    west-commands: scripts/west-commands.yml
```

A signing environment without a west workspace can install the standalone
`zkeyring` command instead, with the same subcommands. Install the dependencies
of the MCUboot revision used for the build and pass its imgtool with
`--imgtool`.

```sh
python -m pip install "git+<zkeyring repository>@<commit>"
```

For CLI commands, create `zkeyring.yaml` in the working directory or pass
`--config`. Paths in a profile are relative to this file. Each fingerprint is
the lowercase SHA-256 of the DER-encoded SubjectPublicKeyInfo.

The signed firmware sample provides a profile for every backend in
[`zkeyring.yaml`](../samples/signed_firmware/zkeyring.yaml). Copy the profiles
you need and adjust the backend settings for your environment.

Use `west zkeyring public-key --profile dev > dev-public.pem` to export the public
key and `west zkeyring fingerprint dev-public.pem` to compute the value for the profile.
During provisioning, omit the fingerprint from the profile to inspect the
public key. Confirm it using a trusted source and then pin it. The signing
command requires a fingerprint and checks it against both the build PEM and the
backend.

### OS keyring and CI environments

For `local-keyring`, store the signing PEM as the credential value under
the configured service and account. zkeyring uses a native credential store,
such as macOS Keychain, Freedesktop Secret Service or Windows Credential Locker.

For `environment`, set the named CI secret only in the signing job.
`encoding: "pem"` accepts the multiline PEM and is the default.
`encoding: "base64"` accepts a single-line base64 representation of the
entire PEM. Both forms make the private key available to the signing process.

### SOPS + age

Create an age identity for each developer and for CI. Encrypt one signing key
per group of authorized recipients. For a new development key, pipe the PEM
directly into SOPS.

```sh
mkdir -p keys
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 |
    sops encrypt --age "$DEV_AGE_RECIPIENT,$CI_AGE_RECIPIENT" \
        --input-type binary --output-type binary /dev/stdin > keys/dev.sops
```

Each developer can use `SOPS_AGE_KEY_FILE` or the standard SOPS identity
location. A CI runner can receive its dedicated identity through the protected
`SOPS_AGE_KEY` variable. If the developer's identity is stored in an OS
keychain, provide it to SOPS through `SOPS_AGE_KEY` for the signing process.
zkeyring reads the decrypted signing PEM through a pipe.

To remove a recipient, list the remaining recipients in `.sops.yaml`.

```yaml
creation_rules:
  - path_regex: '^keys/dev\.sops$'
    age: '<remaining age recipients, comma-separated>'
```

Apply the change and rotate the document data key.

```sh
sops updatekeys keys/dev.sops
sops rotate --in-place --input-type binary --output-type binary keys/dev.sops
```

A removed recipient retains access to historical copies they could previously
decrypt. Rotate the firmware signing key and update MCUboot trust if the
previous key must be retired.

### Vault, OpenBao and Infisical

- **Vault KV v2.** Store the PEM in the configured field. Grant read on
  `secret/data/firmware/dev` and set `VAULT_TOKEN`. The profile pins the KV
  version. Set `encoding: "base64"` if the field contains encoded PEM.
- **Vault Transit and OpenBao.** Create an `ecdsa-p256` Transit key with export
  and plaintext backup disabled. Grant read on `transit/keys/firmware-release`
  and update on `transit/sign/firmware-release/sha2-256`.
  RSA keys use the same paths. For Ed25519, grant update on
  `transit/sign/firmware-release`.
  Use `VAULT_ADDR`, `VAULT_TOKEN` and optionally `VAULT_NAMESPACE` for
  Vault. Use `BAO_ADDR`, `BAO_TOKEN` and optionally `BAO_NAMESPACE` for
  OpenBao. A profile can set `url` and `namespace` for when these variables
  are not set.
- **Infisical.** Create an ECC-NIST-P256 Sign/Verify key with *Allow Export*
  disabled. Set `INFISICAL_TOKEN` to a short-lived machine identity access token
  with permission to read key metadata and the public key and sign. The profile
  pins the current version. `INFISICAL_API_URL` takes precedence over the
  profile `url`.

### GitLab Secrets and PKCS#11

For `gitlab-secrets`, set `variable` to the GitLab job variable that contains
the absolute path of the runner-managed PEM file. This is the default
GitLab Secrets Manager delivery mode and also works with file-type CI/CD
variables. If the job uses `file: false`, select the `environment` provider.
[GitLab Secrets Manager](https://docs.gitlab.com/ci/secrets/secrets_manager/#use-secrets-in-job-scripts)

```yaml
sign_firmware:
  secrets:
    FIRMWARE_PEM_FILE:
      gitlab_secrets_manager:
        name: firmware_signing_key
  script:
    - west zkeyring sign --build-dir build --profile gitlab
```

For PKCS#11, install the optional dependency with
`python -m pip install 'python-pkcs11>=0.9'`. Provision a sensitive,
non-extractable P-256 private key and its public key on the token. Both entries
use the same `CKA_ID`, which the profile specifies in hex. Set the named PIN
variable only in the signing process. The token signs the SHA-256 digest with
`CKM_ECDSA`.

In CI, set credentials only in the protected signing job. Keep distinct dev
and release keys. Use the release public key in the release MCUboot build.

## Build and sign

Import the zkeyring module into the Zephyr workspace and add this to the
application's `prj.conf` to sign the MCUboot image during the build.

```kconfig
CONFIG_ZSIGNING=y
```

With `west build --sysbuild`, Zephyr builds MCUboot and the application, and
invokes zkeyring after the application binary is generated. Provide any
backend credentials, such as `VAULT_TOKEN`, in the build environment. zkeyring
uses the same Python environment as Zephyr.

The build selects `profiles.default` in `zkeyring.yaml` at the root of the
Zephyr application by default. To use a different file, set its path in the
application's `prj.conf`.

```kconfig
CONFIG_ZSIGNING_PROFILE_FILE="signing/profiles.yaml"
```

Relative paths are resolved from the application root, where its
`CMakeLists.txt` is located.
The `ZKEYRING_CONFIG` CMake variable overrides `CONFIG_ZSIGNING_PROFILE_FILE`.
Use `ZKEYRING_PROFILE` to select another profile within the file. In sysbuild,
prefix either CMake variable with `<image>_` to configure a specific image.

This example uses Zephyr's Hello World sample and the QEMU Cortex-M0 board from
the integration test. It sets the same Kconfig option from the command line
for the unmodified sample. Set `public_key` and `fingerprint` in the `dev`
profile and use a separate build directory for each profile.

```sh
west build -b qemu_cortex_m0 --sysbuild ../zephyr/samples/hello_world -d build -- \
    -DSB_CONFIG_BOOTLOADER_MCUBOOT=y \
    -DSB_CONFIG_ZKEYRING_SIGNATURE_ECDSA_P256=y \
    -DSB_CONFIG_ZKEYRING_PROFILE_PUBLIC_KEY=y \
    -DSB_CONFIG_MCUBOOT_MODE_SINGLE_APP=y \
    -Dmcuboot_CONFIG_BOOT_VALIDATE_SLOT0=y \
    -Dhello_world_CONFIG_ZSIGNING=y \
    "-DZKEYRING_CONFIG=$PWD/zkeyring.yaml" \
    -DZKEYRING_PROFILE=dev
```

The signed file is `build/hello_world/zephyr/zephyr.signed.bin` by default.
The selected MCUboot board configuration must validate images according to your
boot policy. The hook supports single-slot, swap and overwrite-only builds.
It rejects unsupported settings such as image encryption, direct XIP, RAM load
and extra imgtool arguments.

The [signed firmware sample](../samples/signed_firmware/README.md) shows the
Vault Transit flow on a minimal application running under QEMU.

### Sign in a separate CI job

Set `CONFIG_ZSIGNING_DEFERRED=y` alongside `CONFIG_ZSIGNING=y` in the
application configuration. The build exports the unsigned `zephyr/zephyr.bin`,
`zkeyring-sign.json` and `zkeyring-public.pem`. Transfer these files to the
signing job, keeping their paths relative to the application build directory.
Sign from a workspace at the same west manifest revision or pass its
`scripts/imgtool.py` with `--imgtool`.

```sh
west zkeyring sign --build-dir build --profile dev
```

Publish the signed output after a successful exit. Use `--output` to select a
separate artifact path for the job.

## Validate

From the repository root, run the Docker suite with the Zephyr SDK, Vault,
OpenBao, Secret Service and SoftHSM. The full suite adds Infisical,
PostgreSQL and Redis.

```sh
scripts/test-signing
scripts/test-signing --infisical
```

The suite checks Python against Zephyr's Ruff configuration. It generates
temporary credentials and keys, tests the services and uses QEMU to test
acceptance and rejection by MCUboot. The GitLab test exercises a file supplied
in the same form as a runner secret. Use Zephyr's C style and `checkpatch` for
C contributions.
