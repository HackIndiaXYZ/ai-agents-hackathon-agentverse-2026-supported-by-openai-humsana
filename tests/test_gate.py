#!/usr/bin/env python3
"""The gate's own tests. No network: the boundary is faked, so every test here is about this
repository's behaviour rather than the service's.

    python3 -m unittest discover -s tests -t .

The live loop is proven separately, by scripts/demo_hiring.py, against api.humsana.com.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humsana_gate import classes, repair                        # noqa: E402
from humsana_gate.gate import EXECUTED, REPAIRED_THEN_EXECUTED, Gate  # noqa: E402
from humsana_gate.store import Clearance, NoClearance, Store    # noqa: E402

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
    "reasons": [{"code": "EFFECTUATION_DRIFT", "detail": "1 field(s) outside the authorized tuple"}],
}

CLEARED = {"contract": "humsana.authorization/1", "receipt_id": "authz_test",
           "binding_digest": "sha256:test", "code": "MATCHED", "matched": True, "consumed": True}

WITHDRAWN = {"code": "GRANT_ALREADY_USED", "matched": False, "consumed": False,
             "receipt_id": "authz_test"}


def presented():
    return {"candidate": {"id": "cand-7731"}, "role": {"id": "senior-engineer"},
            "decision": {"value": "advance_to_offer"}, "standard": {"id": "mid-bar-2yr"},
            "salary": {"currency": "GBP", "value": 95000}}


class FakeBoundary:
    """Answers what a test tells it to, and records what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.presented = []

    def verify_action(self, **kwargs):
        return {"receipt_id": "authz_test", "decision": "ALLOW", "binding": {"digest": "sha256:test"}}

    def effectuate_action(self, *, action_name, parameters, receipt_id=None, grant_id=None):
        self.presented.append(parameters)
        return self.answers.pop(0) if self.answers else WITHDRAWN


class TestTheRepair(unittest.TestCase):
    """The admissible action is computed from the refusal, and it never widens anything."""

    def test_a_name_that_moved_is_returned_to_the_authorized_one(self):
        got = repair.from_refusal(DRIFT, presented())
        self.assertTrue(got.repairable)
        self.assertEqual(got.admissible["standard"]["id"], "senior-bar-4yr")
        self.assertEqual(got.permitted[0]["rule"], "MAY_NOT_CHANGE")
        self.assertEqual(got.permitted[0]["equals"], "senior-bar-4yr")

    def test_a_ceiling_permits_at_most_the_authorized_value(self):
        answer = dict(DRIFT, drift=[{"field": "parameters.salary.value", "authorized": 95000,
                                     "final": 120000, "rule": "MAY_NOT_INCREASE"}])
        got = repair.from_refusal(answer, presented())
        self.assertEqual(got.admissible["salary"]["value"], 95000)
        self.assertEqual(got.permitted[0]["at_most"], 95000)

    def test_a_key_that_was_never_authorized_is_dropped(self):
        answer = dict(DRIFT, drift=[{"field": "parameters.channel", "authorized": None,
                                     "final": "general", "rule": "MAY_NOT_GROW"}])
        action = presented()
        action["channel"] = "general"
        got = repair.from_refusal(answer, action)
        self.assertNotIn("channel", got.admissible)
        self.assertTrue(got.permitted[0]["must_be_absent"])

    def test_only_the_drifted_fields_move(self):
        got = repair.from_refusal(DRIFT, presented())
        self.assertEqual(got.admissible["salary"], presented()["salary"])
        self.assertEqual(got.admissible["candidate"], presented()["candidate"])
        self.assertEqual(got.admissible["role"], presented()["role"])

    def test_a_wasted_grant_is_not_repairable(self):
        got = repair.from_refusal(WITHDRAWN, presented())
        self.assertFalse(got.repairable)
        self.assertIsNone(got.admissible)
        self.assertIn("authority rather than the action", got.why)

    def test_an_unknown_rule_refuses_to_guess(self):
        answer = dict(DRIFT, drift=[{"field": "parameters.x", "authorized": 1, "final": 2,
                                     "rule": "MAY_NOT_DRIFT" }])
        got = repair.from_refusal(answer, presented())
        self.assertFalse(got.repairable)

    def test_every_rule_has_a_bound(self):
        for rule in ("MAY_NOT_CHANGE", "MAY_NOT_INCREASE", "MAY_ONLY_SHRINK", "MAY_NOT_GROW"):
            self.assertIn(rule, repair.BOUNDS)


