#!/usr/bin/env python3
"""The authorization matrix, measured against the live boundary.

One row per way the action can differ from what was authorized, each with the result it must
have. Every row runs a real `verify` and a real `effectuate` against api.humsana.com.

The point of a matrix rather than a story: a single refusal only shows that one field was
caught. What has to hold is the whole tuple, including that a field which was NOT moved cannot
buy a pass for one that was, and that a presentation that satisfies every rule clears.

One row is expected to disagree, and it is labelled `known gap` rather than hidden:

    a bar bound as a NUMBER, 4 years, presented as 2 years

The engine holds a number upward only, so a minimum expressed as a number is not protected. That
row is why the demo binds the bar by name, and it is the evidence for the one rule the engine is
missing. When that rule lands, delete the `known_gap` flag and the row must pass like the rest.

    python3 scripts/authorization_matrix.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humsana_gate import classes                                  # noqa: E402
from humsana_gate.boundary import Boundary, BoundaryError         # noqa: E402
from scripts.slice import require_credentials                     # noqa: E402

ACTION = "hiring.advance_candidate"

# The tuple the demo uses: the bar is bound by its name.
LABEL_BAR = {
    "candidate": {"id": "cand-7731"},
    "role": {"id": "senior-engineer"},
    "decision": {"value": "advance_to_offer"},
    "standard": {"id": "senior-bar-4yr"},
    "salary": {"currency": "GBP", "value": 95000},
}

# The same authority written with the bar as a number. Built here rather than shipped in
# classes.py, because it is not an authority this gateway would recommend.
NUMBER_BAR = {
    "candidate": {"id": "cand-7731"},
    "role": {"id": "senior-engineer"},
    "decision": {"value": "advance_to_offer"},
    "standard": {"years_minimum": 4},
    "salary": {"currency": "GBP", "value": 95000},
}


def with_values(base, **overrides):
    import copy
    out = copy.deepcopy(base)
    for dotted, value in overrides.items():
        legs = dotted.replace("__", ".").split(".")
        node = out
        for leg in legs[:-1]:
            node = node.setdefault(leg, {})
        node[legs[-1]] = value
    return out


def without(base, dotted):
    import copy
    out = copy.deepcopy(base)
    legs = dotted.split(".")
    node = out
    for leg in legs[:-1]:
        node = node[leg]
    node.pop(legs[-1], None)
    return out


ROWS = [
    dict(label="the authorized action, presented as authorized",
         authorized=LABEL_BAR, presented=LABEL_BAR, expect="CLEAR"),
    dict(label="a bar the employer never authorized",
         authorized=LABEL_BAR,
         presented=with_values(LABEL_BAR, standard__id="mid-bar-2yr"), expect="REFUSE"),
    dict(label="the offer below the ceiling",
         authorized=LABEL_BAR,
         presented=with_values(LABEL_BAR, salary__value=90000), expect="CLEAR"),
    dict(label="the offer above the ceiling",
         authorized=LABEL_BAR,
         presented=with_values(LABEL_BAR, salary__value=120000), expect="REFUSE"),
    dict(label="a different candidate altogether",
         authorized=LABEL_BAR,
         presented=with_values(LABEL_BAR, candidate__id="cand-9999"), expect="REFUSE"),
    dict(label="an extra field that was never authorized",
         authorized=LABEL_BAR,
         presented=with_values(LABEL_BAR, notify__channel="general"), expect="REFUSE"),
    dict(label="an extra nested field, the parent object being new too",
         authorized=LABEL_BAR,
         presented=with_values(LABEL_BAR, audit={"trail": "on"}), expect="REFUSE"),
    dict(label="a bound field omitted entirely",
         authorized=LABEL_BAR, presented=without(LABEL_BAR, "salary"), expect="REFUSE"),
    dict(label="a bound nested field omitted entirely",
         authorized=LABEL_BAR, presented=without(LABEL_BAR, "salary.value"), expect="REFUSE"),
    dict(label="two fields moved at once",
         authorized=LABEL_BAR,
         presented=with_values(LABEL_BAR, standard__id="mid-bar-2yr", salary__value=120000),
         expect="REFUSE"),
    dict(label="a bar bound as a NUMBER, 4 years, presented as 2",
         authorized=NUMBER_BAR,
         presented=with_values(NUMBER_BAR, standard__years_minimum=2), expect="REFUSE",
         known_gap="the engine holds a number upward only, so a numeric floor is not protected"),
    dict(label="a bar bound as a NUMBER, 4 years, presented as 6",
         authorized=NUMBER_BAR,
         presented=with_values(NUMBER_BAR, standard__years_minimum=6), expect="REFUSE"),
]


def main() -> int:
    require_credentials()
    boundary = Boundary(os.environ.get("HUMSANA_BASE_URL", "https://api.humsana.com"),
                        os.environ.get("HUMSANA_API_KEY"))

    disagreements, gaps = [], []
    print(f"{'row':56s} {'expected':9s} {'measured':9s} verdict")
    print("-" * 108)

    for row in ROWS:
        receipt = boundary.verify_action(
            action_name=ACTION, parameters=row["authorized"], consequence_class="C1",
            purpose=classes.get(ACTION)["purpose"], actor_id="agent-1",
            principal_reference="employer:8842")
        answer = boundary.effectuate_action(action_name=ACTION, parameters=row["presented"],
                                            receipt_id=receipt.get("receipt_id"))
        measured = "CLEAR" if boundary.clears(answer) else "REFUSE"
        drift = answer.get("drift") or []

        if measured == row["expect"]:
            verdict = "as expected"
        elif row.get("known_gap"):
            verdict = "KNOWN GAP"
            gaps.append(row)
        else:
            verdict = "DISAGREES"
            disagreements.append(row)

        print(f"{row['label']:56s} {row['expect']:9s} {measured:9s} {verdict}")
        if drift:
            for item in drift:
                print(f"{'':56s} {item.get('field')}: {item.get('authorized')!r} -> "
                      f"{item.get('final')!r}  {item.get('rule')}")

    print()
    print(f"{len(ROWS)} rows, {len(ROWS) - len(disagreements) - len(gaps)} as expected, "
          f"{len(gaps)} known gap, {len(disagreements)} disagreement")
    for row in gaps:
        print(f"  known gap   {row['label']}: {row['known_gap']}")
    for row in disagreements:
        print(f"  DISAGREES   {row['label']}: expected {row['expect']}")

    data = {"expected": classes.authorized_action(ACTION)[1]}
    print(f"\nthe authorized tuple the demo binds: {data['expected']}")
    return 1 if disagreements else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BoundaryError as error:
        print(f"call failed: {error}", file=sys.stderr)
        print(error.payload, file=sys.stderr)
        sys.exit(2)
