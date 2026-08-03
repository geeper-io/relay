#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
python_bin="$project_root/.venv/bin/python"

if [ ! -x "$python_bin" ]; then
  python_bin=$(command -v python3 || true)
fi

if [ -z "$python_bin" ]; then
  echo "Python 3 was not found. Create the project virtual environment first." >&2
  exit 1
fi

if ! "$python_bin" -c 'import sqlalchemy, uvicorn' >/dev/null 2>&1; then
  echo "Relay dependencies are missing. Install requirements.txt in .venv first." >&2
  exit 1
fi

exec "$python_bin" "$script_dir/admin_demo.py" "$@"
