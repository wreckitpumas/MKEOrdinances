"""Load per-source profiles (pdf_cleaner/profiles/*.yaml).

A profile holds every layout-dependent rule for one source document: header and
footer patterns, heading patterns, history note patterns, and table settings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROFILE_DIR = Path(__file__).parent / "profiles"


@dataclass
class Profile:
    name: str
    raw: dict
    path: Path
    match_filenames: list[re.Pattern] = field(default_factory=list)

    # Convenience accessors with defaults so a minimal profile still works.
    def section(self, key: str) -> dict:
        val = self.raw.get(key) or {}
        return val if isinstance(val, dict) else {}

    def patterns(self, section: str, key: str = "patterns") -> list[re.Pattern]:
        return [re.compile(p) for p in (self.section(section).get(key) or [])]

    def get(self, key: str, default=None):
        return self.raw.get(key, default)


def load_profile(path: Path) -> Profile:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    name = raw.get("name") or path.stem
    return Profile(
        name=name,
        raw=raw,
        path=path,
        match_filenames=[re.compile(p) for p in raw.get("match_filenames") or []],
    )


def all_profiles(profile_dir: Path = PROFILE_DIR) -> list[Profile]:
    return [load_profile(p) for p in sorted(profile_dir.glob("*.yaml"))]


def resolve_profile(pdf: Path, explicit: str | None = None) -> Profile:
    """Pick a profile by explicit name or path, else by filename match."""
    if explicit:
        p = Path(explicit)
        if p.suffix in (".yaml", ".yml") and p.exists():
            return load_profile(p)
        candidate = PROFILE_DIR / f"{explicit}.yaml"
        if candidate.exists():
            return load_profile(candidate)
        raise SystemExit(f"Profile not found: {explicit}")
    for prof in all_profiles():
        if any(rx.search(pdf.name) for rx in prof.match_filenames):
            return prof
    raise SystemExit(
        f"No profile matches {pdf.name!r}. Pass --profile <name or path> "
        f"(available: {', '.join(p.name for p in all_profiles())})."
    )
