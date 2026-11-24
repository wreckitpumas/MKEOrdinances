"""Start the PDF Cleaner browser page.

The double-click launchers call this file. On first run on a computer it
creates a private Python environment ON THAT COMPUTER (not in this folder, so
the folder can live on a flash drive), installs the required packages, and then
starts the page. Later runs start right away.

Everything the installer prints is saved to "PDF Cleaner setup log.txt" next to
this file, so a failed setup can be diagnosed.
"""

import hashlib
import os
import platform
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE_REQS = HERE / "app requirements.txt"
OPTIONAL_REQS = HERE / "optional requirements.txt"
LOG = HERE / "PDF Cleaner setup log.txt"


def data_dir() -> Path:
    """Per-computer, per-user folder for the private environment."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "PDF Cleaner"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PDF Cleaner"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "pdf-cleaner"


def env_dir() -> Path:
    # One environment per Python installation, so two Pythons never collide.
    tag = hashlib.sha256(sys.executable.encode()).hexdigest()[:8]
    ver = f"py{sys.version_info.major}{sys.version_info.minor}"
    return data_dir() / f"env-{ver}-{tag}"


def env_python(env: Path) -> Path:
    return env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def log(msg: str) -> None:
    print(msg, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def run_logged(cmd: list[str]) -> int:
    """Run a command, show its output, and append it to the setup log."""
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("\n$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd) + "\n")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        for line in proc.stdout:
            sys.stdout.write(line)
            fh.write(line)
        return proc.wait()


def stop(msg: str) -> None:
    log("\n" + "=" * 70 + "\n" + msg + f"\n\nThe full details are in: {LOG}\n" + "=" * 70)
    sys.exit(1)


def stamp_for(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def setup(env: Path) -> None:
    py = env_python(env)
    if not py.exists():
        log(f"First run on this computer: setting up PDF Cleaner in\n  {env}\nThis takes a minute or two and needs internet once.")
        try:
            env.parent.mkdir(parents=True, exist_ok=True)
            venv.EnvBuilder(with_pip=True, clear=True).create(env)
        except Exception as e:  # e.g. Debian/Ubuntu without python3-venv
            stop(
                f"Could not create the Python environment: {e}\n\n"
                "On Ubuntu or Debian, install it with:  sudo apt install python3-venv\n"
                "Otherwise reinstall Python from https://www.python.org/downloads/"
            )
    core_stamp = env / "core.sha256"
    if not core_stamp.exists() or core_stamp.read_text() != stamp_for(CORE_REQS):
        log("Updating the package installer (pip) ...")
        # A current pip uses the computer's own certificates, which fixes most
        # SSL errors on work networks. Not fatal if it fails.
        run_logged([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip"])
        log("Installing the required packages ...")
        rc = run_logged(
            [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--prefer-binary", "-r", str(CORE_REQS)]
        )
        if rc != 0:
            stop(
                "Installing the required packages failed.\n\n"
                "Common causes:\n"
                " - No internet, or a work network or firewall blocking python.org / pypi.org.\n"
                "   Try another network (for example a phone hotspot) once; after that no internet is needed.\n"
                " - An SSL or certificate error on a work network: ask IT to allow pypi.org and\n"
                "   files.pythonhosted.org, or try another network once.\n"
                f" - A very new or unusual Python ({platform.python_version()}). Python 3.12 or 3.13 from\n"
                "   https://www.python.org/downloads/ is the safest choice."
            )
        core_stamp.write_text(stamp_for(CORE_REQS))
    opt_stamp = env / "optional.sha256"
    if OPTIONAL_REQS.exists() and (not opt_stamp.exists() or opt_stamp.read_text() != stamp_for(OPTIONAL_REQS)):
        log("Installing the optional Claude package (only needed for 'Use Claude for flagged tables') ...")
        rc = run_logged(
            [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--prefer-binary", "-r", str(OPTIONAL_REQS)]
        )
        if rc == 0:
            opt_stamp.write_text(stamp_for(OPTIONAL_REQS))
        else:
            log(
                "\nNote: the optional Claude package did not install. PDF Cleaner works normally;\n"
                "only the 'Use Claude for flagged tables' option is unavailable. Setup will retry it next time.\n"
            )


def main() -> None:
    LOG.write_text(
        f"PDF Cleaner setup log\nPython {platform.python_version()} at {sys.executable}\n"
        f"System: {platform.platform()}\nFolder: {HERE}\n",
        encoding="utf-8",
    )
    if sys.version_info < (3, 10):
        stop(
            f"PDF Cleaner needs Python 3.10 or newer. This computer has Python {platform.python_version()}.\n"
            "Install a current Python from https://www.python.org/downloads/ and try again."
        )
    env = env_dir()
    setup(env)
    os.chdir(HERE)  # so a .env file and the API cache live next to the launcher
    log("Starting PDF Cleaner. Your browser will open. Close this window (or press Ctrl+C) to stop.")
    env_vars = dict(os.environ, PYTHONPATH=str(HERE), PYTHONDONTWRITEBYTECODE="1")
    args = [str(env_python(env)), "-m", "pdf_cleaner.web"] + sys.argv[1:]
    sys.exit(subprocess.call(args, cwd=HERE, env=env_vars))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
