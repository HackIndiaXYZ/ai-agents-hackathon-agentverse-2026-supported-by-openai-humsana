#!/usr/bin/env python3
"""The whole spine, live, against the real boundary. No UI, no agent, no mock.

    1. ask for an authorization for a consequential action
    2. present the exact action for execution            -> cleared, once
    3. for each way the action can be made to move:
         ask again, then present the moved action        -> refused, the field named

    python3 scripts/slice.py [action_class]

Every step is a real HTTP call to api.humsana.com, and every answer is a signed record.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humsana_gate import Boundary, BoundaryError          # noqa: E402
from humsana_gate import classes                          # noqa: E402


def load_env(path=".env"):
    """Minimal .env reader, so the repository needs no dependency for one file."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def rule(width=100):
    print("-" * width)


def main():
    load_env()
    action_class = (sys.argv[1] if len(sys.argv) > 1
                    else os.environ.get("DEMO_ACTION_CLASS", "hiring.advance_candidate"))

    entry = classes.get(action_class)
    name, authorized = classes.authorized_action(action_class)
    boundary = Boundary(os.environ.get("HUMSANA_BASE_URL", "https://api.humsana.com"),
                        os.environ.get("HUMSANA_API_KEY"))

    def verify():
        return boundary.verify_action(
            action_name=name, parameters=authorized, purpose=entry["purpose"],
            consequence_class=entry["consequence_class"],
            actor_id="agent-1", principal_reference="employer:8842")

    rule()
    print(f"action class : {action_class}    domain: {entry['domain']}")
    print(f"authority    : {entry['purpose']}")
    for field, held_by in entry["held_by"].items():
        print(f"  held by    : {field:22s} {held_by:17s} {classes.RULE_TABLE[held_by]}")
    health = boundary.health()
    print(f"boundary     : version {health.get('version')}  policy {health.get('policy_version')}  "
          f"contract {health.get('contract') or 'humsana.authorization/1'}")
    rule()

    # --------------------------------------------------- 1 and 2, the action that matches
    print("\n1. ask for an authorization")
    receipt = verify()
    print(f"   receipt  : {receipt.get('receipt_id')}")
    print(f"   decision : {receipt.get('decision')}   rules: "
          f"{(receipt.get('policy') or {}).get('rules_applied')}")
    print(f"   digest   : {(receipt.get('binding') or {}).get('digest')}")

    print("\n2. present the exact action for execution")
    answer = boundary.effectuate_action(action_name=name, parameters=authorized,
                                        receipt_id=receipt.get("receipt_id"))
    print(f"   code     : {answer.get('code')}   matched {answer.get('matched')}   "
          f"consumed {answer.get('consumed')}")
    print("   -> the executor may perform the action, once" if boundary.clears(answer)
          else "   -> the executor must not act")

    # ------------------------------------------- 3 onward, one bound value moved each time
    step = 3
    for scenario in entry["scenarios"]:
        moved = classes.apply_scenario(scenario, authorized)
        print(f"\n{step}. {scenario['label']}")
        for dotted in scenario["change"]:
            was, now = classes.read_path(authorized, dotted), classes.read_path(moved, dotted)
            print(f"   authorized {dotted}: {was}")
            print(f"   presented  {dotted}: {now}")

        receipt = verify()
        answer = boundary.effectuate_action(action_name=name, parameters=moved,
                                            receipt_id=receipt.get("receipt_id"))
        print(f"   code     : {answer.get('code')}   decision {answer.get('decision')}   "
              f"matched {answer.get('matched')}   consumed {answer.get('consumed')}")
        for item in boundary.drift(answer):
            print(f"   drift    : {item.get('field')}   authorized {item.get('authorized')}   "
                  f"presented {item.get('final')}   {item.get('rule')}")
        if boundary.clears(answer):
            print("   -> cleared. That would be a defect worth reporting, not a demo.")
        else:
            print("   -> the executor must not act. Nothing was spent, so the same "
                  "authorization still executes the exact action while its window is open.")
        step += 1

    print("\nEvery receipt and every execution above is a signed record. They verify offline "
          "against the key this deployment publishes, without trusting this script.")


if __name__ == "__main__":
    try:
        main()
    except BoundaryError as error:
        print(f"\ncall failed: {error}", file=sys.stderr)
        print(f"payload: {error.payload}", file=sys.stderr)
        sys.exit(1)
