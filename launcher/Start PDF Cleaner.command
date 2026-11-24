#!/bin/bash
# macOS launcher: double-click to start PDF Cleaner.
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 launch.py "$@"
else
  echo
  echo "Python 3.10 or newer is required. Install it from https://www.python.org/downloads/"
fi
echo
read -r -p "Press Return to close this window."
