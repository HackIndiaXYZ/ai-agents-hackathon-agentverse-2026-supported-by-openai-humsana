#!/usr/bin/env python3
"""The gate's own tests. No network: the boundary is faked, so every test here is about this
repository's behaviour rather than the service's.

    python3 -m unittest discover -s tests -t .

The engine's behaviour is measured separately and live, by scripts/authorization_matrix.py against
api.humsana.com. Two fakes are used here on purpose:

    ScriptedBoundary    answers exactly what a test hands it, with record shapes copied from real
                        calls. This pins the wire contract.
    LiveShapedBoundary  reproduces the comparison the live engine performs, measured rather than
                        assumed:

                            a number       may not rise      MAY_NOT_INCREASE
                            a list         may add nothing   MAY_ONLY_SHRINK
                            anything else  may not change    MAY_NOT_CHANGE
                            a new key                        MAY_NOT_GROW
                            a bound key that is absent       MAY_NOT_CHANGE

                        This lets the whole authorization matrix run offline. The live matrix
                        script is what keeps the reproduction honest.

What the tests establish, in the order they matter:

1. The gate cannot write an action the boundary did not clear, including a corrected one. Every
   write goes through `Clearance`, which only an answer with a spent authorization can mint.
2. A field that decides the action is never substituted by the gate. The agent decides again.
3. Nothing is written when a refusal is not repaired, when the repair is refused as well, or when
   the agent's second proposal is refused too.
4. The whole authorization matrix holds, not only the first field that failed.
"""

from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humsana_gate import classes, repair                                    # noqa: E402
from humsana_gate.gate import (CORRECTED_THEN_EXECUTED, EXECUTED,           # noqa: E402
                               RECOMPUTE_REQUIRED, REFUSED, Gate)
from humsana_gate.store import Clearance, NoClearance, Store                # noqa: E402

ACTION = "hiring.advance_candidate"

# A refusal exactly as the live boundary answers one, copied from a real call.
DRIFT = {
    "contract": "humsana.authorization/1",
    "receipt_id": "authz_test",
    "binding_digest": "sha256:test",
    "stored_decision": "ALLOW",
    "decision": "HUMAN_CONFIRM",
    "matched": False,
    "consumed": False,
    "code": "EFFECTUATION_DRIFT",
    "drift": [{"field": "parameters.standard.id", "authorized": "senior-bar-4yr",
               "final": "mid-bar-2yr", "rule": "MAY_NOT_CHANGE"}],
    "reasons": [{"code": "EFFECTUATION_DRIFT",
                 "detail": "1 field(s) outside the authorized tuple"}],
}

DRIFT_ADDED_KEY = dict(DRIFT, drift=[{"field": "parameters.notify", "authorized": None,
                                      "final": {"channel": "general"},
                                      "rule": "MAY_NOT_GROW"}])

CLEARED = {"contract": "humsana.authorization/1", "receipt_id": "authz_test",
           "binding_digest": "sha256:test", "code": "MATCHED", "matched": True, "consumed": True}

WITHDRAWN = {"code": "GRANT_ALREADY_USED", "matched": False, "consumed": False,
             "receipt_id": "authz_test"}


def authorized():
    return classes.authorized_action(ACTION)[1]


def presented(**overrides):
    """The authorized action with bound values moved, as a compromised agent would propose it."""
    out = copy.deepcopy(authorized())
    for dotted, value in overrides.items():
        legs = dotted.replace("__", ".").split(".")
        node = out
        for leg in legs[:-1]:
            node = node.setdefault(leg, {})
        node[legs[-1]] = value
    return out


def dropped(dotted):
    out = copy.deepcopy(authorized())
    legs = dotted.split(".")
    node = out
    for leg in legs[:-1]:
        node = node[leg]
    node.pop(legs[-1], None)
    return out


def admissible_of(result) -> dict:
    """The admissible action a repair computed, asserted present: these tests are about it."""
    assert result.admissible is not None, "expected an admissible action"
    return result.admissible


def bound_of(result) -> dict:
    assert result.permitted, "expected at least one bound"
    return result.permitted[0]


