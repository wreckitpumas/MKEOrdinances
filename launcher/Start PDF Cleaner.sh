#!/bin/bash
# Linux launcher: run ./"Start PDF Cleaner.sh" (or double-click if your file manager allows).
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  exec python3 launch.py "$@"
else
  echo "Python 3.10 or newer is required. Install python3 and python3-venv with your package manager."
  exit 1
fi
