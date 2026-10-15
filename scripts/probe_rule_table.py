#!/usr/bin/env python3
"""Measure the rule table, live. One variant per bound field type.

The comparison is not "nothing may change". Read out of `policy/effectuation.py`, it is:

    a number      may not rise          MAY_NOT_INCREASE
    a list        may add nothing       MAY_ONLY_SHRINK
    anything else may not change        MAY_NOT_CHANGE
    a new key at execution              MAY_NOT_GROW

A consequence worth knowing before writing a demo: a numeric authority is a CEILING, so
lowering it is permitted. If you want a value that may not move at all, do not express it as
a number.

    python3 scripts/probe_rule_table.py
"""

from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humsana_gate import Boundary, BoundaryError   # noqa: E402
from scripts.slice import require_credentials      # noqa: E402

NAME = "hiring.advance_candidate"

AUTHORIZED = {
    "candidate": {"id": "cand-7731"},
    "role": {"id": "senior-engineer"},
    "decision": {"value": "advance_to_offer"},
    "standard": {"id": "senior-bar-4yr"},
    "salary": {"currency": "GBP", "value": 95000},
}

VARIANTS = {
    "a string moved (the standard label)": [("standard.id", "sr-engineer-3yr")],
    "a number raised (salary 95000 -> 120000)": [("salary.value", 120000)],
    "a number lowered (salary 95000 -> 90000)": [("salary.value", 90000)],
    "another string moved (candidate id)": [("candidate.id", "cand-9999")],
    "a key added that was never authorized": [("__add__", ("channel", "general"))],
    "currency changed under a number": [("salary.currency", "USD")],
}


def set_path(parameters, dotted, value):
    node = parameters
    for part in dotted.split(".")[:-1]:
        node = node[part]
    node[dotted.split(".")[-1]] = value
    return parameters


def main():
    require_credentials()
    boundary = Boundary(os.environ.get("HUMSANA_BASE_URL", "https://api.humsana.com"),
                        os.environ.get("HUMSANA_API_KEY"))

    print(f"{'variant':44s} {'code':26s} {'rule':16s} field")
    print("-" * 118)

    for label, changes in VARIANTS.items():
        receipt = boundary.verify_action(
            action_name=NAME, parameters=copy.deepcopy(AUTHORIZED), consequence_class="C1",
            purpose="advance a screened candidate against the role standard",
            actor_id="agent-hiring-1", principal_reference="employer:8842")

        moved = copy.deepcopy(AUTHORIZED)
        for dotted, value in changes:
            if dotted == "__add__":
                moved[value[0]] = value[1]
            else:
                set_path(moved, dotted, value)

        answer = boundary.effectuate_action(action_name=NAME, parameters=moved,
                                            receipt_id=receipt.get("receipt_id"))
        drift = boundary.drift(answer)
        if drift:
            for item in drift:
                print(f"{label:44s} {str(answer.get('code')):26s} "
                      f"{str(item.get('rule')):16s} {item.get('field')} "
                      f"({item.get('authorized')} -> {item.get('final')})")
        else:
            print(f"{label:44s} {str(answer.get('code')):26s} {'-':16s} "
                  f"no drift, cleared={boundary.clears(answer)}")


if __name__ == "__main__":
    try:
        main()
    except BoundaryError as error:
        print(f"call failed: {error}", file=sys.stderr)
        print(error.payload, file=sys.stderr)
        sys.exit(1)
