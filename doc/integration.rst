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

The command and the build run the zkeyring sources of the workspace, at the
revision selected by the manifest.

Enable signing
--------------

Add this to the application's ``prj.conf``.

.. code-block:: cfg

   CONFIG_ZSIGNING=y

Enable MCUboot with an ECDSA P-256 key in ``sysbuild.conf``.

.. code-block:: cfg

   SB_CONFIG_BOOTLOADER_MCUBOOT=y
   SB_CONFIG_BOOT_SIGNATURE_TYPE_ECDSA_P256=y

Build with the **public** key.

.. code-block:: sh

   west build -b <board> --sysbuild <application> -- \
       "-DSB_CONFIG_BOOT_SIGNATURE_KEY_FILE=\"$PWD/signing-public.pem\"" \
       -DZKEYRING_PROFILE=dev

.. important::

   Without ``SB_CONFIG_BOOT_SIGNATURE_KEY_FILE``, sysbuild selects MCUboot's
   example private key, and zkeyring stops with *requires a public-only MCUboot
   PEM*. An application can set its own default in ``Kconfig.sysbuild``:

   .. code-block:: kconfig

      config BOOT_SIGNATURE_KEY_FILE
      	string
      	default "$(APP_DIR)/keys/dev-public.pem" if BOOT_SIGNATURE_TYPE_ECDSA_P256

      source "share/sysbuild/Kconfig"

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
     - Profile file, relative to the application. Default: ``zkeyring.toml``
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

.. _release-configuration:

Development and release configurations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Zephyr's ``FILE_SUFFIX`` can switch the profile and the MCUboot public key
together. Select the profile in the application configuration.

.. code-block:: cfg
   :caption: prj.conf

   CONFIG_ZSIGNING=y
   CONFIG_ZSIGNING_PROFILE="dev"

.. code-block:: cfg
   :caption: prj_release.conf

   CONFIG_ZSIGNING=y
   CONFIG_ZSIGNING_PROFILE="release"

Select the public key in the sysbuild configuration.

.. code-block:: kconfig
   :caption: Kconfig.sysbuild

   config RELEASE_KEY
   	bool "Trust the release key"

   config BOOT_SIGNATURE_KEY_FILE
   	string
   	default "$(APP_DIR)/keys/release-public.pem" if RELEASE_KEY
   	default "$(APP_DIR)/keys/dev-public.pem"

   source "share/sysbuild/Kconfig"

.. code-block:: cfg
   :caption: sysbuild.conf

   SB_CONFIG_BOOTLOADER_MCUBOOT=y
   SB_CONFIG_BOOT_SIGNATURE_TYPE_ECDSA_P256=y

.. code-block:: cfg
   :caption: sysbuild_release.conf

   SB_CONFIG_BOOTLOADER_MCUBOOT=y
   SB_CONFIG_BOOT_SIGNATURE_TYPE_ECDSA_P256=y
   SB_CONFIG_RELEASE_KEY=y

With a suffix, the ``_release`` files replace the default files. Repeat the
common options in both, then build.

.. code-block:: sh

   west build -b <board> -p --sysbuild -d build
   west build -b <board> -p --sysbuild -d build-release -- -DFILE_SUFFIX=release

An extra file that only sets ``CONFIG_ZSIGNING_PROFILE``, such as
``-DEXTRA_CONF_FILE=release.conf``, changes the signing key but not the key that
MCUboot accepts. If they differ, signing stops.

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
