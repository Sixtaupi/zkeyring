Profile reference
=================

Profiles are stored in a TOML file, ``zkeyring.toml`` by default. Paths in a
profile are relative to this file. It contains no private key or token, so you
can commit it.

.. code-block:: toml

   [profiles.default]
   backend = "local-keyring"
   service = "zkeyring"
   key = "firmware-dev"
   fingerprint = "<64 lowercase hexadecimal digits>"

   [profiles.release]
   backend = "vault-transit"
   key = "firmware-release"
   version = 1
   fingerprint = "<64 lowercase hexadecimal digits>"

Every profile has these fields.

``backend``
   Required. One of the names below.

``fingerprint``
   Required to sign. See :ref:`fingerprint-pinning`.

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
     - ``mount`` (default ``secret``), ``url``, ``encoding``
   * - :doc:`vault-transit <backends/vault-transit>`
     - ``key``, ``version``
     - ``mount`` (default ``transit``), ``url``
   * - :doc:`openbao <backends/openbao>`
     - ``key``, ``version``
     - ``mount`` (default ``transit``), ``url``
   * - :doc:`infisical <backends/infisical>`
     - ``key``, ``version``
     - ``url``
   * - :doc:`pkcs11 <backends/pkcs11>`
     - ``module``, ``token``, ``key``, ``pin_variable``
     -

``version`` is a positive integer. ``url`` must use HTTPS, except on
``localhost``, ``127.0.0.1`` and ``::1``.

.. note::

   ``vault`` is accepted as an alias of ``vault-transit``. Existing
   ``zkeys.toml`` files and ``zkeys-sign.json`` build metadata are still read.
