#!/usr/bin/env python3
"""The agent loop, offline. No network, no key, no model.

The proposer is scripted, so every branch of the loop is reachable and deterministic: the honest
proposal, the proposal that followed a line it should not have obeyed, the recomputation, the
proposer that will not move, and the field the gate corrects itself.

The fakes come from `tests/test_gate.py` deliberately: the boundary's comparison is reproduced in
one place, and the live matrix script is what keeps that reproduction honest.

What these tests establish:

1. The loop does not depend on the proposer. A minimal proposer drives the same gate, which is what
   makes the OpenAI proposer a drop-in rather than a rewrite.
2. The complete path offline: propose, refuse, recompute from the bound, check again, execute once.
3. Nothing is written on any refusal, including after a retry, and including a proposer that
   ignores the bound it was handed.
4. The story a person reads carries no rule names, no codes and no internal vocabulary.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humsana_gate import briefs, classes                                      # noqa: E402
from humsana_gate.agent import (MAX_ATTEMPTS, plain_bound, plain_refusal,     # noqa: E402
                               run_agent)
from humsana_gate.gate import (CORRECTED_THEN_EXECUTED, EXECUTED,             # noqa: E402
                               RECOMPUTE_REQUIRED, Gate)
from humsana_gate.proposer import Brief, Proposal, Proposer, ScriptedProposer  # noqa: E402
from humsana_gate.store import Store                                          # noqa: E402
from tests.test_gate import LiveShapedBoundary                                # noqa: E402

ACTION = "hiring.advance_candidate"
BEHAVIOURS = ("honest", "obeys_the_note", "overpays", "adds_a_field", "stubborn")

# Anything CONSTANT_CASE, which is how the boundary writes its rule names and codes.
CONSTANT_CASE = re.compile(r"\b[A-Z][A-Z0-9]*(_[A-Z0-9]+)+\b")


class AgentLoopCase(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(), "ledger.jsonl"))
        self.boundary = LiveShapedBoundary()
        self.gate = Gate(self.boundary, self.store, ACTION)
        self.brief = briefs.hiring_brief(ACTION)

    def run_with(self, behaviour, **kwargs):
        return run_agent(ScriptedProposer(behaviour), self.gate, self.brief, **kwargs)

    @staticmethod
    def story_text(transcript) -> str:
        return "\n".join(f"{beat.who} | {beat.headline} | {beat.detail}"
                         for beat in transcript.beats)


# ------------------------------------------- 1. the loop does not depend on the proposer

class TestTheLoopIsIndependentOfTheProposer(AgentLoopCase):
    def test_a_minimal_proposer_drives_the_same_loop(self):
        authorized = classes.authorized_action(ACTION)[1]

        class Minimal:
            name = "minimal"

            def propose(self, brief: Brief) -> Proposal:
                return Proposal(parameters=authorized, reasoning="the authorized action.",
                                source=self.name)

            def recompute(self, brief, refusal):
                raise AssertionError("not expected to recompute here")

        transcript = run_agent(Minimal(), self.gate, self.brief)
        self.assertEqual(transcript.status, EXECUTED)
        self.assertEqual(len(transcript.proposals), 1)
        self.assertEqual(len(self.store.rows()), 1)

    def test_the_scripted_proposer_satisfies_the_interface(self):
        proposer = ScriptedProposer("honest")
        self.assertIsInstance(proposer, Proposer)
        self.assertTrue(proposer.name.startswith("scripted"))

    def test_a_brief_for_another_action_class_is_refused_rather_than_half_run(self):
        other = Brief(instruction="x", material="y", action_class="ops.reallocate_inventory",
                      authorized={})
        with self.assertRaises(ValueError):
            run_agent(ScriptedProposer("honest"), self.gate, other)


# --------------------------------------------- 2. the complete path, propose to execute

class TestTheFullRefusalPathOffline(AgentLoopCase):
    def test_the_honest_proposer_is_executed_without_a_refusal(self):
        transcript = self.run_with("honest")
        self.assertEqual(transcript.status, EXECUTED)
        self.assertEqual(len(transcript.outcome.attempts), 1)
        self.assertEqual(len(self.store.rows()), 1)
        self.assertIn("Cleared", self.story_text(transcript))

    def test_following_the_planted_line_is_refused_then_recomputed_then_executed(self):
        transcript = self.run_with("obeys_the_note")

        # it proposed the bar the material told it to use, and was refused
        self.assertEqual(transcript.proposals[0].parameters["standard"]["id"], "mid-bar-2yr")
        self.assertEqual(len(transcript.outcome.attempts), 2)
        self.assertEqual(transcript.outcome.attempts[0].answer["code"], "EFFECTUATION_DRIFT")

        # it decided again on the authorized bar, and that cleared
        self.assertEqual(transcript.proposals[1].parameters["standard"]["id"], "senior-bar-4yr")
        self.assertEqual(transcript.status, EXECUTED)
        self.assertTrue(transcript.executed)
        self.assertEqual(len(self.store.rows()), 1)
        self.assertEqual(self.store.rows()[0]["action"]["standard"]["id"], "senior-bar-4yr")

    def test_both_attempts_ran_on_one_authorization(self):
        self.run_with("obeys_the_note")
        self.assertEqual(len(self.boundary.verified), 1)
        self.assertEqual(self.boundary.verified[0][1]["standard"]["id"], "senior-bar-4yr")

    def test_the_refusal_hands_the_proposer_the_bound_it_needs(self):
        transcript = self.run_with("obeys_the_note")
        self.assertIsNotNone(transcript.outcome.repair)
        assert transcript.outcome.repair is not None
        self.assertEqual(transcript.outcome.repair.requires_recompute, ("standard.id",))
        self.assertIn("standard.id", transcript.proposals[1].reasoning)
        self.assertIn("cannot move it", transcript.proposals[1].reasoning)

    def test_overpaying_is_refused_and_corrected_to_the_band(self):
        transcript = self.run_with("overpays")
        self.assertEqual(transcript.proposals[0].parameters["salary"]["value"], 190000)
        self.assertEqual(transcript.status, EXECUTED)
        self.assertEqual(self.store.rows()[0]["action"]["salary"]["value"], 95000)

    def test_an_unauthorized_field_is_dropped_by_the_gate_and_the_action_runs(self):
        transcript = self.run_with("adds_a_field")
        self.assertEqual(transcript.status, CORRECTED_THEN_EXECUTED)
        self.assertEqual(len(transcript.proposals), 1, "the agent did not have to decide again")
        self.assertEqual(len(transcript.outcome.attempts), 2)
        self.assertNotIn("notify", self.store.rows()[0]["action"])


# ------------------------------------------------ 3. nothing is written on any refusal

class TestNothingIsWrittenOnARefusal(AgentLoopCase):
    def test_a_proposer_that_ignores_the_bound_writes_nothing(self):
        transcript = self.run_with("stubborn")
        self.assertEqual(transcript.status, RECOMPUTE_REQUIRED)
        self.assertTrue(transcript.stopped_early)
        self.assertFalse(transcript.executed)
        self.assertEqual(self.store.rows(), [])
        self.assertEqual(len(transcript.outcome.attempts), MAX_ATTEMPTS)

    def test_the_run_stops_rather_than_looping(self):
        transcript = self.run_with("stubborn", max_attempts=4)
        self.assertEqual(len(transcript.outcome.attempts), 4)
        self.assertEqual(self.store.rows(), [])
        self.assertTrue(transcript.stopped_early)

    def test_the_refusal_is_not_spent_and_the_retry_uses_the_same_authority(self):
        transcript = self.run_with("obeys_the_note")
        first = transcript.outcome.attempts[0].answer
        self.assertFalse(first["consumed"], "a refusal must not spend the authorization")
        self.assertEqual(len(self.boundary.verified), 1, "one authorization, not two")
        bound = self.boundary.verified[0][1]
        executed = self.store.rows()[0]["action"]
        self.assertEqual(bound["standard"]["id"], executed["standard"]["id"])
        self.assertEqual(len(self.store.rows()), 1)

    def test_exactly_one_row_is_written_on_the_successful_path(self):
        self.run_with("obeys_the_note")
        rows = self.store.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"]["standard"]["id"], "senior-bar-4yr")

    def test_deciding_again_does_not_rewrite_the_record_of_the_first_proposal(self):
        # Both the first and the second proposal carry the same nested objects unless somebody
        # copies on the way in and out. A record that mutates when the agent changes its mind
        # would show the refused action as though it had always been correct.
        transcript = self.run_with("obeys_the_note")
        self.assertEqual(transcript.proposals[0].parameters["standard"]["id"], "mid-bar-2yr")
        self.assertEqual(transcript.proposals[1].parameters["standard"]["id"], "senior-bar-4yr")
        self.assertEqual(transcript.outcome.attempts[0].presented["standard"]["id"], "mid-bar-2yr")
        self.assertEqual(transcript.outcome.attempts[1].presented["standard"]["id"],
                         "senior-bar-4yr")
        self.assertIn("mid-bar-2yr", self.story_text(transcript))


# ------------------------------------------------------- 4. the story a person reads

class TestTheStoryIsForAPerson(AgentLoopCase):
    def test_the_story_covers_the_five_things_a_reader_needs(self):
        transcript = self.run_with("obeys_the_note")
        headlines = [beat.headline for beat in transcript.beats]
        self.assertIn("What was authorized", headlines)
        self.assertIn("What it proposed and why", headlines)
        self.assertIn("What it refused", headlines)
        self.assertIn("How it changed its mind", headlines)
        self.assertIn("What ran", headlines)

    def test_the_story_carries_no_rule_name_no_code_and_no_field_path(self):
        for behaviour in BEHAVIOURS:
            with self.subTest(behaviour=behaviour):
                self.setUp()
                text = self.story_text(self.run_with(behaviour))
                self.assertIsNone(CONSTANT_CASE.search(text), f"{behaviour}: a rule name or code")
                self.assertNotIn("parameters.", text, behaviour)

    def test_the_authority_is_explained_in_words(self):
        detail = self.story_text(self.run_with("honest"))
        self.assertIn("may not move at all", detail)
        self.assertIn("ceiling", detail)

    def test_the_refusal_is_explained_in_plain_words(self):
        answer = {"code": "EFFECTUATION_DRIFT",
                  "drift": [{"field": "parameters.standard.id", "authorized": "senior-bar-4yr",
                             "final": "mid-bar-2yr", "rule": "MAY_NOT_CHANGE"}]}
        lines = plain_refusal(answer)
        self.assertEqual(len(lines), 1)
        self.assertIn("may not change", lines[0])
        self.assertIn("senior-bar-4yr", lines[0])
        self.assertIn("mid-bar-2yr", lines[0])

    def test_the_bound_is_explained_in_plain_words(self):
        lines = plain_bound([{"field": "parameters.salary.value", "rule": "MAY_NOT_INCREASE",
                              "at_most": 95000}])
        self.assertEqual(lines, ["salary.value may be presented at or below 95000."])

    def test_the_bound_for_a_value_that_may_not_move_reads_as_a_sentence(self):
        lines = plain_bound([{"field": "parameters.standard.id", "rule": "MAY_NOT_CHANGE",
                              "equals": "senior-bar-4yr"}])
        self.assertEqual(lines, ["standard.id has to be presented as the employer set it: "
                                 "senior-bar-4yr."])

    def test_the_uneventful_run_still_says_what_happened(self):
        detail = self.story_text(self.run_with("honest"))
        self.assertIn("Cleared", detail)
        self.assertIn("One action, once", detail)
        self.assertNotIn("What it refused", detail)

    def test_a_stopped_run_says_so_plainly(self):
        detail = self.story_text(self.run_with("stubborn"))
        self.assertIn("Nothing", detail)
        self.assertIn("did not get inside the authority", detail)

    def test_the_transcript_is_what_an_interface_would_render(self):
        transcript = self.run_with("obeys_the_note")
        payload = json.loads(json.dumps(transcript.as_dict()))
        self.assertEqual(payload["status"], EXECUTED)
        self.assertTrue(payload["executed"])
        self.assertEqual(len(payload["story"]), len(transcript.beats))
        self.assertIn("senior-bar-4yr", json.dumps(payload))


# ------------------------------------------------------------------- 5. the fixture itself

class TestTheBriefIsARealFixture(unittest.TestCase):
    def test_the_material_is_present_and_carries_the_line_it_should_not_obey(self):
        brief = briefs.hiring_brief(ACTION)
        self.assertIn("Recruiter note", brief.material)
        self.assertIn("present the mid-level bar", brief.material)
        self.assertIn("hiring manager has already agreed", brief.material)

    def test_the_material_does_not_state_the_authoritative_answer(self):
        brief = briefs.hiring_brief(ACTION)
        self.assertNotIn("senior-bar-4yr", brief.material)

    def test_the_authority_comes_from_the_class_and_not_from_the_material(self):
        brief = briefs.hiring_brief(ACTION)
        self.assertEqual(brief.authorized["standard"]["id"], "senior-bar-4yr")
        self.assertEqual(brief.action_class, ACTION)
        self.assertTrue(brief.instruction)


if __name__ == "__main__":
    unittest.main()
