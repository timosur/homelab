#!/usr/bin/env bash
# Publish the board-member template and enforce its 30-minute inactivity policy.
set -euo pipefail

template_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

coder templates push board-member --directory "$template_dir" --yes
coder templates edit board-member --default-ttl 30m --activity-bump 30m
