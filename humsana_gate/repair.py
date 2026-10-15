"""The admissible action, computed from the refusal, and whether the gate may apply it.

A refusal that names the field, both values and the rule carries what may be presented instead,
and that computation is deterministic: the rule names the field and the field has one authorized
value. No model is involved, and it can only ever narrow.

The harder question is not what the admissible action is. It is whether substituting it keeps the
action the agent meant to take. A corrected payload is not a corrected decision: an agent that
evaluated a candidate against the mid-level bar still did that, whatever the payload says
afterwards. So this module answers both questions, and the gate acts on the difference.

    safe_to_apply   every drifted field is one the class declares as carrying nothing about the
                    decision, so replacing it cannot change what the action means
    requires_recompute
                    the drifted fields the agent reasoned with. The gate must not substitute
                    these: it hands them back, the agent decides again, and what it produces is
                    checked from scratch

A field the class does not name counts as deciding, so nothing unclassified is ever substituted.
A caller that could propose its own admissible set would be widening its own authority, which is
the thing the boundary exists to prevent.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

# The one code a caller can repair by presenting a corrected action. Every other refusal is
# about the authority rather than the action, so no correction to the action can fix it.
REPAIRABLE_CODES = ("EFFECTUATION_DRIFT",)

# What each rule permits, expressed so an agent can act on it without carrying a copy of our rule
# table: MAY_NOT_CHANGE -> equals, MAY_NOT_INCREASE -> at_most, MAY_ONLY_SHRINK -> subset_of,
# MAY_NOT_GROW -> must_be_absent.
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
    requires_recompute: tuple = ()
    why: str = ""

    @property
    def safe_to_apply(self) -> bool:
        """True only when every drifted field carries nothing about the decision."""
        return bool(self.repairable) and not self.requires_recompute

    def as_dict(self) -> dict:
        return {"repairable": self.repairable, "code": self.code, "why": self.why,
                "safe_to_apply": self.safe_to_apply,
                "requires_recompute": list(self.requires_recompute),
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
    chain = []
    for leg in legs[:-1]:
        if not isinstance(node, dict) or leg not in node:
            return
        chain.append((node, leg))
        node = node[leg]
    if isinstance(node, dict):
        node.pop(legs[-1], None)
    # A parent the drop emptied has to go with it. Leaving `notify: {}` behind keeps the key
    # present, so the boundary would report it as unauthorized all over again.
    for parent, leg in reversed(chain):
        if isinstance(parent.get(leg), dict) and not parent[leg]:
            parent.pop(leg, None)
        else:
            break


def from_refusal(answer: dict, presented: dict, *, decides=(), presentation=()) -> Repair:
    """Read the boundary's refusal and compute the action it would accept instead.

    `presented` is the action that was refused, so a key the caller added is dropped rather than
    silently ignored, and an untouched field keeps the value the caller sent. `decides` and
    `presentation` come from the action class: they are the customer's domain knowledge, not the
    boundary's.
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
    permitted, changes, recompute = [], [], []

    for entry in drift:
        dotted = str(entry.get("field", "")).removeprefix("parameters.")
        rule = entry.get("rule")
        if not dotted or rule not in BOUNDS:
            return Repair(repairable=False, code=str(code),
                          why=f"the drift names a rule this client does not know: {rule!r}")
        authorized, final = entry.get("authorized"), entry.get("final")

        # Only a field the class explicitly calls presentation may be substituted. Everything
        # else, named or not, is one the agent reasoned with.
        carried = "presentation" if dotted in presentation else "decides"
        if carried == "decides":
            recompute.append(dotted)

        if rule == "MAY_NOT_GROW":
            # The field was never authorized, so there is no value to publish beside it: the bound
            # is that it must not be presented at all.
            bound = {"field": dotted, "rule": rule, "must_be_absent": True, "why": WHY[rule],
                     "carries": carried}
            _drop(admissible, dotted)
            changes.append(f"{dotted}: {final!r} removed, it was never authorized")
        else:
            bound = {"field": dotted, "rule": rule, BOUNDS[rule]: authorized, "why": WHY[rule],
                     "carries": carried}
            _set(admissible, dotted, authorized)
            changes.append(f"{dotted}: {final!r} -> {authorized!r}")
        permitted.append(bound)

    if recompute:
        why = ("these fields decide the action, so the agent recomputes with the authorized "
               "values and proposes again; the gate does not substitute them")
    else:
        why = ("every drifted field carries nothing about the decision, so the action below can "
               "be presented against the same authorization")

    return Repair(repairable=True, code=str(code), permitted=tuple(permitted),
                  admissible=admissible, changes=tuple(changes),
                  requires_recompute=tuple(recompute), why=why)
