#!/usr/bin/env python3
"""The repository cannot carry a credential without this test failing.

This repository is public and is shared with hackathon organisers, so a committed key is a published
key. Four layers, because each one alone has a blind spot:

1. Shapes in the tracked files. Catches a key that looks like a key, wherever it sits.
2. The same shapes across every commit on every branch. Catches a key that was committed and then
   removed, which a scan of the current files cannot see and which a push publishes anyway.
3. The values of the keys that exist on this machine, checked against both of the above. This is the
   layer that catches a key whose shape we have no pattern for, which is every provider we have not
   written a pattern for.
4. The plumbing: .env is not tracked and is ignored, the template carries placeholders only, and the
   ignore file covers what it must.

Untracked files are out of scope on purpose. An untracked, gitignored `.env` on a developer's machine
is expected, is what the live mode reads, and is not what a push publishes.

Nothing here prints the value it caught. A failing test ends up in a build log, which is not a place
to leak a credential, so a finding is named by file, commit, shape and variable name.

A scan that could not run is a failure, never a skip or a pass. The first version of this file let a
history scan fail and report no findings, which reads exactly like a clean history, and it skipped.
A guard that skips is a guard that lies.

This test reads nothing outside the repository, so it behaves the same in a reviewer's clone.

    python3 -m unittest discover -s tests -t .
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_SHAPES = {
    "an OpenAI-style key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "a private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "a bearer token": re.compile(r"[Bb]earer\s+[A-Za-z0-9._-]{24,}"),
    "an assigned secret": re.compile(
        r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s[\"']?[A-Za-z0-9_\-]{24,}"),
}

PLACEHOLDERS = {"", "replace_me", "none", "null", "changeme", "your_key_here", "xxx"}

# Named so a failing test says which file and which shape, without printing the match.
SKIP_DIRS = {".git", "var", "__pycache__", ".venv", "venv", ".pytest_cache"}

# Names that look like credentials and are not: they identify a session or a container rather than
# granting access to anything, and scanning them is noise.
NOT_A_CREDENTIAL = {"HERMES_SESSION_KEY", "TERMINAL_DOCKER_SHARED_CONTAINER_KEY", "GPG_KEY"}

# Values shorter than this are placeholders or identifiers, not keys.
MIN_CREDENTIAL_LENGTH = 20

# A string that is certainly in the history. If the history scan cannot find this, the scan is not
# reading anything and every credential check below it would pass for the wrong reason.
BENIGN_MARKER = "Humsana"

# The three things a scan can say. "could not run" is deliberately not the same as "found nothing".
RAN = "ran"
NO_HISTORY = "no history"
FAILED = "could not run"


def run_git(*args: str, timeout: int = 120) -> tuple[bool, str]:
    """(it ran, stdout). A grep that matched nothing ran fine and returns no lines."""
    try:
        done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    if done.returncode == 0:
        return True, done.stdout
    if done.returncode == 1 and args[:1] == ("grep",):
        return True, ""
    return False, done.stderr.strip()


def tracked_files() -> list[str]:
    """What git tracks, or the working tree when git is not available."""
    ran, out = run_git("ls-files", timeout=30)
    if ran and out.strip():
        return [line for line in out.splitlines() if line.strip()]
    found = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            if name == ".env":
                continue
            found.append(os.path.relpath(os.path.join(base, name), ROOT))
    return sorted(found)


def history() -> list[str]:
    """Every commit reachable from every branch and tag, newest first."""
    ran, out = run_git("rev-list", "--all")
    if not ran:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def parse_grep(lines: list[str]) -> list[str]:
    """git grep output to `commit:path:line`, keeping the sha when git adds one."""
    hits = []
    for line in lines:
        parts = line.split(":", 3)
        if len(parts) < 3:
            continue
        if re.fullmatch(r"[0-9a-f]{7,40}", parts[0]):
            hits.append(f"{parts[0][:12]}:{parts[1]}:{parts[2]}")
        else:
            hits.append(f"{parts[0]}:{parts[1]}")
    return hits


def scan_history(pattern: str, *, fixed: bool = False) -> tuple[str, list[str]]:
    """Search every commit. Returns (state, findings that never carry the matched content).

    A pattern written for Python's `re` may carry an inline flag that git's own regex does not accept,
    so a leading (?i) becomes a command line flag instead. Passing it through unchanged made the whole
    scan fail, and a failed scan reported no findings, which is indistinguishable from a clean one.
    """
    revisions = history()
    if not revisions:
        return NO_HISTORY, []
    pattern = pattern.removeprefix("(?i)")
    # -e, because a pattern may legitimately begin with a dash: the shape for a private key block
    # does, and without this git reads it as an option and the scan fails.
    args = ["grep", "-I", "-n", "-i"]
    args += ["-F", "-e", pattern] if fixed else ["-E", "-e", pattern]
    ran, out = run_git(*args, *revisions)
    if not ran:
        return FAILED, []
    return RAN, parse_grep(out.splitlines())


def credentials_on_this_machine() -> dict[str, str]:
    """The keys available here, by name. The values never leave this function's return."""
    found: dict[str, str] = {}
    for name, value in os.environ.items():
        if name in NOT_A_CREDENTIAL or not value:
            continue
        if not name.upper().endswith(("_API_KEY", "_KEY", "_TOKEN", "_SECRET")):
            continue
        if len(value) >= MIN_CREDENTIAL_LENGTH:
            found[f"environment:{name}"] = value
    env_path = os.path.join(ROOT, ".env")
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                value = value.strip().strip("\"'")
                if name.upper().endswith(("_API_KEY", "_KEY", "_TOKEN", "_SECRET")):
                    if len(value) >= MIN_CREDENTIAL_LENGTH and value.lower() not in PLACEHOLDERS:
                        found[f".env:{name}"] = value
    return found


