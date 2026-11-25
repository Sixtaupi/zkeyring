# Security policy

## Report a vulnerability

Submit security reports privately using **Report a vulnerability** on the
[repository's GitHub Security Advisories page](https://github.com/Sixtaupi/zkeyring/security/advisories).
If the form is unavailable, use the contact listed on the
[Sixtaupi GitHub profile](https://github.com/Sixtaupi) to request a private
reporting channel. Share vulnerability details through the private channel.

Include the affected zkeyring commit or release, the Zephyr and MCUboot
revisions, the signing backend, the impact, and steps to reproduce. Use test
keys and redact credentials, private keys and sensitive firmware data from
logs and attachments.

Keep the report private during triage. The maintainers will coordinate a fix
and disclosure with the reporter and publish confirmed issues through GitHub
Security Advisories.

## Threat model

zkeyring protects the integrity of the signing operation and limits where
private key material is handled. The assets are the signing key, credentials
that authorize signatures, the approved firmware and the public key trusted
by MCUboot.

### Signing and key custody

- Before signing, zkeyring checks the pinned fingerprint against the public
  keys from the build and the backend. It verifies the signature and signed
  image before writing the final output.
- Vault Transit, OpenBao, Infisical KMS and PKCS#11 perform signing with
  non-exportable keys in the service or token. Possession of a valid signing
  credential can still authorize signatures within its policy.
- The OS keyring, SOPS, environment, GitLab file and Vault KV backends load
  private key material into the signing process. The signing runner's
  isolation is part of the key protection for these backends.

### Trust boundaries

- Treat the signing runner, zkeyring checkout, Python dependencies, MCUboot
  imgtool and the selected backend as trusted components. Treat the profile,
  pinned fingerprint and signing credentials as trusted inputs.
- Authorize firmware through the release process. Protect the unsigned
  image and its signing metadata from substitution between build and signing,
  and give release credentials only to the protected signing job. Use separate
  development and release keys.
- Configure the device's verification key and boot policy in MCUboot. Device
  provisioning and rollback protection are part of the firmware deployment
  process.

See the [signing concepts](doc/concepts.rst) and
[deferred signing setup](doc/integration.rst#sign-in-a-separate-ci-job).
