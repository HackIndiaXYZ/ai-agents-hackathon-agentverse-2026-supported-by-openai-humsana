#!/usr/bin/env python3
"""The pre-push gate: prove that what we are about to publish carries no credential.

    python3 scripts/preflight.py

Run this before every push. Six checks, and exit 0 means push:

  1. a key shape in a tracked file, staged or not
  2. a key shape anywhere in the history of every branch and tag
  3. the value of a key that exists on this machine, by value, in a tracked file
  4. the same values, by value, in the history
  5. .env is not tracked, and a .env on disk is ignored
  6. the offline suite passes with every key removed from the environment, which is what a reviewer
     runs and is the promise the README makes

Checks 3 and 4 are the ones that do not care what a key looks like. A pattern only catches the
providers someone thought to write a pattern for, so these take the actual values from the
environment, from this repository's `.env`, and from the key files a developer has here, and look for
them in what would be published.

The key files outside the repository are read here and not in the test suite, on purpose: the suite
has to behave the same in a reviewer's clone, where those files do not exist.

A check that could not run is reported as a failure, never as a pass. The first version of this file
treated a failed history scan as an empty one, which is the same output as a clean history.

Nothing prints a value. A finding names the file, the commit, and where the value came from.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_no_secrets import (FAILED, NO_HISTORY, PLACEHOLDERS,  # noqa: E402
                                   RAN, SECRET_SHAPES, MIN_CREDENTIAL_LENGTH, history,
                                   parse_grep, run_git, scan_history, tracked_files)

# Key files a developer has on this machine. Missing files are skipped.
OUTSIDE_KEY_FILES = [pathlib.Path.home() / ".hermes" / ".env", pathlib.Path.home() / ".env"]

KEYISH = ("_API_KEY", "_KEY", "_TOKEN", "_SECRET")

NOT_A_CREDENTIAL = {"HERMES_SESSION_KEY", "TERMINAL_DOCKER_SHARED_CONTAINER_KEY", "GPG_KEY"}


def values_in(path: pathlib.Path, label: str) -> dict[str, str]:
    """The keys in one file, by name. The values never leave this dict."""
    found: dict[str, str] = {}
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip().strip("\"'")
        if name.upper().endswith(KEYISH) and len(value) >= MIN_CREDENTIAL_LENGTH:
            if value.lower() not in PLACEHOLDERS:
                found[f"{label}:{name}"] = value
    return found


def local_keys() -> dict[str, str]:
    found: dict[str, str] = {}
    for name, value in os.environ.items():
        if not name.upper().endswith(KEYISH) or len(value) < MIN_CREDENTIAL_LENGTH:
            continue
        if name.upper() in NOT_A_CREDENTIAL:
            continue
        found[f"environment:{name}"] = value
    for path in [ROOT / ".env", *OUTSIDE_KEY_FILES]:
        try:
            label = str(path.relative_to(pathlib.Path.home()))
            label = f"~/{label}"
        except ValueError:
            label = str(path)
        found.update(values_in(path, label))
    return found


def main() -> int:
    files = tracked_files()
    revisions = history()
    keys = local_keys()
    failures: list[str] = []

    print("preflight: what a push would publish")
    print(f"  tracked files                  {len(files)}")
    print(f"  commits reachable from refs    {len(revisions)}")
    print(f"  keys reachable on this machine {len(keys)}: {', '.join(sorted(keys)) or 'none'}")
    print("  (names only, no value is ever printed)")
    print()

    def report(number: int, title: str, findings: list[str], detail: str = "") -> None:
        status = "ok" if not findings else "FAIL"
        print(f"check {number}  {title:<44} {status}{('  ' + detail) if detail else ''}")
        for finding in findings[:5]:
            print(f"         {finding}")

    # 1. shapes, in the tracked files
    shape_hits = []
    for name in files:
        path = ROOT / name
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        for label, shape in SECRET_SHAPES.items():
            if shape.search(body):
                shape_hits.append(f"{name} carries {label}")
    report(1, "a key shape in a tracked file", shape_hits)
    failures += shape_hits

    # 2. shapes, in the history. A scan that could not run is a finding, not a pass.
    history_hits = []
    for label, shape in SECRET_SHAPES.items():
        state, hits = scan_history(shape.pattern)
        if state == FAILED:
            history_hits.append(f"the history scan for {label} could not run, so it is not evidence")
        else:
            history_hits += [f"{label} at {hit}" for hit in hits]
    report(2, "a key shape anywhere in history", history_hits,
           "(no history here)" if not revisions else "")
    failures += history_hits

    # 3. the actual values, in the tracked files
    value_hits = []
    for source, value in keys.items():
        for name in files:
            path = ROOT / name
            if not path.is_file():
                continue
            if value in path.read_text(encoding="utf-8", errors="replace"):
                value_hits.append(f"the key from {source} appears in {name}")
    report(3, "a key from this machine in a tracked file", value_hits,
           f"({len(keys)} keys checked)")
    failures += value_hits

    # 4. the actual values, in the history
    value_history = []
    for source, value in keys.items():
        if not revisions:
            break
        ran, out = run_git("grep", "-I", "-n", "-F", "-e", value, *revisions)
        if not ran:
            value_history.append(f"the history scan for the key from {source} could not run")
            continue
        value_history += [f"the key from {source} appears at {hit}"
                          for hit in parse_grep(out.splitlines())]
    report(4, "a key from this machine in history", value_history,
           f"({len(keys)} keys checked)")
    failures += value_history

    # 5. the plumbing
    plumbing = []
    if ".env" in files:
        plumbing.append(".env is tracked by git")
    if (ROOT / ".env").exists():
        ran, _ = run_git("check-ignore", "-q", ".env", timeout=30)
        if not ran:
            plumbing.append("a .env on disk is not ignored by git")
    report(5, ".env not tracked, and ignored", plumbing)
    failures += plumbing

    # 6. the suite a reviewer will run, with no keys at all
    clean = {name: value for name, value in os.environ.items()
             if not name.upper().endswith(KEYISH) or name.upper() in NOT_A_CREDENTIAL}
    done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                          cwd=ROOT, capture_output=True, text=True, env=clean, timeout=600)
    both = done.stdout + "\n" + done.stderr
    summary = next((line.strip() for line in reversed(both.splitlines())
                    if line.startswith("Ran ")), "no summary line")
    if done.returncode != 0:
        failures.append("the offline suite fails without keys")
        print(f"check 6  {'the offline suite with no keys set':<44} FAIL")
        print(done.stdout[-2000:])
        print(done.stderr[-2000:])
    else:
        report(6, "the offline suite with no keys set", [], f"({summary})")
        if "skipped=" in summary:
            print("         note: something was skipped, so a layer did not run. "
                  "Only a copy without git history should skip.")

    print()
    if failures:
        print(f"DO NOT PUSH: {len(failures)} finding(s) above.")
        return 1
    print("Push is safe. Nothing here carries a credential, and the suite passes without one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
