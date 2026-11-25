zkeyring
========

**Sign Zephyr firmware for MCUboot without putting the private key in the
build.** The build only needs a public key. The private key stays in the store
you choose, from a developer's OS keychain to a hardware security module.

.. raw:: html
   :file: diagrams/flow.svg

.. grid:: 1 2 2 2
   :gutter: 3

   .. grid-item-card:: Get started
      :link: getting-started
      :link-type: doc

      Sign a firmware image and boot it in QEMU with a key from your OS
      keyring.

   .. grid-item-card:: Understand the concept
      :link: concepts
      :link-type: doc

      What zkeyring trusts, what it checks, and where the private key lives.

   .. grid-item-card:: Choose a backend
      :link: backends/index
      :link-type: doc

      Compare the nine backends and follow the setup guide for yours.

   .. grid-item-card:: Integrate with your build
      :link: integration
      :link-type: doc

      Kconfig, sysbuild, signing in a separate CI job, and flashing.

.. toctree::
   :hidden:
   :caption: Guide

   getting-started
   concepts
   integration

.. toctree::
   :hidden:
   :caption: Backends

   backends/index
   backends/local-keyring
   backends/sops
   backends/environment
   backends/gitlab-secrets
   backends/vault-kv
   backends/vault-transit
   backends/openbao
   backends/infisical
   backends/pkcs11

.. toctree::
   :hidden:
   :caption: Reference

   configuration
   cli
   troubleshooting
