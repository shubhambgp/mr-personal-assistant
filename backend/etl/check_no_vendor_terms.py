"""Fails if any confidential vendor identifier appears anywhere in the tree.

WHY THE HASHES. The obvious way to write this check is a list of forbidden
words. That is what the previous version did — and it meant the committed,
public file spelled out in plaintext exactly the name it existed to keep out.
The check leaked the thing it was checking for.

So the terms are stored as SHA-256 of the lowercased token. This script
tokenises every text file, hashes each token, and compares. Same coverage, and
the repository never contains the plaintext.

This is not cryptographic secrecy — a short dictionary word is brute-forceable
by anyone who already has a candidate list. It is about not *publishing* a
client's name in a public repository, which is the actual requirement.

    python -m etl.check_no_vendor_terms [root]
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

# Overridable so the test suite can point at its own hash file and verify the
# mechanism with a sentinel term — otherwise the test would have to contain the
# real plaintext, which is the exact leak this whole module exists to avoid.
HASHES_FILE = Path(
    os.environ.get("FORBIDDEN_HASHES")
    or Path(__file__).resolve().parent / "forbidden_hashes.txt"
)

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache",
}
SKIP_SUFFIXES = {
    ".parquet", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
    ".pdf", ".zip", ".duckdb", ".woff", ".woff2", ".ttf",
}
# Lockfiles are enormous and contain only package names.
SKIP_NAMES = {"package-lock.json", "forbidden_hashes.txt"}

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")


def load_hashes() -> set[str]:
    if not HASHES_FILE.exists():
        sys.exit(f"{HASHES_FILE} missing — cannot verify.")
    return {
        line.split("#")[0].strip()
        for line in HASHES_FILE.read_text().splitlines()
        if line.split("#")[0].strip()
    }


def digest(token: str) -> str:
    return hashlib.sha256(token.lower().encode()).hexdigest()


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    # A gate that did not actually run must never report PASS. Both of these
    # were reachable: `--root ..` (this script takes a positional path, not a
    # flag) resolved to a directory named "--root", rglob yielded nothing, and
    # the script printed "scanned 0 text files … PASS".
    if not root.is_dir():
        sys.exit(f"{root} is not a directory — this script takes a positional path, not a flag.")
    forbidden = load_hashes()

    hits: list[str] = []
    scanned = 0

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES or path.name in SKIP_NAMES:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        scanned += 1
        for match in TOKEN.finditer(text):
            if digest(match.group(0)) in forbidden:
                # Report the location, never the matched token itself — printing
                # it would reintroduce the leak into CI logs.
                line = text.count("\n", 0, match.start()) + 1
                hits.append(f"{path.relative_to(root)}:{line}")

    print(f"scanned {scanned} text files under {root}")
    if scanned == 0:
        print("FAIL — scanned nothing, so this proves nothing.")
        return 1
    if hits:
        print(f"FAIL — a forbidden identifier appears at {len(hits)} location(s):")
        for hit in sorted(set(hits))[:50]:
            print(f"  {hit}")
        return 1
    print("PASS — no forbidden identifier found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
