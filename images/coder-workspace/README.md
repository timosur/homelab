# Coder workspace image

This Debian-based image supplies the non-root `coder` user (UID/GID `1000`) for the personal
Coder workspace. It deliberately contains no personal credentials, Kubernetes configuration, or
Git configuration.

The image provides APT with passwordless `sudo`, OpenSSH, Git, Python and uv, Node.js and
OpenSpec, kubectl (including `kubectl kustomize`), Helm, Ansible, and Chezmoi. Packages installed
interactively with APT are disposable: rebuild the image for durable tool changes.

The build workflow publishes amd64 images to `ghcr.io/timosur/homelab/coder-workspace`. After a
reviewed main-branch publish, copy the reported immutable digest into the Coder template; never
use its mutable `latest` tag there.
