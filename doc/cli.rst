Command reference
=================

``west zkeyring`` and the standalone ``zkeyring`` command have the same
subcommands. They exit with status 0 on success. Errors go to standard error
with a non-zero status.

Commands that use a signing profile select ``default`` unless you pass
``--profile NAME``. Add ``--verbose`` to see the exception chain with details
such as system error numbers and YAML line numbers.

.. _login-logout:

``login`` and ``logout``
------------------------

Open and close a local session for a Vault Transit, Vault KV, OpenBao or Infisical
profile. Run from the application directory or pass the same ``--config`` file
that the build uses.

.. code-block:: sh

   west zkeyring login --profile vault --role firmware-dev
   west zkeyring logout --profile vault

   west zkeyring login --profile openbao --role firmware-dev
   west zkeyring logout --profile openbao

   west zkeyring login --profile infisical
   west zkeyring logout --profile infisical

Vault and OpenBao require their ``vault`` or ``bao`` CLI and an OIDC auth mount.
Use ``--auth-path`` for a mount other than ``oidc``. Omit ``--role`` to use
the mount's default role. The profile can set both as ``auth_path`` and
``role``; the options override them.
Infisical uses interactive user login and requires Infisical CLI 0.45.4 or later.
The user needs permission to use the signing key in Infisical KMS.

The service URL comes from ``VAULT_ADDR``, ``BAO_ADDR`` or
``INFISICAL_API_URL``, or else from the profile's ``url``. The namespace comes
from ``VAULT_NAMESPACE`` or ``BAO_NAMESPACE``, or else from the profile's
``namespace``. The environment takes precedence. Keep the same URL and namespace
when logging in, signing and logging out.

zkeyring stores the access token in the OS keyring. The session is scoped to the
profile file, profile name, service URL and Vault namespace. The build and
``public-key`` reuse it. Run ``login`` again when the session expires.
``VAULT_TOKEN``, ``BAO_TOKEN`` and ``INFISICAL_TOKEN`` from the environment take precedence.

In a terminal, the build and ``public-key`` log in by themselves when no session
exists, and log in again once when the service refuses the session, for example
after it expired. They use the profile's ``role`` and ``auth_path``. zkeyring
never logs in when the ``CI`` variable is set, when there is no terminal, or when
the token comes from the environment: the command fails with *run zkeyring login
for this profile or set VAULT_TOKEN* instead. In CI, set ``VAULT_TOKEN``,
``BAO_TOKEN`` or ``INFISICAL_TOKEN``.

``logout`` removes the local token and asks the vendor CLI to end the session.
Vault and OpenBao require permission on ``auth/token/revoke-self``. Infisical ends its
dedicated CLI profile session. Tokens supplied through environment variables
are managed by the caller.

``sign``
--------

.. code-block:: sh

   west zkeyring sign --build-dir DIR [--profile NAME]
                      [--config FILE] [--output FILE] [--imgtool FILE] [--verbose]

Sign a build and print the path of the signed image.

``--build-dir``
   The application build directory, or a sysbuild directory that contains
   exactly one zkeyring-enabled application.

``--profile``
   The profile name. The default is ``default``.

``--config``
   The YAML profile file. The command looks for ``zkeyring.yaml`` and then
   ``zkeyring.yml`` in the current directory by default.

``--output``
   A ``.bin`` file separate from the input. Default: ``zephyr.signed.bin`` next
   to the unsigned image.

``--imgtool``
   MCUboot's ``scripts/imgtool.py``. Default: the one in the west workspace.

``public-key``
--------------

.. code-block:: sh

   west zkeyring public-key [--profile NAME] [--config FILE] > signing-public.pem

Write the public key of the backend to standard output. If the profile has a
fingerprint, it must match.

``fingerprint``
---------------

.. code-block:: sh

   west zkeyring fingerprint signing-public.pem

Print the fingerprint of a public PEM.

Verify an image
---------------

Use MCUboot's imgtool to check a signed image independently.

.. code-block:: sh

   imgtool verify -k signing-public.pem firmware.signed.bin
