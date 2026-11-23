# syntax=docker/dockerfile:1

# The released base supplies the non-root dev user (UID/GID 1000), Git, and
# the /workspace working directory used by Dark Factory projects.
FROM node:24-trixie-slim@sha256:af02d57b57719b9657a45199a79264a9437940cd55b3c7b3fc99615654f124a0 AS node
FROM ghcr.io/astral-sh/uv:0.9.25@sha256:13e233d08517abdafac4ead26c16d881cd77504a2c40c38c905cf3a0d70131a6 AS uv

FROM ghcr.io/prskr/dark-factory/claude-project@sha256:39ea9f86bfe4c1e2768e94f8a0fe456d4474f7b9cd036adcf9ccf74e14448b1d

USER root

COPY --from=node /usr/local /usr/local
COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ansible python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
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
    && npm install --global @fission-ai/openspec@1.16.0 \
    && npm cache clean --force

USER 1000:1000
WORKDIR /workspace
