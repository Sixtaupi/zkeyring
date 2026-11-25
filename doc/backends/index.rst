Choose a backend
================

A backend is the place that holds your signing key. Choose it from your
situation:

- **On your machine:** :doc:`local-keyring`
- **Shared with a small team through Git:** :doc:`sops`
- **In a CI secret:** :doc:`environment`, or :doc:`gitlab-secrets` on GitLab
- **For releases:** :doc:`vault-transit`, :doc:`openbao`, :doc:`infisical` or
  :doc:`pkcs11`, because the key cannot be copied
- **An existing key already stored in Vault KV:** :doc:`vault-kv`

Select the backend
------------------

You select a backend by choosing a profile in ``zkeyring.toml``. The
``backend`` line of the profile names the backend.

.. code-block:: toml

   [profiles.default]
   backend = "local-keyring"
   service = "my-product"
   key = "firmware-dev"

   [profiles.release]
   backend = "vault-transit"
   key = "firmware-release"
   version = 1

The build uses ``default``. Select another profile in ``prj.conf``, or override
it for one build with ``ZKEYRING_PROFILE``. Give MCUboot the public key of that
profile.

.. code-block:: cfg

   CONFIG_ZSIGNING_PROFILE="release"

.. code-block:: sh

   west build -b <board> --sysbuild <application> -- \
       -DZKEYRING_PROFILE=release \
       "-DSB_CONFIG_BOOT_SIGNATURE_KEY_FILE=\"$PWD/release-public.pem\""

See :ref:`release-configuration` to switch both with configuration files.

You can start with one backend and move to another later. Only the profile
changes, and the bootloader's public key if you use a new key.

Compare
-------

.. list-table::
   :header-rows: 1
   :widths: 24 24 26 26

   * - Backend
     - Private key visible to the signing job
     - Credential
     - Good for
   * - :doc:`local-keyring`
     - Yes
     - OS session
     - One developer
   * - :doc:`sops`
     - Yes
     - age identity
     - Small team, key in Git
   * - :doc:`environment`
     - Yes
     - CI secret variable
     - Simple CI
   * - :doc:`gitlab-secrets`
     - Yes
     - File from the runner
     - GitLab CI
   * - :doc:`vault-kv`
     - Yes
     - Vault token
     - Central storage of an existing key
   * - :doc:`vault-transit`
     - **No**
     - Vault token
     - Releases
   * - :doc:`openbao`
     - **No**
     - OpenBao token
     - Releases, open source service
   * - :doc:`infisical`
     - **No**
     - Infisical token
     - Releases, web-managed keys
   * - :doc:`pkcs11`
     - **No**
     - Token PIN
     - Releases, hardware protection

A common setup is ``local-keyring`` or ``sops`` with a development key, and a
remote backend with a separate release key. See :ref:`key-custody` for the
difference between in-process and remote signing.

.. note::

   The captures on these pages come from real runs with throwaway keys and local
   test services. Addresses, identifiers and fingerprints will differ in your
   environment.
