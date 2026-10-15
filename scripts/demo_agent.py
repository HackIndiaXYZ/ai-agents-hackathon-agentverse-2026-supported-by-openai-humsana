#!/usr/bin/env python3
"""The end-to-end run: the agent proposes, Humsana checks, the agent decides again, one write.

Live against api.humsana.com, through the proposer interface, so what runs here is the loop that
the OpenAI proposer will drive unchanged.

    python3 scripts/demo_agent.py                          # the proposal the material led it to
    python3 scripts/demo_agent.py --proposer honest        # no refusal, for contrast
    python3 scripts/demo_agent.py --proposer overpays      # a different bound value moved
    python3 scripts/demo_agent.py --proposer adds_a_field  # a field that was never authorized
    python3 scripts/demo_agent.py --proposer stubborn      # the agent does not move, and nothing runs
    python3 scripts/demo_agent.py --proposer openai        # the model proposes, and decides again

The scripted proposer exists so the demonstration repeats exactly. It is not the agent. The model is
the agent, behind the same interface, and `--proposer openai` is the run that counts. It reads
OPENAI_API_KEY from .env, and never prints it.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humsana_gate import briefs, classes                                   # noqa: E402
from humsana_gate.agent import run_agent                                   # noqa: E402
from humsana_gate.boundary import Boundary, BoundaryError                  # noqa: E402
from humsana_gate.gate import Gate                                        # noqa: E402
from humsana_gate.openai_proposer import OpenAIError, OpenAIProposer        # noqa: E402
from humsana_gate.proposer import ScriptedProposer                         # noqa: E402
from humsana_gate.store import Store                                      # noqa: E402
from scripts.slice import require_credentials                             # noqa: E402

ACTION_CLASS = os.environ.get("DEMO_ACTION_CLASS", "hiring.advance_candidate")
LEDGER = os.environ.get("DEMO_LEDGER", "var/ledger-agent.jsonl")


def rule(width=94):
    print("-" * width)


def build_proposer(kind: str = ""):
    """The proposer the loop runs behind: a scripted behaviour, or `openai` for the model."""
    if kind == "openai":
        try:
            return OpenAIProposer()
        except OpenAIError as error:
            print(f"{error}", file=sys.stderr)
            raise SystemExit(2) from None
    try:
        return ScriptedProposer(kind or "obeys_the_note")
    except ValueError as error:
        print(f"{error}", file=sys.stderr)
        print("  or --proposer openai, which reads OPENAI_API_KEY from .env", file=sys.stderr)
        raise SystemExit(2) from None


def main(argv: list[str]) -> int:
    require_credentials()
    kind = "obeys_the_note"
    if "--proposer" in argv:
        kind = argv[argv.index("--proposer") + 1]
    quiet = "--quiet" in argv

    proposer = build_proposer(kind)
    boundary = Boundary(os.environ.get("HUMSANA_BASE_URL", "https://api.humsana.com"),
                        os.environ.get("HUMSANA_API_KEY"))
    store = Store(LEDGER)
    gate = Gate(boundary, store, ACTION_CLASS)
    brief = briefs.hiring_brief(ACTION_CLASS)

    rows_before = len(store.rows())
    transcript = run_agent(proposer, gate, brief)
    rows_after = len(store.rows())

    print()
    rule()
    print(f"HUMSANA AND AN AGENT   {ACTION_CLASS}")
    print(f"boundary {os.environ.get('HUMSANA_BASE_URL')}   proposer {proposer.name}")
    rule()
    for beat in transcript.beats:
        print(f"\n{beat.who.upper()}  {beat.headline}")
        for line in beat.detail.splitlines():
            print(f"  {line}")

    rule()
    print(f"status            {transcript.status}")
    print(f"attempts          {len(transcript.outcome.attempts)}")
    print(f"ledger            {rows_before} rows before, {rows_after} after")
    if transcript.executed:
        print(f"the action that ran, read back out of the file:")
        print("  " + json.dumps(store.rows()[-1]["action"], sort_keys=True))
    else:
        print("nothing ran, and no authorization was spent")

    if not quiet:
        refusal = transcript.outcome.first_refusal
        if refusal is not None:
            record = refusal.get("authorization_record") or {}
            print(f"\nthe refusal is a signed record: {refusal.get('code')} "
                  f"{refusal.get('receipt_id')} "
                  f"digest {str(record.get('record_digest'))[:24]}")
    for note in getattr(proposer, "notes", []):
        print(f"note: {note}")
    rule()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BoundaryError as error:
        print(f"\ncall failed: {error}", file=sys.stderr)
        print(error.payload, file=sys.stderr)
        sys.exit(2)
    except OpenAIError as error:
        print(f"\n{error}", file=sys.stderr)
        sys.exit(2)
