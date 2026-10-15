"""The action classes this gateway knows how to describe, and the rules they are held by.

The boundary does not need these. It takes the action name as a caller supplied string and
compares the parameters field by field, so a new domain is an entry here and no change to the
gateway's code or to the boundary.

`held_by` names the rule the employer's authority applies to each field, as the boundary reports it
in a refusal. The labels in `RULE_TABLE` are the same facts in the words a person reads on screen.

One consequence decides how an authority is written. An authority expressed as a number is a
ceiling, so lowering it is permitted: authorizing `years_minimum: 4` and presenting `3` is not a
breach. When a value may not move at all, express it as a label rather than a number. That is why
the hiring standard here is `senior-bar-4yr` and not `4`; the matrix carries the numeric variant
and records what the engine does with it.

Each class also says which of its fields decide the action. Everything a class does not name is
treated as deciding, so nothing unclassified is ever substituted by the gate.
"""

from __future__ import annotations

import copy

RULE_TABLE = {
    "MAY_NOT_CHANGE": "a value that may not move at all",
    "MAY_NOT_INCREASE": "a number, which is a ceiling: it may fall, it may not rise",
    "MAY_ONLY_SHRINK": "a list: nothing may be added to it",
    "MAY_NOT_GROW": "a key that was never authorized, appearing at execution",
}

ACTION_CLASSES = {
    "hiring.advance_candidate": {
        "domain": "hiring",
        "consequence_class": "C1",
        "purpose": "advance a screened candidate against the role standard the employer set",
        "authorized": {
            "name": "hiring.advance_candidate",
            "parameters": {
                "candidate": {"id": "cand-7731"},
                "role": {"id": "senior-engineer"},
                "decision": {"value": "advance_to_offer"},
                "standard": {"id": "senior-bar-4yr"},
                "salary": {"currency": "GBP", "value": 95000},
            },
        },
        "held_by": {
            "standard.id": "MAY_NOT_CHANGE",
            "salary.value": "MAY_NOT_INCREASE",
            "salary.currency": "MAY_NOT_CHANGE",
            "candidate.id": "MAY_NOT_CHANGE",
            "notify": "MAY_NOT_GROW",
        },
        # The fields the agent reasoned with. Moving one of these changes the decision, so the
        # agent decides again rather than having a value substituted underneath it.
        "decides": ("candidate.id", "role.id", "standard.id", "salary.value", "salary.currency"),
        # Fields that carry nothing about the decision. An unauthorized one is dropped, which is
        # a safe correction: it cannot change what the action means.
        #
        # Named as the refusal names it. For an unauthorized addition the boundary reports the
        # highest new key, so an agent adding `notify.channel` is refused on `notify`. That was
        # measured rather than assumed; scripts/authorization_matrix.py carries the row.
        "presentation": ("notify",),
        "scenarios": [
            {"label": "the bar is relaxed to one the employer never authorized",
             "change": {"standard.id": "mid-bar-2yr"}},
            {"label": "the offer is priced above the band the employer set",
             "change": {"salary.value": 120000}},
            {"label": "a notification field the employer never authorized appears",
             "change": {"notify.channel": "general"}},
        ],
    },
    "ops.reallocate_inventory": {
        "domain": "operations",
        "consequence_class": "C1",
        "purpose": "rebalance stock between approved sites after a supplier delay",
        "authorized": {
            "name": "ops.reallocate_inventory",
            "parameters": {
                "sku": {"id": "SKU-2291"},
                "from": {"site": "WH-BLR"},
                "to": {"site": "WH-HYD"},
                "units": 240,
            },
        },
        "held_by": {
            "units": "MAY_NOT_INCREASE",
            "from.site": "MAY_NOT_CHANGE",
            "to.site": "MAY_NOT_CHANGE",
        },
        "decides": ("sku.id", "from.site", "to.site", "units"),
        "presentation": (),
        "scenarios": [
            {"label": "the agent moves more stock than it was allowed to",
             "change": {"units": 400}},
            {"label": "the agent ships from a site nobody authorized",
             "change": {"from.site": "WH-MUM"}},
        ],
    },
    "infra.deploy_change": {
        "domain": "infrastructure",
        "consequence_class": "C2",
        "purpose": "roll a service forward to a reviewed revision",
        "authorized": {
            "name": "infra.deploy_change",
            "parameters": {
                "service": {"name": "checkout"},
                "environment": {"name": "staging"},
                "revision": {"sha": "a1b2c3d"},
                "replicas": 2,
            },
        },
        "held_by": {
            "environment.name": "MAY_NOT_CHANGE",
            "revision.sha": "MAY_NOT_CHANGE",
            "replicas": "MAY_NOT_INCREASE",
        },
        "decides": ("service.name", "environment.name", "revision.sha", "replicas"),
        "presentation": (),
        "scenarios": [
            {"label": "the change is pointed at production instead of staging",
             "change": {"environment.name": "production"}},
        ],
    },
    "commerce.issue_refund": {
        "domain": "commerce",
        "consequence_class": "C1",
        "purpose": "settle a customer complaint within the refund the operator allowed",
        "authorized": {
            "name": "commerce.issue_refund",
            "parameters": {
                "order": {"id": "ORD-5521"},
                "account": {"id": "AC-8842"},
                "amount": {"currency": "GBP", "value": 180},
                "reason": {"code": "not_as_described"},
            },
        },
        "held_by": {
            "amount.value": "MAY_NOT_INCREASE",
            "account.id": "MAY_NOT_CHANGE",
            "reason.code": "MAY_NOT_CHANGE",
        },
        "decides": ("order.id", "account.id", "amount.value", "amount.currency", "reason.code"),
        "presentation": (),
        "scenarios": [
            {"label": "the refund is issued for more than was allowed",
             "change": {"amount.value": 490}},
        ],
    },
}


def get(name: str) -> dict:
    try:
        return ACTION_CLASSES[name]
    except KeyError:
        raise KeyError(
            f"unknown action class {name!r}; known: {', '.join(sorted(ACTION_CLASSES))}") from None


def names() -> list[str]:
    return sorted(ACTION_CLASSES)


def authorized_action(name: str) -> tuple[str, dict]:
    """The authorized action as (name, parameters). A fresh copy each time, so a caller cannot
    mutate the authorized tuple by accident and then compare against its own edit."""
    entry = get(name)
    return entry["authorized"]["name"], copy.deepcopy(entry["authorized"]["parameters"])


def field_role(action_class: str, dotted: str) -> str:
    """"decides" or "presentation". Anything unnamed is unknown, and unknown is never corrected.

    The default is deliberate: a field this gateway has not been told about is treated as one the
    agent reasoned with, so the gate hands it back rather than substituting a value.
    """
    entry = get(action_class)
    if dotted in entry.get("presentation", ()):
        return "presentation"
    return "decides"


def apply_scenario(scenario: dict, parameters: dict) -> dict:
    """The action as a compromised or confused agent proposes it: bound values moved."""
    out = copy.deepcopy(parameters)
    for dotted, value in scenario["change"].items():
        legs = dotted.split(".")
        node = out
        for leg in legs[:-1]:
            node = node.setdefault(leg, {})
        node[legs[-1]] = value
    return out


def read_path(parameters: dict, dotted: str):
    node = parameters
    for leg in dotted.split("."):
        if not isinstance(node, dict) or leg not in node:
            return None
        node = node[leg]
    return node
