#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ -x .venv/bin/python ]; then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else
  python3 -m venv .venv
  PY=.venv/bin/python
fi
"$PY" scripts/setup_env.py
"$PY" -m pip install -q -r requirements.txt
exec "$PY" -m app
