"""The boundary's answers in words a person can read.

The boundary speaks in rule names and field paths, because that is what a machine needs to enforce
an authority. A person needs the opposite, and so does a model: what moved, what the employer had
set, what would have been accepted instead. This module is the only place that translation happens,
so it happens once and reads the same wherever it is shown.

Nothing here decides anything. It renders, and every sentence it renders comes from the refusal the
boundary already produced. A refusal line that does not fit a known rule is still rendered, in
plainer terms, rather than dropped.
"""

from __future__ import annotations

from . import classes


def plain_refusal(answer: dict) -> list[str]:
    """The refusal in words, without the rule names the boundary uses internally."""
    lines = []
    for item in (answer.get("drift") or []):
        field = str(item.get("field", "")).removeprefix("parameters.")
        was, now, rule = item.get("authorized"), item.get("final"), item.get("rule")
        if rule == "MAY_NOT_CHANGE":
            lines.append(f"{field} may not change. The employer set it to {was}, and the action "
                         f"presented {now}.")
        elif rule == "MAY_NOT_INCREASE":
            lines.append(f"{field} may not rise. The employer allowed no more than {was}, and the "
                         f"action presented {now}.")
        elif rule == "MAY_ONLY_SHRINK":
            lines.append(f"{field} is a list the employer froze. Nothing may be added, and {now} "
                         f"was added.")
        elif rule == "MAY_NOT_GROW":
            lines.append(f"{field} was never authorized, and the action carried it anyway.")
        else:
            lines.append(f"{field} moved from {was} to {now}, which the employer did not allow.")
    return lines or ["The action did not match what the employer authorized."]


BOUND_PHRASING = {
    "equals": "{field} has to be presented as the employer set it: {value}.",
    "at_most": "{field} may be presented at or below {value}.",
    "subset_of": "{field} may only be narrowed from {value}.",
}


def plain_bound(bound) -> list[str]:
    """What the action has to look like instead, in the same plain voice."""
    lines = []
    for entry in bound:
        field = str(entry.get("field", "")).removeprefix("parameters.")
        if entry.get("must_be_absent"):
            lines.append(f"{field} has to be left out of the action.")
            continue
        for key, template in BOUND_PHRASING.items():
            if key in entry:
                lines.append(template.format(field=field, value=entry[key]))
                break
    return lines


def plain_authority(action_class: str) -> str:
    """What the employer bound, in words: the fields, and what may happen to each."""
    entry = classes.get(action_class)
    lines = [entry["purpose"] + "."]
    for dotted, rule in entry["held_by"].items():
        lines.append(f"  {dotted}: {classes.RULE_TABLE[rule]}")
    return "\n".join(lines)
