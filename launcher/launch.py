"""Start the PDF Cleaner browser page.

The double-click launchers call this file. On first run it creates a private
Python environment in ".venv" next to this file and installs the packages in
"app requirements.txt" (needs internet once). Later runs start right away.
"""

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
REQS = HERE / "app requirements.txt"
STAMP = VENV / "installed.sha256"


def fail(msg: str) -> None:
    print("\n" + msg + "\n")
    sys.exit(1)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main() -> None:
    if sys.version_info < (3, 10):
        fail(
            f"PDF Cleaner needs Python 3.10 or newer. This is Python {sys.version.split()[0]}.\n"
            "Install a current Python from https://www.python.org/downloads/ and try again."
        )
    os.chdir(HERE)  # so a .env file and the API cache live next to the launcher
    want = hashlib.sha256(REQS.read_bytes()).hexdigest()
    if not venv_python().exists():
        print("First run: setting up PDF Cleaner (one time, needs internet) ...")
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)
    if not STAMP.exists() or STAMP.read_text().strip() != want:
        print("Installing packages ...")
        cmd = [str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQS)]
        if subprocess.call(cmd) != 0:
            fail("Package installation failed. Check your internet connection and run the launcher again.")
        STAMP.write_text(want)
    print("Starting PDF Cleaner. Your browser will open. Close this window (or press Ctrl+C) to stop.")
    args = [str(venv_python()), "-m", "pdf_cleaner.web"] + sys.argv[1:]
    sys.exit(subprocess.call(args, cwd=HERE))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
