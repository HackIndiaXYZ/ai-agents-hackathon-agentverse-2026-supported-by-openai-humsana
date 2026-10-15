"""The admissible version of a refused action, computed from the refusal itself.

A refusal that names the field, both values and the rule carries everything needed to compute
what may be presented instead, and that computation is deterministic: it reads the rule, not a
domain and not a model.

This module never widens anything. Every value it writes back is the value the authorization
already carried, so a repaired action is the authorized action with the drifted fields returned
to what was authorized and nothing else touched.

It is not a second authorization engine. It decides nothing: it reads the boundary's own answer,
and the repaired action must still clear the boundary before anything runs. A caller that could
propose its own admissible set would be widening its own authority, which is exactly what the
boundary exists to prevent.

Annotating a refusal with this is what turns "stop" into "finish the job inside your authority".
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

# The one code a caller can repair by presenting a corrected action. Every other refusal is
# about the authority rather than the action, so no correction to the action can fix it.
REPAIRABLE_CODES = ("EFFECTUATION_DRIFT",)

# What each rule permits, expressed so an agent can act on it without carrying a copy of our
# rule table: MAY_NOT_CHANGE -> equals, MAY_NOT_INCREASE -> at_most,
# MAY_ONLY_SHRINK -> subset_of, MAY_NOT_GROW -> must_be_absent.
BOUNDS = {
    "MAY_NOT_CHANGE": "equals",
    "MAY_NOT_INCREASE": "at_most",
    "MAY_ONLY_SHRINK": "subset_of",
    "MAY_NOT_GROW": "must_be_absent",
}

WHY = {
    "MAY_NOT_CHANGE": "this value may not move at all, so it has to be presented as authorized",
    "MAY_NOT_INCREASE": "this is a ceiling, so anything at or below the authorized value is accepted",
    "MAY_ONLY_SHRINK": "nothing may be added, so the authorized list is the widest accepted",
    "MAY_NOT_GROW": "this key was never authorized, so it has to be left out",
}


@dataclass(frozen=True)
class Repair:
    repairable: bool
    code: str
    permitted: tuple = ()
    admissible: dict | None = None
    changes: tuple = ()
    why: str = ""

    def as_dict(self) -> dict:
        return {"repairable": self.repairable, "code": self.code, "why": self.why,
                "permitted": [dict(entry) for entry in self.permitted],
                "changes": list(self.changes),
                "admissible": self.admissible}


def _set(parameters: dict, dotted: str, value):
    legs = dotted.split(".")
    node = parameters
    for leg in legs[:-1]:
        node = node.setdefault(leg, {})
    node[legs[-1]] = value


def _drop(parameters: dict, dotted: str):
    legs = dotted.split(".")
    node = parameters
    for leg in legs[:-1]:
        if not isinstance(node, dict) or leg not in node:
            return
        node = node[leg]
    if isinstance(node, dict):
        node.pop(legs[-1], None)


def from_refusal(answer: dict, presented: dict) -> Repair:
    """Read the boundary's refusal and compute the action it would accept instead.

    `presented` is the action that was refused, so a key the caller added is dropped rather than
    silently ignored, and an untouched field keeps the value the caller sent.
    """
    if not isinstance(answer, dict):
        raise TypeError("from_refusal takes the boundary's answer")

    code = answer.get("code")
    drift = answer.get("drift") or (answer.get("effectuation") or {}).get("drift") or []

    if code not in REPAIRABLE_CODES or not drift:
        return Repair(repairable=False, code=str(code),
                      why=("this refusal is about the authority rather than the action, so no "
                           "corrected action can fix it"))

    admissible = copy.deepcopy(presented)
    permitted, changes = [], []

    for entry in drift:
        dotted = str(entry.get("field", "")).removeprefix("parameters.")
        rule = entry.get("rule")
        if not dotted or rule not in BOUNDS:
            return Repair(repairable=False, code=str(code),
                          why=f"the drift names a rule this client does not know: {rule!r}")
        authorized, final = entry.get("authorized"), entry.get("final")

        if rule == "MAY_NOT_GROW":
            # The field was never authorized, so there is no value to publish beside it: the
            # bound is that it must not be presented at all.
            bound = {"field": dotted, "rule": rule, "must_be_absent": True, "why": WHY[rule]}
            _drop(admissible, dotted)
            changes.append(f"{dotted}: {final!r} removed, it was never authorized")
        else:
            bound = {"field": dotted, "rule": rule, BOUNDS[rule]: authorized, "why": WHY[rule]}
            _set(admissible, dotted, authorized)
            changes.append(f"{dotted}: {final!r} -> {authorized!r}")
        permitted.append(bound)

    return Repair(repairable=True, code=str(code), permitted=tuple(permitted),
                  admissible=admissible, changes=tuple(changes),
                  why="present the action below and the same authorization still covers it")
