#!/usr/bin/env python3
"""The whole story, live, against api.humsana.com.

An employer authorizes one hiring action. The agent, having read material it should not have
obeyed, proposes a different one. The gate refuses it, computes the admissible action from the
refusal, presents that against the same authorization, and writes only once the boundary clears
it. Then it prints the ledger row, so the write can be checked rather than believed.

    python3 scripts/demo_hiring.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humsana_gate import classes                              # noqa: E402
from humsana_gate.boundary import Boundary, BoundaryError     # noqa: E402
from humsana_gate.gate import Gate                            # noqa: E402
from humsana_gate.store import Store                          # noqa: E402
from scripts.slice import load_env                            # noqa: E402

ACTION_CLASS = "hiring.advance_candidate"
LEDGER = os.environ.get("DEMO_LEDGER", "var/ledger.jsonl")


def rule(width=96):
    print("-" * width)


def main():
    load_env()
    boundary = Boundary(os.environ.get("HUMSANA_BASE_URL", "https://api.humsana.com"),
                        os.environ.get("HUMSANA_API_KEY"))
    store = Store(LEDGER)
    gate = Gate(boundary, store, ACTION_CLASS)

    entry = classes.get(ACTION_CLASS)
    authorized = gate.authorized

    rule()
    print("THE AUTHORITY, as the employer set it")
    print(f"  {entry['purpose']}")
    for field, held_by in entry["held_by"].items():
        print(f"    {field:22s} {classes.RULE_TABLE[held_by]}")
    rule()

    # What the agent proposes. In the full demo an OpenAI agent produces this after reading
    # candidate material that carries an instruction it should not have obeyed.
    scenario = entry["scenarios"][0]
    proposed = classes.apply_scenario(scenario, authorized)
    print("THE AGENT")
    print(f"  {scenario['label']}")
    for dotted in scenario["change"]:
        print(f"    {dotted}: {classes.read_path(authorized, dotted)} -> "
              f"{classes.read_path(proposed, dotted)}")

    print("\nTHE GATE: binding the authority, then presenting the action as it is about to happen")
    outcome = gate.run(proposed, note=f"{ACTION_CLASS} via the gate")

    first = outcome.attempts[0].answer
    print(f"  receipt     {outcome.receipt_id}")
    print(f"  refused     {first.get('code')}   decision {first.get('decision')}   "
          f"matched {first.get('matched')}   consumed {first.get('consumed')}")
    for item in (first.get("drift") or []):
        print(f"    the field  {item.get('field')}")
        print(f"    authorized {item.get('authorized')}")
        print(f"    presented  {item.get('final')}")
        print(f"    held by    {item.get('rule')}")

    if outcome.repair and outcome.repair.repairable:
        print("\nTHE ADMISSIBLE ACTION, computed from the refusal, not from a model")
        for bound in outcome.repair.permitted:
            key = next(k for k in bound if k not in ("field", "rule", "why"))
            print(f"  {bound['field']}: {bound['rule']} -> {key} {bound[key]!r}")
            print(f"    {bound['why']}")
        print(f"  {outcome.repair.why}")

        print("\nTHE GATE AGAIN: the same authorization, the corrected action")
        second = outcome.attempts[-1].answer
        print(f"  code        {second.get('code')}   matched {second.get('matched')}   "
              f"consumed {second.get('consumed')}")
        print(f"  status      {outcome.status}")
    else:
        print("\nNO REPAIR IS POSSIBLE")
        print(f"  {outcome.note}")
        print(f"  status      {outcome.status}")

    if outcome.wrote:
        print("\nTHE WRITE")
        print("  the ledger row, read back out of the file rather than printed from memory:")
        print("  " + json.dumps(store.rows()[-1], sort_keys=True)[:400])
    else:
        print("\nNothing was written. The store refuses a write that carries no clearance.")

    rule()
    print("One authorization. A refusal that names the field and the rule, an admissible action")
    print("derived from it, and a write that happened because the boundary cleared it, once.")


if __name__ == "__main__":
    try:
        main()
    except BoundaryError as error:
        print(f"\ncall failed: {error}", file=sys.stderr)
        print(error.payload, file=sys.stderr)
        sys.exit(1)
