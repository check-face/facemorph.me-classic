#!/bin/sh
# Records how requirements-cpu.lock and requirements-cu128.lock were made (24 October 2026, uv 0.12.21).
# The distribution is frozen: re-running this resolves today's transitive versions, so do it only for
# the one allowed unfreeze (a new GPU generation the torch/CUDA pin does not cover). One lock per torch
# index serves amd64 and arm64: it lists every wheel hash of each pinned version.
set -eu
cd "$(dirname "$0")"
for index in cpu cu128; do
  uv pip compile --python-version 3.11 --python-platform x86_64-manylinux_2_28 --generate-hashes \
    --index-url "https://download.pytorch.org/whl/$index" --extra-index-url https://pypi.org/simple \
    --index-strategy unsafe-best-match --custom-compile-command ./lock-requirements.sh \
    requirements-torch.txt requirements.txt -o "requirements-$index.lock"
done
