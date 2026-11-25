Build integration
=================

.. _add-to-manifest:

Add zkeyring to your workspace
------------------------------

Your workspace provides Zephyr and MCUboot. Add zkeyring to its ``west.yml``.
``west-commands`` makes ``west zkeyring`` available. It is not needed if the
entry imports the zkeyring manifest, which adds no other project.

.. code-block:: yaml

   manifest:
     projects:
       - name: zkeyring
         url: <zkeyring repository>
         revision: <commit>
         west-commands: scripts/west-commands.yml

Then update the workspace and install the Python dependencies of its modules.

.. code-block:: sh

   west update
   west packages pip --install

This installs the zkeyring Python package and its dependencies. The command and
the build run the zkeyring sources of the workspace, at the revision selected
by the manifest. To use a development checkout, run
``python -m pip install -e path/to/zkeyring`` before installing the workspace
dependencies.

Enable signing
--------------

Add this to the application's ``prj.conf``.

.. code-block:: cfg

   CONFIG_ZSIGNING=y

Enable MCUboot with an ECDSA P-256 key and select the key from the zkeyring
profile in ``sysbuild.conf``.

.. code-block:: cfg

   SB_CONFIG_BOOTLOADER_MCUBOOT=y
   SB_CONFIG_ZKEYRING_SIGNATURE_ECDSA_P256=y
   SB_CONFIG_ZKEYRING_PROFILE_PUBLIC_KEY=y

Set the path to the **public** key and its fingerprint in the profile, then
build. See :ref:`profile-public-key`.

.. code-block:: sh

   west build -b <board> --sysbuild <application> -- \
       -DZKEYRING_PROFILE=dev

Choose the signature algorithm
------------------------------

Open the Sysbuild Kconfig menu for an existing build.

.. code-block:: sh

   west build -d build -t sysbuild_menuconfig

Under **Modules**, open the zkeyring module and select **Firmware signature
algorithm**. The menu offers ECDSA P-256, RSA-PSS 2048, RSA-PSS 3072 and
Ed25519. Select a profile whose key matches the algorithm. The menu saves its
selection in the build directory. To keep the selection across clean builds,
set the corresponding option in ``sysbuild.conf``.

.. list-table::
   :header-rows: 1

   * - Algorithm
     - Option
   * - ECDSA P-256
     - ``SB_CONFIG_ZKEYRING_SIGNATURE_ECDSA_P256=y``
   * - RSA-PSS 2048
     - ``SB_CONFIG_ZKEYRING_SIGNATURE_RSA_2048=y``
   * - RSA-PSS 3072
     - ``SB_CONFIG_ZKEYRING_SIGNATURE_RSA_3072=y``
   * - Ed25519
     - ``SB_CONFIG_ZKEYRING_SIGNATURE_ED25519=y``

An explicit zkeyring selection takes precedence over the MCUboot Sysbuild
algorithm settings. The default menu entry uses those existing settings.

With Mbed TLS 4, MCUboot's RSA build uses TF-PSA-Crypto. The development
manifest includes this module. In an existing development workspace, fetch it
with ``west update tf-psa-crypto``.

Select the profile
------------------

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Setting
     - Meaning
   * - ``CONFIG_ZSIGNING``
     - Sign the image with zkeyring
   * - ``CONFIG_ZSIGNING_DEFERRED``
     - Export the signing inputs without signing
   * - ``CONFIG_ZSIGNING_PROFILE_FILE``
     - Profile file, relative to the application. Default: ``zkeyring.yaml``
   * - ``CONFIG_ZSIGNING_PROFILE``
     - Profile to use. Default: ``default``
   * - ``ZKEYRING_CONFIG``
     - CMake variable that overrides the profile file
   * - ``ZKEYRING_PROFILE``
     - CMake variable that overrides the profile

In sysbuild, prefix a CMake variable with ``<image>_`` to configure one image,
for example ``-Dsigned_firmware_ZKEYRING_PROFILE=dev``.

Backend credentials, such as ``VAULT_TOKEN``, must be present in the
environment of the build. Use a separate build directory for each public key.

.. _profile-public-key:

