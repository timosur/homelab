# syntax=docker/dockerfile:1

# Keep the toolchain bases pinned so published workspace-image digests are
# reproducible and can be promoted into the Coder template unchanged.
FROM node:24-trixie-slim@sha256:54f84b4cf8432b21aaffd5d3ce8d41e12cea0d10d33c09d2f3c9879d49d6b446 AS node
FROM ghcr.io/astral-sh/uv:0.9.25@sha256:13e233d08517abdafac4ead26c16d881cd77504a2c40c38c905cf3a0d70131a6 AS uv

FROM debian:trixie-slim

ARG TARGETARCH

USER root

COPY --from=node /usr/local /usr/local
COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ansible \
        ca-certificates \
        curl \
        git \
        openssh-client \
        openssh-server \
        python3 \
        python3-venv \
        sudo \
    && rm -rf /var/lib/apt/lists/* \
    && test "$TARGETARCH" = amd64 \
    && curl --fail --location --silent --show-error \
        --output /usr/local/bin/kubectl \
        https://dl.k8s.io/release/v1.36.1/bin/linux/amd64/kubectl \
    && echo '629d3f410e09bf49b64ae7079f7f0bda1191efed311f7d37fdbab0ad5b0ec2b7  /usr/local/bin/kubectl' | sha256sum --check \
    && chmod 0755 /usr/local/bin/kubectl \
    && curl --fail --location --silent --show-error \
        --output /tmp/helm.tar.gz \
        https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz \
    && echo '0403a88ae20913d11b10a2467b50087721fea7581736da6b9d3992f5357fe54b  /tmp/helm.tar.gz' | sha256sum --check \
    && tar --extract --gzip --file /tmp/helm.tar.gz --strip-components=1 --directory /usr/local/bin linux-amd64/helm \
    && rm /tmp/helm.tar.gz \
    && curl --fail --location --silent --show-error \
        --output /tmp/chezmoi.tar.gz \
        https://github.com/twpayne/chezmoi/releases/download/v2.74.0/chezmoi_2.74.0_linux_amd64.tar.gz \
    && echo '1b98a567f726f41540414aea4caf912699d53e248b29ccf8369cf553657b2374  /tmp/chezmoi.tar.gz' | sha256sum --check \
    && tar --extract --gzip --file /tmp/chezmoi.tar.gz --directory /usr/local/bin chezmoi \
    && rm /tmp/chezmoi.tar.gz \
    && npm install --global @fission-ai/openspec@1.16.0 \
    && npm cache clean --force \
    && groupadd --gid 1000 coder \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash coder \
    && echo 'coder ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/coder \
    && chmod 0440 /etc/sudoers.d/coder \
    && install --directory --owner=coder --group=coder --mode=0755 /workspace /run/sshd

USER coder
WORKDIR /home/coder
