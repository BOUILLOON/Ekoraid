#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
if [[ ! -x .venv/bin/python ]]; then
  printf '%s\n' 'Installation manquante : .venv/bin/python.' >&2
  exit 1
fi
exec .venv/bin/python -m playlist25 stop
