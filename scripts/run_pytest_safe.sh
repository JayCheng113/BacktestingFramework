#!/bin/zsh

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(cd -- "${script_dir}/.." && pwd)
shim_dir="${repo_root}/tools/pytest_shim"

export PYTHONPATH="${shim_dir}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

exec python3 -X faulthandler -m pytest -p pytest_asyncio.plugin "$@"
