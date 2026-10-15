#!/usr/bin/env python3
"""The repository cannot carry a credential without this test failing.

This repository is public and is shared with hackathon organisers, so a committed key is a
published key. The check is about *tracked* files, which is the right boundary: an untracked
`.env` on a developer's machine is expected, is gitignored, and is what the live mode reads.

Five assertions, all offline:

    no tracked file carries a key, a token, a private key block or a secret assignment
    .env is not tracked, and a .env on disk is ignored
    .env.example carries placeholders only
    .gitignore covers .env and the local ledger
    the file list is real, so the test cannot pass by finding nothing

    python3 -m unittest discover -s tests -t .
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_SHAPES = {
    "an OpenAI key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "a private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "a bearer token": re.compile(r"[Bb]earer\s+[A-Za-z0-9._-]{24,}"),
    "an assigned secret": re.compile(
        r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s[\"']?[A-Za-z0-9_\-]{24,}"),
}

PLACEHOLDERS = {"", "replace_me", "none", "null", "changeme", "your_key_here", "xxx"}

# Named so a failing test says which file and which shape, without printing the match.
SKIP_DIRS = {".git", "var", "__pycache__", ".venv", "venv", ".pytest_cache"}


def tracked_files() -> list[str]:
    """What git tracks, or the working tree when git is not available."""
    try:
        done = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                              timeout=30)
        if done.returncode == 0 and done.stdout.strip():
            return [line for line in done.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            if name == ".env":
                continue
            found.append(os.path.relpath(os.path.join(base, name), ROOT))
    return sorted(found)


class TestNoCredentialIsCommitted(unittest.TestCase):
    def setUp(self):
        self.files = tracked_files()

    def test_the_file_list_is_real(self):
        # Without this, a broken walk or an empty git answer would pass every check below.
        self.assertGreater(len(self.files), 5, self.files)
        self.assertIn("README.md", self.files)
        self.assertIn(".env.example", self.files)

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

    def test_env_is_never_tracked_and_is_ignored(self):
        self.assertNotIn(".env", self.files)
        if os.path.exists(os.path.join(ROOT, ".env")):
            done = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT)
            self.assertEqual(done.returncode, 0, "a .env on disk must be ignored by git")

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
