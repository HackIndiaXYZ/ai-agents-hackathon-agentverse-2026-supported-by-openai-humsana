#!/usr/bin/env python3
"""The whole story, live, against api.humsana.com.

An employer authorizes one hiring action. The agent, having read material it should not have
obeyed, proposes a different one. The gate follows the two branches the refusal allows:

    a field that decides the action   the gate does not substitute it. It hands the bound back,
                                      the agent decides again, and the new action is checked from
                                      scratch on the same authorization.
    a field that carries nothing       the gate drops or restores it, presents the admissible
                                      action, and writes if that clears.

Three scenarios, one per shape of drift, and the ledger is counted before and after each attempt
so "nothing was written" is a reading rather than a claim.

    python3 scripts/demo_hiring.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humsana_gate import classes                              # noqa: E402
from humsana_gate.boundary import Boundary, BoundaryError     # noqa: E402
from humsana_gate.gate import (CORRECTED_THEN_EXECUTED,        # noqa: E402
                              RECOMPUTE_REQUIRED, Gate)
from humsana_gate.store import Store                          # noqa: E402
from scripts.slice import require_credentials                 # noqa: E402

ACTION_CLASS = "hiring.advance_candidate"
LEDGER = os.environ.get("DEMO_LEDGER", "var/ledger.jsonl")


def rule(width=92):
    print("-" * width)


def show_drift(answer):
    for item in (answer.get("drift") or []):
        print(f"    the field  {item.get('field')}")
        print(f"    authorized {item.get('authorized')!r}")
        print(f"    presented  {item.get('final')!r}")
        print(f"    held by    {item.get('rule')}")


def show_bound(outcome):
    print("\nTHE BOUND, computed from the refusal and not from a model")
    for bound in outcome.repair.permitted:
        key = next(k for k in bound if k not in ("field", "rule", "why", "carries"))
        print(f"  {bound['field']}: {bound['rule']} -> {key} {bound[key]!r}"
              f"   (this field {'decides the action' if bound['carries'] == 'decides' else 'carries nothing about the decision'})")
        print(f"    {bound['why']}")
    print(f"  {outcome.repair.why}")


def recompute_from_bound(outcome, proposed):
    """What an agent does with a refusal: it decides again, using the values it is allowed to use.

    Scripted here so the demo runs without a model; scripts/agent_openai.py does it with one.
    """
    out = json.loads(json.dumps(proposed))
    for bound in outcome.repair.permitted:
        if bound["carries"] != "decides":
            continue
        legs = bound["field"].split(".")
        node = out
        for leg in legs[:-1]:
            node = node.setdefault(leg, {})
        if bound.get("must_be_absent"):
            node.pop(legs[-1], None)
        else:
            node[legs[-1]] = next(bound[k] for k in bound
                                  if k not in ("field", "rule", "why", "carries"))
    return out


def run_scenario(gate, store, scenario, index, total):
    entry = classes.get(ACTION_CLASS)
    authorized = gate.authorized
    proposed = classes.apply_scenario(scenario, authorized)

    rule()
    print(f"scenario {index}/{total}   {scenario['label']}")
    rule()
    print("THE AGENT PROPOSES")
    for dotted in scenario["change"]:
        print(f"    {dotted}: {classes.read_path(authorized, dotted)!r} -> "
              f"{classes.read_path(proposed, dotted)!r}")

    before = len(store.rows())
    print(f"\nTHE GATE, first check at the point of execution        ledger rows: {before}")
    outcome = gate.run(proposed, note=f"{ACTION_CLASS} via the gate")

    first = outcome.attempts[0].answer
    print(f"  receipt     {outcome.receipt_id}")
    print(f"  refused     {first.get('code')}   decision {first.get('decision')}   "
          f"matched {first.get('matched')}   consumed {first.get('consumed')}")
    show_drift(first)
    # The ledger is counted once, at the end. Counting it here would be ambiguous for the
    # correction branch, which makes both attempts inside a single run.

    if outcome.status == RECOMPUTE_REQUIRED:
        show_bound(outcome)
        print("\nTHE AGENT DECIDES AGAIN, against the values the refusal named")
        second_proposal = recompute_from_bound(outcome, proposed)
        for dotted in scenario["change"]:
            print(f"    {dotted}: {classes.read_path(proposed, dotted)!r} -> "
                  f"{classes.read_path(second_proposal, dotted)!r}")
        outcome = gate.resume(outcome, second_proposal, note=f"{ACTION_CLASS} recomputed")
        last = outcome.attempts[-1].answer
        print(f"\nTHE GATE, second check, same receipt {outcome.receipt_id}")
        print(f"  code        {last.get('code')}   matched {last.get('matched')}   "
              f"consumed {last.get('consumed')}")
    elif outcome.repair and outcome.repair.repairable:
        show_bound(outcome)
        last = outcome.attempts[-1].answer
        print(f"\nTHE GATE, the corrected action, same receipt {outcome.receipt_id}")
        print(f"  code        {last.get('code')}   matched {last.get('matched')}   "
              f"consumed {last.get('consumed')}")
    else:
        print(f"\nNO REPAIR IS POSSIBLE\n  {outcome.note}")

    print(f"  status      {outcome.status}")
    after = len(store.rows())
    if outcome.wrote:
        print("\nTHE WRITE")
        print("  the ledger row, read back out of the file rather than printed from memory:")
        print("  " + json.dumps(store.rows()[-1], sort_keys=True)[:400])
    else:
        print("  nothing was written: the store refuses a write that carries no clearance")
    return dict(scenario=scenario["label"], status=outcome.status,
                wrote=outcome.wrote, rows_before=before, rows_after=after,
                attempts=len(outcome.attempts))


def main():
    require_credentials()
    boundary = Boundary(os.environ.get("HUMSANA_BASE_URL", "https://api.humsana.com"),
                        os.environ.get("HUMSANA_API_KEY"))
    store = Store(LEDGER)
    gate = Gate(boundary, store, ACTION_CLASS)
    entry = classes.get(ACTION_CLASS)

    print()
    rule(92)
    print(f"HUMSANA GATE   {ACTION_CLASS}   {os.environ.get('HUMSANA_BASE_URL')}")
    rule(92)
    print("THE AUTHORITY, as the employer set it")
    print(f"  {entry['purpose']}")
    for field, held_by in entry["held_by"].items():
        print(f"    {field:22s} {classes.RULE_TABLE[held_by]}")
    print("  the tuple the boundary binds:")
    print("    " + json.dumps(gate.authorized, sort_keys=True))

    results = [run_scenario(gate, store, scenario, index, len(entry["scenarios"]))
               for index, scenario in enumerate(entry["scenarios"], start=1)]

    rule()
    print("SUMMARY")
    for result in results:
        wrote = "wrote one row" if result["wrote"] else f"wrote nothing ({result['rows_before']} -> " \
                                                        f"{result['rows_after']} rows)"
        print(f"  {result['status']:22s} {result['attempts']} attempt(s)   {wrote}"
              f"   {result['scenario']}")

    broken = [r for r in results if not r["wrote"] and r["rows_after"] != r["rows_before"]]
    live_ok = [r for r in results if r["status"] in ("EXECUTED", CORRECTED_THEN_EXECUTED)]
    print(f"\n  {len(live_ok)} of {len(results)} scenarios ended with the boundary clearing the "
          f"action, and every write went through it")
    print(f"  a refused action changed the ledger in {len(broken)} of {len(results)} scenarios")
    rule(92)
    print("The gate holds no authority of its own. A field that decides the action is never")
    print("substituted; the agent decides again. Nothing is written that the boundary did not clear.")
    return 1 if broken else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BoundaryError as error:
        print(f"\ncall failed: {error}", file=sys.stderr)
        print(error.payload, file=sys.stderr)
        sys.exit(2)
