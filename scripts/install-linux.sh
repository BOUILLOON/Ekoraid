#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
python_command="${PLAYLIST_PYTHON:-python3}"
"$python_command" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Python 3.12 ou plus est requis.")'
if [[ ! -x .venv/bin/python ]]; then
  "$python_command" -m venv .venv
fi
.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Le dossier .venv existant nécessite Python 3.12 ou plus.")'
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  .venv/bin/python -m ensurepip --upgrade
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python -m playlist25 init
if ! .venv/bin/python -m playlist25 doctor; then
  printf '%s\n' 'La collecte est installée. Les diagnostics indiquent les prérequis audio/YouTube encore manquants.'
fi
if command -v node >/dev/null 2>&1; then
  node -e 'if (Number(process.versions.node.split(".")[0]) < 20) { console.error("Node.js 20 ou plus est requis pour YouTube."); process.exit(1); }' || true
fi
printf '%s\n' 'Mot de passe : .venv/bin/python -m playlist25 configure-admin' 'Lancement : bash scripts/start.sh'