Set the public key in the profile
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Enable ``SB_CONFIG_ZKEYRING_PROFILE_PUBLIC_KEY=y`` in ``sysbuild.conf`` and
``CONFIG_ZSIGNING=y`` in the main application. Set ``public_key`` and
``fingerprint`` in the selected zkeyring profile. For example:

.. code-block:: yaml

   profiles:
     release:
       backend: "vault-transit"
       key: "firmware-release"
       version: 1
       public_key: "keys/release-public.pem"
       fingerprint: "<64 lowercase hexadecimal digits>"

Sysbuild checks the local PEM against the fingerprint and uses it as MCUboot's
primary verification key. The check works offline, including in deferred
signing builds. Select another key by changing ``CONFIG_ZSIGNING_PROFILE`` in
``prj.conf`` or with ``-DZKEYRING_PROFILE=<name>``. A profile without
``public_key`` uses the public PEM configured by ``SB_CONFIG_BOOT_SIGNATURE_KEY_FILE``.

.. _release-configuration:

Development and release configurations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Define ``dev`` and ``release`` in ``zkeyring.yaml``, each with its own
``public_key`` and ``fingerprint``. Enable ``SB_CONFIG_ZKEYRING_PROFILE_PUBLIC_KEY``
in the shared ``sysbuild.conf``. Use Zephyr's ``FILE_SUFFIX`` to select the
application configuration for each build.

.. code-block:: cfg
   :caption: prj.conf

   CONFIG_ZSIGNING=y
   CONFIG_ZSIGNING_PROFILE="dev"

.. code-block:: cfg
   :caption: prj_release.conf

   CONFIG_ZSIGNING=y
   CONFIG_ZSIGNING_PROFILE="release"

.. code-block:: sh

   west build -b <board> -p --sysbuild -d build
   west build -b <board> -p --sysbuild -d build-release -- -DFILE_SUFFIX=release

You can also select the profile with ``-DZKEYRING_PROFILE=release`` or set
``CONFIG_ZSIGNING_PROFILE`` in an extra configuration file passed with
``-DEXTRA_CONF_FILE=release.conf``.

.. _deferred-signing:

Sign in a separate CI job
-------------------------

Keep credentials out of the build with deferred signing.

.. raw:: html
   :file: diagrams/deferred.svg

.. code-block:: cfg

   CONFIG_ZSIGNING=y
   CONFIG_ZSIGNING_DEFERRED=y

Transfer the three files to the signing job and keep their paths relative to
the application build directory. Then sign.

.. code-block:: sh

   west zkeyring sign --build-dir build --profile release \
       --output firmware.signed.bin

Run the job from a workspace at the same manifest revision. Without a west
workspace, install the standalone command and the dependencies of the same
MCUboot revision, and pass its imgtool.

.. code-block:: sh

   python -m pip install "git+<zkeyring repository>@<commit>"
   zkeyring sign --build-dir build --profile release \
       --imgtool <mcuboot>/scripts/imgtool.py --output firmware.signed.bin

Give credentials to the protected signing job only. Use distinct development
and release keys, and build the release bootloader with the release public key.

Flash the signed image
----------------------

.. warning::

   zkeyring produces a signed ``.bin`` file. Some runners flash
   ``zephyr.hex`` instead, which is **not signed**. This includes the Nordic
   runners, and STM32CubeProgrammer by default. Check ``hex_file`` and
   ``bin_file`` in ``zephyr/runners.yaml`` and do not rely on a plain
   ``west flash`` with these runners.

Flash MCUboot, then write ``zephyr.signed.bin`` at the address of
``slot0_partition``. With STM32CubeProgrammer, ``--download-address`` selects
the ``.bin`` file.

.. code-block:: sh

   west flash --domain mcuboot
   west flash --domain <application> --download-address <slot0 address>

.. _unsupported:

Unsupported configurations
--------------------------

The build stops when one of these is enabled:

- image encryption;
- direct XIP, RAM load and firmware updater modes;
- pure signatures and SHA-512;
- confirmed or unsigned image generation;
- extra imgtool arguments;
- vendor or class UUIDs.
