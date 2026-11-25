#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

set -eu
workspace=$(mktemp -d)
mkdir -p "$workspace/zkeyring"
cd "$workspace/zkeyring"
cp -R /source/cmake /source/samples /source/tests /source/scripts /source/sysbuild /source/zephyr .
cp /source/CMakeLists.txt /source/Kconfig /source/LICENSE /source/README.md \
    /source/pyproject.toml /source/west.yml /source/west-dev.yml .
west init -l --mf west-dev.yml .
west update --narrow -o=--depth=1
python3 -m pip install -e '.[pkcs11]' -r ../bootloader/mcuboot/scripts/requirements.txt
# Python lint and formatting are checked by ruff in the lint workflow.
# Run from the Zephyr tree to use its .checkpatch.conf.
(cd ../zephyr && scripts/checkpatch.pl --no-tree --file ../zkeyring/samples/signed_firmware/src/main.c)
export XDG_DATA_HOME="$workspace/keyring-data"
XDG_RUNTIME_DIR=$(mktemp -d)
export XDG_RUNTIME_DIR
export ZKEYRING_TEST_PKCS11_MODULE=/usr/lib/softhsm/libsofthsm2.so
export ZKEYRING_TEST_NATIVE_KEYRING=1
# The inner shell expands these variables inside the D-Bus session.
# shellcheck disable=SC2016
dbus-run-session -- sh -ec '
    printf "%s" "$(openssl rand -hex 16)" | gnome-keyring-daemon --components=secrets --unlock
    exec python3 -m pytest -q --tb=short tests/signing
'
