Command reference
=================

``west zkeyring`` and the standalone ``zkeyring`` command have the same
subcommands. They exit with status 0 on success. Errors go to standard error
with a non-zero status.

``sign``
--------

.. code-block:: sh

   west zkeyring sign --build-dir DIR --profile NAME
                      [--config FILE] [--output FILE] [--imgtool FILE]

Sign a build and print the path of the signed image.

``--build-dir``
   The application build directory, or a sysbuild directory that contains
   exactly one zkeyring-enabled application.

``--profile``
   The profile to use.

``--config``
   The profile file. Default: ``zkeyring.toml`` in the current directory.

``--output``
   A ``.bin`` file separate from the input. Default: ``zephyr.signed.bin`` next
   to the unsigned image.

``--imgtool``
   MCUboot's ``scripts/imgtool.py``. Default: the one in the west workspace.

``public-key``
--------------

.. code-block:: sh

   west zkeyring public-key --profile NAME [--config FILE] > signing-public.pem

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
