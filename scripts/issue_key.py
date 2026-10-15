#!/usr/bin/env python3
"""Ask the boundary for a key. It is shown once and only its hash is kept, so put it in .env
on first receipt or wait an hour for the window to pass.

    python3 scripts/issue_key.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("HUMSANA_BASE_URL", "https://api.humsana.com")
PURPOSE = os.environ.get("HUMSANA_KEY_PURPOSE", "AgentVerse 2026 demo gate")

body = json.dumps({"purpose": PURPOSE}).encode("utf-8")
request = urllib.request.Request(f"{BASE}/v1/keys", data=body, method="POST")
request.add_header("content-type", "application/json")
request.add_header("user-agent", "humsana-gate/0.1")

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as error:
    raw = error.read().decode("utf-8", "replace")
    print(f"refused HTTP {error.code}", file=sys.stderr)
    try:
        parsed = json.loads(raw)
        print(f"  code:   {parsed.get('code')}", file=sys.stderr)
        print(f"  message: {parsed.get('message')}", file=sys.stderr)
        print(f"  remedy: {parsed.get('remedy')}", file=sys.stderr)
    except ValueError:
        print(raw, file=sys.stderr)
    sys.exit(1)

key = payload.get("api_key")
if not key:
    print("the service answered without a key:", json.dumps(payload), file=sys.stderr)
    sys.exit(1)

masked = f"{key[:8]}...{key[-4:]}" if len(key) > 16 else "(short key)"

if "--save" in sys.argv:
    # The script writes the file, so the secret never travels through a terminal, a log or a
    # chat transcript. .env is seeded from .env.example when it does not exist yet.
    if not os.path.exists(".env") and os.path.exists(".env.example"):
        shutil.copy(".env.example", ".env")
    lines, found = [], False
    if os.path.exists(".env"):
        for line in open(".env", encoding="utf-8"):
            if line.startswith("HUMSANA_API_KEY="):
                lines.append(f"HUMSANA_API_KEY={key}\n")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"HUMSANA_API_KEY={key}\n")
    with open(".env", "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    os.chmod(".env", 0o600)
    print(f"saved to .env: HUMSANA_API_KEY={masked}  (file mode 600)")
else:
    print("api_key (shown once):", key)
    print("save it without printing it again: python3 scripts/issue_key.py --save")

print("rate_limit_per_hour :", payload.get("rate_limit_per_hour"))
print("retention           :", json.dumps(payload.get("retention")))
