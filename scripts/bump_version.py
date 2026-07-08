#!/usr/bin/env python3
"""Bump the project version. Usage: bump_version.py {major|minor|patch}.

The root VERSION file is the single source of truth. Pre-launch we read the parts as:
major = 0 until launch, minor = the phase we're in, patch = changes within a phase. The conductor
package version in pyproject.toml is kept in sync so the built image's metadata (and thus
conductor.__version__ inside the container) agrees with VERSION.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
PYPROJECT = ROOT / "conductor" / "pyproject.toml"


def bump(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit(f"unknown part: {part!r} (use major|minor|patch)")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: bump_version.py {major|minor|patch}")
    current = VERSION_FILE.read_text().strip()
    new = bump(current, sys.argv[1])

    VERSION_FILE.write_text(f"{new}\n")
    PYPROJECT.write_text(
        re.sub(
            r'^version = "[^"]+"',
            f'version = "{new}"',
            PYPROJECT.read_text(),
            count=1,
            flags=re.M,
        )
    )
    print(f"{current} -> {new}")


if __name__ == "__main__":
    main()
