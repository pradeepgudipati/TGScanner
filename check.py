#!/usr/bin/env python3
"""Run ruff, black --check, and isort --check-only. Exit non-zero if any fail."""

import subprocess
import sys


def run(cmd: list[str], name: str) -> bool:
    r = subprocess.run(cmd, cwd=".")
    if r.returncode != 0:
        print(f"[FAIL] {name} (exit {r.returncode})", file=sys.stderr)
        return False
    print(f"[OK] {name}")
    return True


def main() -> int:
    ok = True
    ok &= run([sys.executable, "-m", "ruff", "check", "."], "ruff")
    ok &= run([sys.executable, "-m", "black", "--check", "."], "black")
    ok &= run([sys.executable, "-m", "isort", "--check-only", "."], "isort")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
