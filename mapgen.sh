#!/bin/sh
# Runs mapgen with the local venv; working directory is tools/mapgen.
cd "$(dirname "$0")" || exit 1
if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else PY=.venv/bin/python; fi
exec "$PY" -m mapgen "$@"