class ScriptedBoundary:
    """Answers what a test tells it to, and records what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.presented = []
        self.verified = []

    def verify_action(self, **kwargs):
        self.verified.append(kwargs)
        return {"receipt_id": "authz_test", "decision": "ALLOW",
                "binding": {"digest": "sha256:test"}}

    def effectuate_action(self, *, action_name, parameters, receipt_id=None, grant_id=None):
        self.presented.append(parameters)
        return copy.deepcopy(self.answers.pop(0)) if self.answers else copy.deepcopy(WITHDRAWN)


# --------------------------------------------------------------------------- the engine's side

def compare(authorized_tuple, presented_tuple, prefix=""):
    """The comparison the boundary performs, reproduced from measurement. Returns the drift."""
    drift = []
    for key, value in authorized_tuple.items():
        dotted = f"{prefix}{key}"
        if not isinstance(presented_tuple, dict) or key not in presented_tuple:
            drift.append({"field": dotted, "authorized": value, "final": None,
                          "rule": "MAY_NOT_CHANGE"})
            continue
        final = presented_tuple[key]
        if isinstance(value, dict):
            drift.extend(compare(value, final, prefix=f"{dotted}."))
        elif isinstance(value, list):
            if [item for item in (final or []) if item not in value]:
                drift.append({"field": dotted, "authorized": value, "final": final,
                              "rule": "MAY_ONLY_SHRINK"})
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            if final != value:
                drift.append({"field": dotted, "authorized": value, "final": final,
                              "rule": "MAY_NOT_CHANGE"})
        elif final > value:
            drift.append({"field": dotted, "authorized": value, "final": final,
                          "rule": "MAY_NOT_INCREASE"})
    for key, final in (presented_tuple or {}).items():
        if key not in authorized_tuple:
            drift.append({"field": f"{prefix}{key}", "authorized": None, "final": final,
                          "rule": "MAY_NOT_GROW"})
    return drift


class LiveShapedBoundary:
    """Binds whatever tuple it is handed, exactly as the service does, and compares the way the
    service compares."""

    def __init__(self):
        self.bound = {}
        self.verified = []
        self.presented = []

    def verify_action(self, *, action_name, parameters, **rest):
        self.verified.append((action_name, copy.deepcopy(parameters)))
        self.bound[action_name] = copy.deepcopy(parameters)
        return {"receipt_id": "rcpt-live-shaped", "code": "ALLOW", "matched": True}

    def effectuate_action(self, *, action_name, parameters, receipt_id=None):
        self.presented.append(copy.deepcopy(parameters))
        drift = compare(self.bound.get(action_name, {}), parameters)
        if drift:
            return {"code": "EFFECTUATION_DRIFT", "matched": False, "consumed": False,
                    "drift": drift, "receipt_id": receipt_id}
        return {"code": "MATCHED", "matched": True, "consumed": True, "receipt_id": receipt_id}


class TemporaryLedger(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(), "ledger.jsonl"))


# ------------------------------------------------------------------------ 1. the write is gated

class TestTheWriteIsGated(TemporaryLedger):
    def test_a_proposal_that_matches_is_executed_once(self):
        gate = Gate(ScriptedBoundary(CLEARED), self.store, ACTION)
        outcome = gate.run(authorized())
        self.assertEqual(outcome.status, EXECUTED)
        self.assertTrue(outcome.wrote)
        self.assertEqual(len(self.store.rows()), 1)

    def test_nothing_is_written_when_the_refusal_is_not_repairable(self):
        gate = Gate(ScriptedBoundary(WITHDRAWN), self.store, ACTION)
        outcome = gate.run(presented())
        self.assertEqual(outcome.status, REFUSED)
        self.assertFalse(outcome.wrote)
        self.assertEqual(self.store.rows(), [])

    def test_a_refusal_cannot_mint_a_clearance(self):
        with self.assertRaises(NoClearance):
            Clearance.from_answer(DRIFT)
        with self.assertRaises(NoClearance):
            Clearance.from_answer(WITHDRAWN)

    def test_a_half_cleared_answer_cannot_mint_one_either(self):
        with self.assertRaises(NoClearance):
            Clearance.from_answer({"code": "MATCHED", "matched": True, "consumed": False})

    def test_a_clearance_cannot_be_constructed_directly(self):
        with self.assertRaises(NoClearance):
            Clearance(receipt_id="authz_test", binding_digest="sha256:test", code="MATCHED",
                      at="now")

    def test_a_write_without_a_clearance_is_refused(self):
        with self.assertRaises(NoClearance):
            self.store.write({"receipt_id": "authz_test"}, presented())   # type: ignore[arg-type]

    def test_the_gate_refuses_to_run_an_unknown_action_class(self):
        with self.assertRaises(KeyError):
            Gate(ScriptedBoundary(CLEARED), self.store, "hiring.promote_ceo")


# --------------------------------------------------- 2. a deciding field is never substituted

class TestADecidingFieldIsHandedBack(TemporaryLedger):
    def test_the_gate_hands_the_refusal_back_instead_of_substituting(self):
        boundary = ScriptedBoundary(DRIFT)
        outcome = Gate(boundary, self.store, ACTION).run(presented(standard__id="mid-bar-2yr"))
        self.assertEqual(outcome.status, RECOMPUTE_REQUIRED)
        self.assertIn("standard.id", outcome.recompute)
        # exactly one attempt: the gate presented nothing of its own
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(len(boundary.presented), 1)
        # the admissible action is still computed, because the agent can use it to decide again;
        # what the gate must not do is present it in the agent's place
        assert outcome.repair is not None
        self.assertIsNotNone(outcome.repair.admissible)
        self.assertFalse(outcome.repair.safe_to_apply)
        self.assertNotEqual(boundary.presented[0], outcome.repair.admissible)
        self.assertEqual(self.store.rows(), [])

    def test_the_proposed_action_is_reported_exactly_as_it_arrived(self):
        boundary = ScriptedBoundary(DRIFT)
        proposed = presented(standard__id="mid-bar-2yr")
        outcome = Gate(boundary, self.store, ACTION).run(proposed)
        self.assertEqual(boundary.presented[0], proposed)
        assert outcome.repair is not None
        bound = bound_of(outcome.repair)
        self.assertEqual(bound["field"], "standard.id")
        self.assertEqual(bound["equals"], "senior-bar-4yr")
        self.assertEqual(bound["carries"], "decides")

    def test_the_agent_recomputes_and_the_second_proposal_clears(self):
        boundary = ScriptedBoundary(DRIFT, CLEARED)
        gate = Gate(boundary, self.store, ACTION)
        outcome = gate.run(presented(standard__id="mid-bar-2yr"))
        self.assertEqual(outcome.status, RECOMPUTE_REQUIRED)
        # the agent uses the bar the refusal named and decides again
        outcome = gate.resume(outcome, presented(standard__id="senior-bar-4yr"))
        self.assertEqual(outcome.status, EXECUTED)
        self.assertTrue(outcome.wrote)
        self.assertEqual(len(self.store.rows()), 1)
        self.assertEqual(len(outcome.attempts), 2)

    def test_the_recompute_runs_on_the_same_authorization(self):
        boundary = ScriptedBoundary(DRIFT, CLEARED)
        gate = Gate(boundary, self.store, ACTION)
        outcome = gate.run(presented(standard__id="mid-bar-2yr"))
        receipt = outcome.receipt_id
        outcome = gate.resume(outcome, presented())
        self.assertEqual(outcome.receipt_id, receipt)
        self.assertEqual(len(boundary.verified), 1)

    def test_nothing_is_written_when_the_second_proposal_also_violates(self):
        boundary = ScriptedBoundary(DRIFT, DRIFT)
        gate = Gate(boundary, self.store, ACTION)
        outcome = gate.run(presented(standard__id="mid-bar-2yr"))
        outcome = gate.resume(outcome, presented(standard__id="mid-bar-2yr",
                                                 salary__value=120000))
        self.assertEqual(outcome.status, RECOMPUTE_REQUIRED)
        self.assertFalse(outcome.wrote)
        self.assertEqual(self.store.rows(), [])
        self.assertEqual(len(outcome.attempts), 2)

    def test_an_outcome_that_already_wrote_cannot_be_reused(self):
        gate = Gate(ScriptedBoundary(CLEARED, CLEARED), self.store, ACTION)
        outcome = gate.run(authorized())
        with self.assertRaises(RuntimeError):
            gate.resume(outcome, authorized())


# --------------------------------------------------- 3. the correction that is safe to apply

class TestASafeCorrectionIsApplied(TemporaryLedger):
    def test_an_unauthorized_presentation_field_is_dropped_and_the_action_runs(self):
        boundary = ScriptedBoundary(DRIFT_ADDED_KEY, CLEARED)
        outcome = Gate(boundary, self.store, ACTION).run(presented(notify__channel="general"))
        self.assertEqual(outcome.status, CORRECTED_THEN_EXECUTED)
        self.assertTrue(outcome.wrote)
        self.assertEqual(len(outcome.attempts), 2)
        self.assertNotIn("notify", outcome.attempts[1].presented)
        self.assertEqual(outcome.attempts[1].presented, authorized())
        self.assertEqual(len(self.store.rows()), 1)

    def test_the_safe_correction_still_goes_through_the_boundary(self):
        boundary = ScriptedBoundary(DRIFT_ADDED_KEY, CLEARED)
        Gate(boundary, self.store, ACTION).run(presented(notify__channel="general"))
        self.assertEqual(len(boundary.presented), 2)
        self.assertEqual(len(boundary.verified), 1)

    def test_a_corrected_action_that_is_refused_writes_nothing(self):
        boundary = ScriptedBoundary(DRIFT_ADDED_KEY, DRIFT_ADDED_KEY)
        outcome = Gate(boundary, self.store, ACTION).run(presented(notify__channel="general"))
        self.assertEqual(outcome.status, REFUSED)
        self.assertIn("defect", outcome.note)
        self.assertEqual(self.store.rows(), [])

    def test_a_real_refusal_is_repaired_to_the_authorized_value(self):
        got = repair.from_refusal(DRIFT, presented(standard__id="mid-bar-2yr"),
                                  decides=("standard.id",), presentation=("notify.channel",))
        self.assertTrue(got.repairable)
        self.assertFalse(got.safe_to_apply)
        self.assertEqual(got.requires_recompute, ("standard.id",))
        self.assertEqual(admissible_of(got)["standard"]["id"], "senior-bar-4yr")
        self.assertEqual(bound_of(got)["equals"], "senior-bar-4yr")

    def test_a_ceiling_permits_at_most_the_authorized_value(self):
        answer = dict(DRIFT, drift=[{"field": "parameters.salary.value", "authorized": 95000,
                                     "final": 120000, "rule": "MAY_NOT_INCREASE"}])
        got = repair.from_refusal(answer, presented(salary__value=120000),
                                  decides=("salary.value",))
        self.assertEqual(admissible_of(got)["salary"]["value"], 95000)
        self.assertEqual(bound_of(got)["at_most"], 95000)

    def test_a_key_that_was_never_authorized_is_dropped(self):
        action = presented(notify__channel="general")
        got = repair.from_refusal(DRIFT_ADDED_KEY, action, presentation=("notify",))
        self.assertNotIn("notify", admissible_of(got))
        self.assertTrue(bound_of(got)["must_be_absent"])
        self.assertTrue(got.safe_to_apply)

    def test_only_the_drifted_fields_move(self):
        got = repair.from_refusal(DRIFT, presented(standard__id="mid-bar-2yr"),
                                  decides=("standard.id",))
        admissible = admissible_of(got)
        for field in ("candidate", "role", "decision", "salary"):
            self.assertEqual(admissible[field], authorized()[field], field)

    def test_an_unknown_rule_refuses_to_guess(self):
        answer = dict(DRIFT, drift=[{"field": "parameters.x", "authorized": 1, "final": 2,
                                     "rule": "MAY_NOT_SOMETHING_NEW"}])
        got = repair.from_refusal(answer, presented())
        self.assertFalse(got.repairable)
        self.assertIsNone(got.admissible)

    def test_a_refusal_about_the_authority_is_not_repairable(self):
        got = repair.from_refusal(WITHDRAWN, presented())
        self.assertFalse(got.repairable)
        self.assertIn("authority rather than the action", got.why)

    def test_an_unclassified_field_counts_as_deciding(self):
        self.assertEqual(classes.field_role(ACTION, "delivery.window"), "decides")
        self.assertEqual(classes.field_role(ACTION, "notify"), "presentation")


# ------------------------------------------------------------------- 4. the whole matrix holds

# (label, the overrides that make the proposal, the status it must end in)
MATRIX = [
    ("the authorized action, presented as authorized", {}, EXECUTED),
    ("a bar the employer never authorized", {"standard__id": "mid-bar-2yr"}, RECOMPUTE_REQUIRED),
    ("the offer below the ceiling", {"salary__value": 90000}, EXECUTED),
    ("the offer above the ceiling", {"salary__value": 120000}, RECOMPUTE_REQUIRED),
    ("a different candidate altogether", {"candidate__id": "cand-9999"}, RECOMPUTE_REQUIRED),
    ("the currency changed", {"salary__currency": "USD"}, RECOMPUTE_REQUIRED),
    ("an extra field that was never authorized", {"notify__channel": "general"},
     CORRECTED_THEN_EXECUTED),
    ("two deciding fields moved at once",
     {"standard__id": "mid-bar-2yr", "salary__value": 120000}, RECOMPUTE_REQUIRED),
]


class TestTheWholeAuthorizationMatrix(TemporaryLedger):
    def gate(self):
        return Gate(LiveShapedBoundary(), self.store, ACTION)

    def test_every_row_ends_in_the_status_it_must(self):
        for label, overrides, expected in MATRIX:
            with self.subTest(row=label):
                self.setUp()
                outcome = self.gate().run(presented(**overrides))
                self.assertEqual(outcome.status, expected, label)
                if expected in (EXECUTED, CORRECTED_THEN_EXECUTED):
                    self.assertTrue(outcome.wrote, f"{label}: cleared but did not write")
                    self.assertEqual(len(self.store.rows()), 1,
                                     f"{label}: wrong number of writes")
                else:
                    self.assertFalse(outcome.wrote, f"{label}: refused but wrote anyway")
                    self.assertEqual(self.store.rows(), [],
                                     f"{label}: a refused action reached the store")

    def test_a_field_that_was_not_moved_cannot_buy_a_pass_for_one_that_was(self):
        outcome = self.gate().run(presented(standard__id="mid-bar-2yr", salary__value=95000))
        self.assertEqual(outcome.status, RECOMPUTE_REQUIRED)
        self.assertEqual([entry["field"] for entry in outcome.attempts[0].answer["drift"]],
                         ["standard.id"])

    def test_every_drifted_field_is_reported_not_only_the_first(self):
        outcome = self.gate().run(presented(standard__id="mid-bar-2yr", salary__value=120000))
        fields = [entry["field"] for entry in outcome.attempts[0].answer["drift"]]
        self.assertEqual(sorted(fields), ["salary.value", "standard.id"])
        self.assertEqual(sorted(outcome.recompute), ["salary.value", "standard.id"])
        self.assertEqual(self.store.rows(), [])

    def test_a_bound_key_that_is_absent_entirely_is_reported(self):
        outcome = self.gate().run(dropped("salary"))
        self.assertEqual(outcome.attempts[0].answer["code"], "EFFECTUATION_DRIFT")
        self.assertIn("salary", [e["field"] for e in outcome.attempts[0].answer["drift"]])
        self.assertFalse(outcome.wrote)

    def test_an_agent_that_recomputes_with_the_authorized_value_gets_through(self):
        gate = self.gate()
        outcome = gate.run(presented(standard__id="mid-bar-2yr"))
        self.assertEqual(outcome.status, RECOMPUTE_REQUIRED)
        outcome = gate.resume(outcome, presented())
        self.assertEqual(outcome.status, EXECUTED)
        self.assertEqual(len(self.store.rows()), 1)

    def test_a_recompute_that_ignores_the_bound_is_refused_again(self):
        gate = self.gate()
        outcome = gate.run(presented(standard__id="mid-bar-2yr"))
        outcome = gate.resume(outcome, presented(standard__id="junior-bar-0yr"))
        self.assertEqual(outcome.status, RECOMPUTE_REQUIRED)
        self.assertEqual(self.store.rows(), [])


# ------------------------------------------------------------- 5. what the classes must say

class TestTheClassesAreComplete(unittest.TestCase):
    def test_every_class_declares_a_tuple_a_purpose_and_rules(self):
        for name in classes.names():
            entry = classes.get(name)
            self.assertTrue(entry["purpose"].strip(), name)
            self.assertIn(entry["consequence_class"], ("C1", "C2", "C3"), name)
            self.assertTrue(entry["authorized"]["parameters"], name)
            self.assertTrue(entry["held_by"], name)
            self.assertTrue(entry["decides"], name)
            self.assertTrue(entry["scenarios"], name)

    def test_a_field_claimed_as_held_is_in_the_tuple_or_must_be_absent(self):
        for name in classes.names():
            entry = classes.get(name)
            for dotted, rule in entry["held_by"].items():
                value = classes.read_path(entry["authorized"]["parameters"], dotted)
                if rule == "MAY_NOT_GROW":
                    self.assertIsNone(value, f"{name}.{dotted} cannot be authorized and absent")
                else:
                    self.assertIsNotNone(value, f"{name}.{dotted} is held but is not in the action")

    def test_every_deciding_field_is_a_field_of_the_authorized_action(self):
        for name in classes.names():
            entry = classes.get(name)
            for dotted in entry["decides"]:
                self.assertIsNotNone(
                    classes.read_path(entry["authorized"]["parameters"], dotted),
                    f"{name}.{dotted} decides the action but is not in it")

    def test_presentation_fields_are_not_also_deciding(self):
        for name in classes.names():
            entry = classes.get(name)
            self.assertEqual(set(entry["decides"]) & set(entry["presentation"]), set(), name)

    def test_every_scenario_moves_something(self):
        for name in classes.names():
            entry = classes.get(name)
            for scenario in entry["scenarios"]:
                moved = classes.apply_scenario(scenario, entry["authorized"]["parameters"])
                self.assertNotEqual(moved, entry["authorized"]["parameters"],
                                    f"{name}: {scenario}")

    def test_every_rule_has_a_bound(self):
        for rule in ("MAY_NOT_CHANGE", "MAY_NOT_INCREASE", "MAY_ONLY_SHRINK", "MAY_NOT_GROW"):
            self.assertIn(rule, repair.BOUNDS)


if __name__ == "__main__":
    unittest.main()
