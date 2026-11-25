vault-transit
=============

Sign with a non-exportable key in the Transit engine of HashiCorp Vault.

.. list-table::
   :widths: 30 70

   * - **Private key is stored in**
     - HashiCorp Vault Transit
   * - **Signing happens in**
     - HashiCorp Vault
   * - **Signing job needs**
     - A Vault token that can use one key
   * - **Good for**
     - Release signing

Create the key
--------------

Enable the engine and create an ``ecdsa-p256`` key.

.. code-block:: sh

   vault secrets enable transit
   vault write transit/keys/firmware-release type=ecdsa-p256 \
       exportable=false allow_plaintext_backup=false

.. important::

   zkeyring refuses a key that is exportable or allows plaintext backup.

Create a policy that can read the public key and request SHA-256 signatures
with this key.

.. code-block:: text

   path "transit/keys/firmware-release" {
     capabilities = ["read"]
   }
   path "transit/sign/firmware-release/sha2-256" {
     capabilities = ["update"]
   }

.. code-block:: sh

   vault policy write firmware-sign - < firmware-sign.hcl

.. figure:: ../images/vault-transit-provision.png
   :alt: Terminal session that enables Transit, creates a non-exportable P-256 key, and uploads the signing policy.

   Create the key and the policy.

.. tab-set::

   .. tab-item:: Key

      .. figure:: ../images/vault-transit-ui-key.png
         :alt: Vault web interface showing the firmware-release key of type ecdsa-p256.
         :class: ui

         The key in the Vault web interface.

   .. tab-item:: Versions

      .. figure:: ../images/vault-transit-ui-versions.png
         :alt: Vault web interface showing version 1 of the firmware-release key.
         :class: ui

         The version number to pin in the profile.

   .. tab-item:: Policy

      .. figure:: ../images/vault-transit-ui-policy.png
         :alt: Vault web interface showing the firmware-sign policy.
         :class: ui

         The policy in the Vault web interface.

Create a short-lived token for the signing job, or use an auth method of your
CI system.

.. code-block:: sh

   vault token create -policy=firmware-sign -no-default-policy -ttl=15m

Configure the profile
---------------------

.. code-block:: toml

   [profiles.vault]
   backend = "vault-transit"
   mount = "transit"
   key = "firmware-release"
   version = 1

Set ``VAULT_ADDR`` and ``VAULT_TOKEN`` in the environment, and
``VAULT_NAMESPACE`` if you use namespaces. The profile can set ``url`` instead of
``VAULT_ADDR``.

Export the public key and add its fingerprint to the profile.

.. code-block:: sh

   west zkeyring public-key --profile vault > signing-public.pem
   west zkeyring fingerprint signing-public.pem

Sign
----

.. code-block:: sh

   west zkeyring sign --build-dir build --profile vault \
       --output firmware.signed.bin
   imgtool verify -k signing-public.pem firmware.signed.bin

.. figure:: ../images/vault-transit-sign.png
   :alt: The profile, a successful signature and a successful imgtool verification.

   Vault signs the digest. The private key stays in the service.

Revoke access
-------------

Revoke the token to stop the signing job immediately. The key does not change.

.. figure:: ../images/vault-transit-revoked.png
   :alt: west zkeyring sign fails with signing service returned HTTP 403 and produces no output.

   A revoked token can no longer sign.

Security notes
--------------

- The token holder can request signatures but cannot read the key.
- Enable an audit device to record each signature request.
- Administrators can change the policy or the key. Protect these permissions
  separately.
- See :ref:`network-environment` for proxies.

Rotate
------

Rotate the key in Transit.

.. code-block:: sh

   vault write -f transit/keys/firmware-release/rotate

Signing continues with the pinned version. To move to the new key, build a
bootloader that trusts its public key, then change ``version`` and
``fingerprint`` together.

Troubleshoot
------------

*Vault Transit key must be of type ecdsa-p256*
   Create a key of the right type.

*Vault Transit requires a non-exportable key without plaintext backup*
   Create a new key with the options above.

*Vault Transit key version has no public key*
   The pinned ``version`` does not exist.

*signing service returned HTTP 403*
   The token is invalid or revoked, or the policy does not cover both paths.
