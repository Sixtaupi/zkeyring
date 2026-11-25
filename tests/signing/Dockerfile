# SPDX-License-Identifier: Apache-2.0

ARG ZKEYRING_ZEPHYR_IMAGE
FROM ghcr.io/getsops/sops:v3.11.0@sha256:6ce00ac946e1a52b2d1e5b7087441f7b6ef7bad907753104013f8bdb8f5fd891 AS sops
FROM ${ZKEYRING_ZEPHYR_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        age dbus gnome-keyring qemu-system-arm softhsm2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=sops /usr/local/bin/sops /usr/local/bin/sops
ENV CMAKE_BUILD_PARALLEL_LEVEL=4
