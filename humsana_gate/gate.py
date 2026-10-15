"""The gate: one route from a proposal to a state change.

The agent may reason, call tools and prepare whatever it likes. The only way an action reaches
the downstream system is through this object, which asks the boundary twice: once for the
authority, and once immediately before the write, with the action as it is about to happen. A
write happens only on an answer in which the boundary cleared the action and spent the
authorization, and `store.Clearance` will not accept anything else.

When the boundary refuses, the gate does not stop. It reads the refusal and splits it:

    the drifted field carries nothing about the decision   the gate drops or restores it, presents
                                                           the admissible action, and writes if that
                                                           clears. One authorization, two checks.

    the drifted field decides the action                   the gate does NOT substitute a value.
                                                           It hands the refusal back with the
                                                           bound, the agent decides again, and the
                                                           action it produces is checked from
                                                           scratch on the same receipt.

That second branch is the one that matters. A corrected payload is not a corrected decision: an
agent that picked the mid-level bar and then has `standard.id` rewritten under it has not changed
its mind, and a gate that quietly rewrote the field would be laundering the decision while
reporting a clean check. So the substitution only happens where the class says the field carries
nothing about the decision, and everywhere else the agent has to do the work.

Nothing here decides anything. It carries the boundary's answer to the store, and refuses to
carry anything else.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from . import classes, repair
from .boundary import Boundary, BoundaryError
from .store import Clearance, Store

EXECUTED = "EXECUTED"
CORRECTED_THEN_EXECUTED = "CORRECTED_THEN_EXECUTED"
RECOMPUTE_REQUIRED = "RECOMPUTE_REQUIRED"
REFUSED = "REFUSED"


@dataclass
class Attempt:
    presented: dict
    answer: dict

    def __post_init__(self):
        # Recorded by value. The agent may mutate the dict it handed us while it decides again,
        # and a record that changes after the fact is not a record.
        self.presented = copy.deepcopy(self.presented)

    @property
    def cleared(self) -> bool:
        answer = self.answer
        return (answer.get("code") == "MATCHED"
                and answer.get("matched") is True
                and answer.get("consumed") is True)


@dataclass
class Outcome:
    status: str
    receipt_id: str
    authorized: dict
    attempts: list[Attempt] = field(default_factory=list)
    repair: repair.Repair | None = None
    recompute: tuple = ()
    row: dict | None = None
    note: str = ""

    @property
    def wrote(self) -> bool:
        return self.row is not None

    @property
    def first_refusal(self) -> dict | None:
        for attempt in self.attempts:
            if not attempt.cleared:
                return attempt.answer
        return None


class Gate:
    """One instance per action class. `boundary` is anything with the two methods, so the tests
    can run the whole gate with no network."""

    def __init__(self, boundary, store: Store, action_class: str, *,
                 actor_id: str = "agent-1", principal_reference: str = "employer:8842"):
        self.boundary = boundary
        self.store = store
        self.action_class = action_class
        self.entry = classes.get(action_class)
        self.actor_id = actor_id
        self.principal_reference = principal_reference

    @property
    def authorized(self) -> dict:
        return classes.authorized_action(self.action_class)[1]

    # ------------------------------------------------------------- the two calls

    def bind_the_authority(self) -> dict:
        """What the human allowed, bound and signed. This is the tuple everything is held to."""
        name, parameters = classes.authorized_action(self.action_class)
        return self.boundary.verify_action(
            action_name=name, parameters=parameters, purpose=self.entry["purpose"],
            consequence_class=self.entry["consequence_class"],
            actor_id=self.actor_id, principal_reference=self.principal_reference)

    def present(self, receipt_id: str, parameters: dict) -> dict:
        """The check immediately before the write, with the action as it is about to happen."""
        name, _ = classes.authorized_action(self.action_class)
        return self.boundary.effectuate_action(action_name=name, parameters=parameters,
                                               receipt_id=receipt_id)

    # ------------------------------------------------------------------- the run

    def run(self, proposed: dict, *, note: str = "") -> Outcome:
        """One authorization, then the agent's proposal. May end at RECOMPUTE_REQUIRED, in which
        case the agent decides again and offers the new action to `resume` under the same
        receipt."""
        receipt = self.bind_the_authority()
        receipt_id = receipt.get("receipt_id")
        if not receipt_id:
            raise BoundaryError(0, {"code": "NO_RECEIPT",
                                    "message": "the boundary answered without a receipt id",
                                    "answer": receipt})
        outcome = Outcome(status=REFUSED, receipt_id=receipt_id, authorized=self.authorized)
        return self._attempt(outcome, proposed, note)

    def resume(self, outcome: Outcome, proposed: dict, *, note: str = "") -> Outcome:
        """The agent has decided again, using the authorized values the refusal named. The new
        action is checked from scratch, on the same authorization."""
        if outcome.wrote:
            raise RuntimeError("this outcome already wrote; that action is done")
        return self._attempt(outcome, proposed, note)

    def _attempt(self, outcome: Outcome, proposed: dict, note: str) -> Outcome:
        answer = self.present(outcome.receipt_id, proposed)
        outcome.attempts.append(Attempt(presented=proposed, answer=answer))
        attempt = outcome.attempts[-1]

        if attempt.cleared:
            return self._write(outcome, proposed, attempt.answer, note)

        outcome.repair = repair.from_refusal(
            answer, proposed,
            decides=self.entry.get("decides", ()),
            presentation=self.entry.get("presentation", ()))

        if not outcome.repair.repairable:
            outcome.note = outcome.repair.why
            return outcome

        if not outcome.repair.safe_to_apply:
            # The drifted field decides the action. Substituting the authorized value here would
            # change the payload without changing the decision behind it, so the gate stops and
            # hands the work back instead.
            outcome.status = RECOMPUTE_REQUIRED
            outcome.recompute = outcome.repair.requires_recompute
            outcome.note = outcome.repair.why
            return outcome

        # Every drifted field carries nothing about the decision, so the authorized value is the
        # only admissible one and presenting it changes nothing the agent meant.
        corrected = outcome.repair.admissible
        if corrected is None:  # cannot happen: a repairable refusal always carries one
            raise RuntimeError("a repairable refusal arrived without an admissible action")
        second = self.present(outcome.receipt_id, corrected)
        outcome.attempts.append(Attempt(presented=corrected, answer=second))
        second_attempt = outcome.attempts[-1]

        if second_attempt.cleared:
            return self._write(outcome, corrected, second_attempt.answer, note,
                               status=CORRECTED_THEN_EXECUTED)
        outcome.note = ("the corrected action was refused too, which is a defect worth reporting "
                        "rather than a demo")
        return outcome

    def _write(self, outcome: Outcome, action: dict, answer: dict, note: str,
               status: str = EXECUTED) -> Outcome:
        # The only door. Clearance refuses to exist for an answer in which the boundary did not
        # clear the action and spend the authorization.
        outcome.row = self.store.write(Clearance.from_answer(answer), action, note=note)
        outcome.status = status
        return outcome


__all__ = ["Gate", "Outcome", "Attempt", "EXECUTED", "CORRECTED_THEN_EXECUTED",
           "RECOMPUTE_REQUIRED", "REFUSED", "Boundary", "BoundaryError", "Store"]
