"""The gate: one route from a proposal to a state change.

The agent may reason, call tools and prepare whatever it likes. The only way an action reaches
the downstream system is through this object, which asks the boundary twice: once for the
authority, and once immediately before the write, with the action as it is about to happen.

When the boundary refuses and the refusal is repair-shaped, the gate does not stop there. It
computes the admissible action from the refusal (deterministically, see repair.py), presents
that against the same authorization, and only writes when the boundary clears it. A refusal
spends nothing, so the corrected action runs under the authorization the human already gave,
and the record shows the attempt, the refusal and the correction as one story.

Nothing here decides anything. It carries the boundary's answer to the store, and it refuses to
carry anything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import classes, repair
from .boundary import Boundary, BoundaryError
from .store import Clearance, Store

EXECUTED = "EXECUTED"
REPAIRED_THEN_EXECUTED = "REPAIRED_THEN_EXECUTED"
REFUSED = "REFUSED"


@dataclass
class Attempt:
    presented: dict
    answer: dict

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
    row: dict | None = None
    note: str = ""

    @property
    def wrote(self) -> bool:
        return self.row is not None


class Gate:
    """One instance per action class. `boundary` is anything with the two methods, so the
    tests can run the whole gate with no network."""

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
        receipt = self.bind_the_authority()
        receipt_id = receipt.get("receipt_id")
        if not receipt_id:
            raise BoundaryError(0, {"code": "NO_RECEIPT",
                                    "message": "the boundary answered without a receipt id",
                                    "answer": receipt})
        outcome = Outcome(status=REFUSED, receipt_id=receipt_id, authorized=self.authorized)

        answer = self.present(receipt_id, proposed)
        outcome.attempts.append(Attempt(presented=proposed, answer=answer))

        if outcome.attempts[-1].cleared:
            outcome.status = EXECUTED
            outcome.row = self.store.write(Clearance.from_answer(answer), proposed, note=note)
            return outcome

        outcome.repair = repair.from_refusal(answer, proposed)
        if not outcome.repair.repairable:
            outcome.note = outcome.repair.why
            return outcome

        corrected = outcome.repair.admissible
        second = self.present(receipt_id, corrected)
        outcome.attempts.append(Attempt(presented=corrected, answer=second))

        if outcome.attempts[-1].cleared:
            outcome.status = REPAIRED_THEN_EXECUTED
            outcome.row = self.store.write(Clearance.from_answer(second), corrected, note=note)
        else:
            outcome.note = ("the corrected action was refused too, which is a defect worth "
                            "reporting rather than a demo")
        return outcome


__all__ = ["Gate", "Outcome", "Attempt", "EXECUTED", "REPAIRED_THEN_EXECUTED", "REFUSED",
           "Boundary", "BoundaryError", "Store"]
