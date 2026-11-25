Profile reference
=================

Profiles are stored in a YAML file, ``zkeyring.yaml`` by default. Paths in a
profile are relative to this file. It contains no private key or token, so you
can commit it.

.. code-block:: yaml
   :caption: zkeyring.yaml

   profiles:

     default:
       backend: "local-keyring"
       service: "zkeyring"
       key: "firmware-dev"
       fingerprint: "<64 lowercase hexadecimal digits>"

     release:
       backend: "vault-transit"
       key: "firmware-release"
       version: 1
       public_key: "keys/release-public.pem"
       fingerprint: "<64 lowercase hexadecimal digits>"

Every profile has these fields.

``backend``
   Required. One of the names below.

``fingerprint``
   Required to sign. See :ref:`fingerprint-pinning`.

``public_key``
   Optional path to a local public PEM. Its key type must match MCUboot's
   signature configuration. Relative paths are resolved from the profile
   file. With ``SB_CONFIG_ZKEYRING_PROFILE_PUBLIC_KEY=y``,
   Sysbuild uses this key as MCUboot's primary verification key after checking
   its fingerprint. See :ref:`profile-public-key`.

.. list-table::
   :header-rows: 1
   :widths: 22 38 40

   * - Backend
     - Required fields
     - Optional fields
   * - :doc:`local-keyring <backends/local-keyring>`
     - ``service``, ``key``
     -
   * - :doc:`sops <backends/sops>`
     - ``keyring``
     -
   * - :doc:`environment <backends/environment>`
     - ``variable``
     - ``encoding``: ``pem`` (default) or ``base64``
   * - :doc:`gitlab-secrets <backends/gitlab-secrets>`
     - ``variable``
     -
   * - :doc:`vault-kv <backends/vault-kv>`
     - ``path``, ``field``, ``version``
     - ``mount`` (default ``secret``), ``url``, ``encoding``, ``namespace``,
       ``role``, ``auth_path``
   * - :doc:`vault-transit <backends/vault-transit>`
     - ``key``, ``version``
     - ``mount`` (default ``transit``), ``url``, ``namespace``, ``role``,
       ``auth_path``
   * - :doc:`openbao <backends/openbao>`
     - ``key``, ``version``
     - ``mount`` (default ``transit``), ``url``, ``namespace``, ``role``,
       ``auth_path``
   * - :doc:`infisical <backends/infisical>`
     - ``key``, ``version``
     - ``url``
   * - :doc:`pkcs11 <backends/pkcs11>`
     - ``module``, ``token``, ``key``, ``pin_variable``
     -

``version`` is a positive integer. ``url`` must use HTTPS, except on
``localhost``, ``127.0.0.1`` and ``::1``.

``url`` and ``namespace`` are used when the environment does not set them.
``VAULT_ADDR`` and ``VAULT_NAMESPACE``, ``BAO_ADDR`` and ``BAO_NAMESPACE``, or
``INFISICAL_API_URL`` take precedence. ``role`` and
``auth_path`` are the OIDC role and auth mount that ``west zkeyring login``
uses by default. See :ref:`login-logout`.

.. code-block:: yaml

   profiles:
     release:
       backend: "vault-transit"
       url: "https://vault.example.com"
       namespace: "firmware"
       role: "firmware-dev"
       auth_path: "entra"
       key: "firmware-release"
       version: 1
