#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${NINEPLUS_VENV_DIR:-${SCRIPT_DIR}/.venv}"
NINEBOT_CONFIG_DIR="${NINEBOT_CLI_CONFIG:-${SCRIPT_DIR}/ninebot-config}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    printf 'Error: %s was not found. Install python3 and python3-venv first.\n' "${PYTHON_BIN}" >&2
    exit 1
fi

"${PYTHON_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r "${SCRIPT_DIR}/requirements.txt"

install -d -m 700 "${NINEBOT_CONFIG_DIR}"

"${VENV_DIR}/bin/python" -m ninecli --help >/dev/null
"${VENV_DIR}/bin/python" -m compileall -q \
    "${SCRIPT_DIR}/main.py" \
    "${SCRIPT_DIR}/adapters" \
    "${SCRIPT_DIR}/services"

printf 'NinePlus Server dependencies installed.\n'
printf 'Virtual environment: %s\n' "${VENV_DIR}"
printf 'Ninebot config: %s\n' "${NINEBOT_CONFIG_DIR}"
printf 'Next: log in to ninecli, configure NINEPLUS_API_KEY, then start the service.\n'
