# Changelog

## Unreleased

### Added

- Sign MCUboot firmware from Zephyr sysbuild or in a separate signing job.
- Provide a west command and a standalone Python CLI for firmware signing,
  public key export and backend sessions.
- Support ECDSA P-256, RSA-PSS 2048 and 3072, and Ed25519 with compatible backends.
- Provide a signed firmware sample that can boot under QEMU.
- Document coordinated disclosure, the signing threat model and contribution
  checks.

### Changed

- Profile commands select `default` when `--profile` is omitted.
- Network errors identify the failure category, with exception details
  available through `--verbose`.
- Deploy the documentation to GitHub Pages on pushes to `main`.

### Removed

- Remove the pre-release TOML profiles, `zkeys-*` build metadata names and
  `vault` backend alias. Use YAML profiles, `zkeyring-*` build files and
  `vault-transit`.
