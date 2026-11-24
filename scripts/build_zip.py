"""Build "dist/PDF Cleaner.zip": the cleaner, its profiles and the launchers.

Usage: python3 scripts/build_zip.py
"""

import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "PDF Cleaner.zip"
TOP = "PDF Cleaner"
EXECUTABLE = {"Start PDF Cleaner.command", "Start PDF Cleaner.sh"}


def add(zf: zipfile.ZipFile, src: Path, arc: str) -> None:
    info = zipfile.ZipInfo.from_file(src, arc)
    mode = 0o755 if src.name in EXECUTABLE else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16  # keep the launchers executable on Mac and Linux
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, src.read_bytes())


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    files: list[tuple[Path, str]] = []
    for p in sorted((ROOT / "launcher").iterdir()):
        if p.is_file():
            files.append((p, f"{TOP}/{p.name}"))
    for p in sorted((ROOT / "pdf_cleaner").rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".yaml"):
            files.append((p, f"{TOP}/{p.relative_to(ROOT).as_posix()}"))
    with zipfile.ZipFile(OUT, "w") as zf:
        for src, arc in files:
            add(zf, src, arc)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(files)} files)")


if __name__ == "__main__":
    main()
