#!/usr/bin/env python3
"""Read-only formatter checks against an installed Glosso standard library."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

from environment import config_environment
from integration import BINARY, STD, significant


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--std", type=Path, default=STD)
    parser.add_argument("--formatter", type=Path, default=BINARY)
    args = parser.parse_args()
    paths = sorted(args.std.rglob("*.glo"))
    if not paths:
        parser.error(f"no .glo files found in {args.std}")
    with tempfile.TemporaryDirectory(prefix="glofmt-corpus-") as directory:
        environment, _ = config_environment(directory)
        return check_corpus(args, paths, environment)


def check_corpus(args, paths, environment):
    failures = []
    changed = 0
    for index, path in enumerate(paths, 1):
        original = path.read_bytes()
        command = [str(args.formatter.resolve()), '--stdout', f"--std={args.std.resolve()}",
                   f"--stdin-filepath={path.resolve()}"]
        first = subprocess.run(command, input=original, env=environment,
                               capture_output=True, timeout=60)
        if first.returncode:
            failures.append((path, f"formatter exited {first.returncode}: {first.stderr!r}"))
            continue
        if significant(original) != significant(first.stdout):
            failures.append((path, "significant tokens changed"))
        second = subprocess.run(command, input=first.stdout, env=environment,
                                capture_output=True, timeout=60)
        if second.returncode or first.stdout != second.stdout:
            failures.append((path, "not idempotent"))
        changed += first.stdout != original
        if index % 50 == 0:
            print(f"Checked {index}/{len(paths)} files...", flush=True)
    for path, reason in failures:
        print(f"FAIL {path}: {reason}")
    print(f"Checked {len(paths)} files; {changed} reformatted; {len(failures)} failures.")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