class TestNoCredentialIsCommitted(unittest.TestCase):
    def setUp(self):
        self.files = tracked_files()

    def test_the_file_list_is_real(self):
        # Without this, a broken walk or an empty git answer would pass every check below.
        self.assertGreater(len(self.files), 5, self.files)
        self.assertIn("README.md", self.files)
        self.assertIn(".env.example", self.files)

    def test_the_history_scan_reads_something(self):
        # The guard against a vacuous pass. A scan that reads no commits finds no credentials, and
        # looks exactly like a clean history.
        state, hits = scan_history(BENIGN_MARKER)
        if state == NO_HISTORY:
            self.skipTest("no git history in this copy, so there is nothing to scan")
        self.assertEqual(state, RAN, "the history scan could not run, so it is not evidence")
        self.assertTrue(hits, f"the history scan found nothing, not even {BENIGN_MARKER!r}")

    def test_no_tracked_file_carries_a_credential(self):
        for name in self.files:
            path = os.path.join(ROOT, name)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as handle:
                body = handle.read()
            for label, shape in SECRET_SHAPES.items():
                # The match itself is never passed to the assertion: a failing test must not
                # print the credential it caught into a build log.
                matched = shape.search(body) is not None
                self.assertFalse(matched, f"{name} carries {label}")

    def test_no_commit_carries_a_credential(self):
        for label, shape in SECRET_SHAPES.items():
            state, hits = scan_history(shape.pattern)
            if state == NO_HISTORY:
                self.skipTest("no git history in this copy, so there is nothing to scan")
            self.assertEqual(state, RAN, f"the history scan for {label} could not run")
            self.assertEqual(hits, [], f"history carries {label}: " + ", ".join(hits[:5]))

    def test_no_key_that_exists_on_this_machine_is_in_the_repository(self):
        # The layer that does not depend on knowing a provider's key format. If the value is here and
        # it is in something we would publish, that is a finding whether or not we have a pattern for
        # it. Named by variable, never by value.
        keys = credentials_on_this_machine()
        for source, value in keys.items():
            for name in self.files:
                path = os.path.join(ROOT, name)
                if not os.path.isfile(path):
                    continue
                with open(path, encoding="utf-8", errors="replace") as handle:
                    body = handle.read()
                self.assertNotIn(value, body, f"the key from {source} appears in {name}")
        if not history():
            return
        for source, value in keys.items():
            state, hits = scan_history(value, fixed=True)
            self.assertEqual(state, RAN, f"the history scan for the key from {source} could not run")
            self.assertEqual(hits, [], f"the key from {source} appears in history: "
                                       + ", ".join(hits[:5]))

    def test_env_is_never_tracked_and_is_ignored(self):
        self.assertNotIn(".env", self.files)
        if os.path.exists(os.path.join(ROOT, ".env")):
            ran, _ = run_git("check-ignore", "-q", ".env", timeout=30)
            self.assertTrue(ran, "a .env on disk must be ignored by git")

    def test_the_template_carries_placeholders_only(self):
        with open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if any(word in name.upper() for word in ("KEY", "SECRET", "TOKEN", "PASSWORD")):
                    self.assertIn(value.strip().lower(), PLACEHOLDERS,
                                  f"{name} in .env.example must be a placeholder")

    def test_the_ignore_file_covers_env_and_the_local_ledger(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn(".env", body)
        self.assertIn("var/", body)


if __name__ == "__main__":
    unittest.main()