class TestTheStore(unittest.TestCase):
    """The write guard, proven from both sides."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "ledger.jsonl")
        self.store = Store(self.path)

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
            Clearance(receipt_id="authz_test", binding_digest="sha256:test", code="MATCHED", at="now")

    def test_a_write_without_a_clearance_is_refused(self):
        with self.assertRaises(NoClearance):
            self.store.write({"receipt_id": "authz_test"}, presented())   # type: ignore[arg-type]

    def test_a_cleared_answer_writes_one_row(self):
        row = self.store.write(Clearance.from_answer(CLEARED), presented(), note="test")
        self.assertEqual(row["receipt_id"], "authz_test")
        self.assertEqual(len(self.store.rows()), 1)
        self.assertEqual(self.store.rows()[0]["action"]["standard"]["id"], "mid-bar-2yr")


class TestTheGate(unittest.TestCase):
    """One route from a proposal to a write, and the door it uses."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "ledger.jsonl")
        self.store = Store(self.path)

    def test_a_proposal_that_matches_is_executed_once(self):
        gate = Gate(FakeBoundary(CLEARED), self.store, "hiring.advance_candidate")
        outcome = gate.run(gate.authorized)
        self.assertEqual(outcome.status, EXECUTED)
        self.assertTrue(outcome.wrote)
        self.assertEqual(len(self.store.rows()), 1)

    def test_a_refusal_is_repaired_and_then_executed_under_the_same_authorization(self):
        boundary = FakeBoundary(DRIFT, CLEARED)
        gate = Gate(boundary, self.store, "hiring.advance_candidate")
        outcome = gate.run(presented())

        self.assertEqual(outcome.status, REPAIRED_THEN_EXECUTED)
        self.assertEqual(len(outcome.attempts), 2)
        # the second attempt is the admissible action, and only that field moved
        self.assertEqual(outcome.attempts[1].presented["standard"]["id"], "senior-bar-4yr")
        self.assertEqual(boundary.presented[0]["standard"]["id"], "mid-bar-2yr")
        self.assertEqual(len(self.store.rows()), 1)

    def test_nothing_is_written_when_the_refusal_is_not_repairable(self):
        gate = Gate(FakeBoundary(WITHDRAWN), self.store, "hiring.advance_candidate")
        outcome = gate.run(presented())
        self.assertEqual(outcome.status, "REFUSED")
        self.assertFalse(outcome.wrote)
        self.assertEqual(self.store.rows(), [])

    def test_nothing_is_written_when_even_the_repair_is_refused(self):
        gate = Gate(FakeBoundary(DRIFT, DRIFT), self.store, "hiring.advance_candidate")
        outcome = gate.run(presented())
        self.assertEqual(outcome.status, "REFUSED")
        self.assertEqual(self.store.rows(), [])
        self.assertIn("defect", outcome.note)

    def test_the_gate_refuses_to_run_an_unknown_action_class(self):
        with self.assertRaises(KeyError):
            Gate(FakeBoundary(CLEARED), self.store, "hiring.promote_ceo")


class TestTheClasses(unittest.TestCase):
    def test_every_class_declares_the_fields_it_is_held_by(self):
        for name in classes.names():
            entry = classes.get(name)
            self.assertTrue(entry["authorized"]["parameters"])
            self.assertTrue(entry["held_by"])
            self.assertTrue(entry["scenarios"])
            for field in entry["held_by"]:
                self.assertIsNotNone(classes.read_path(entry["authorized"]["parameters"], field),
                                     f"{name}: {field} is not a field of the authorized action")

    def test_every_scenario_moves_something(self):
        for name in classes.names():
            entry = classes.get(name)
            for scenario in entry["scenarios"]:
                moved = classes.apply_scenario(scenario, entry["authorized"]["parameters"])
                self.assertNotEqual(moved, entry["authorized"]["parameters"], f"{name}: {scenario}")


if __name__ == "__main__":
    unittest.main()
