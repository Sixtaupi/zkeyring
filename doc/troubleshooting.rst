Troubleshooting
===============

Build
-----

*zkeyring requires a public-only MCUboot PEM and an imgtool with keyinfo*
   The configured key file is missing or contains a private key. Set
   ``SB_CONFIG_BOOT_SIGNATURE_KEY_FILE`` to a public PEM. If it is correct,
   check that MCUboot in your workspace provides ``imgtool keyinfo``.

*zkeyring profile file not found*
   Set ``CONFIG_ZSIGNING_PROFILE_FILE`` or ``ZKEYRING_CONFIG``.

*This MCUboot configuration is outside the zkeyring .bin signing scope*
   See :ref:`unsupported`.

*missing Python package ..., run "west packages pip --install"*
   Run the command in the virtual environment that contains west.

Profiles and keys
-----------------

*unknown signing profile 'default'*
   The build uses ``default`` unless another profile is selected. Set
   ``CONFIG_ZSIGNING_PROFILE`` or ``ZKEYRING_PROFILE``, or add
   ``[profiles.default]``.

*profile requires a lowercase SHA-256 public key fingerprint*
   Pin the key as described in :ref:`fingerprint-pinning`.

*build public key does not match the profile fingerprint*
   The bootloader is configured for another key. Check the public PEM and the
   selected profile, and use a clean build directory.

*backend public key does not match the profile fingerprint*
   The backend returned another key. Find out why before changing the
   fingerprint.

*the signing key must be ECDSA P-256*
   Create a P-256 key. Other curves and RSA are not supported.

*set ... to use the signing backend*
   The named environment variable is missing or empty.

   .. figure:: images/environment-missing.png
      :alt: west zkeyring sign fails with the message set FIRMWARE_SIGNING_KEY to use the signing backend.

Services
--------

*signing service URL must use HTTPS or loopback HTTP*
   Use an ``https://`` address.

*could not reach the signing service*
   Check the address, the network and the TLS certificate. Requests time out
   after 30 seconds.

*signing service returned HTTP 403*
   The token is invalid, expired or revoked, or its policy does not allow the
   operation.

*signing service returned HTTP 404*
   Check the mount, the path and the key name.

*... requires a non-exportable key without plaintext backup*
   Create a new key with the options shown on the backend page and rotate to
   it. Do not reuse a key that was exportable.

Boot
----

*Unable to find bootable image*
   MCUboot rejected the image. Check that the bootloader was built with the same
   public key, that you flashed the ``.signed.bin`` file, and that it is at the
   ``slot0_partition`` address.
