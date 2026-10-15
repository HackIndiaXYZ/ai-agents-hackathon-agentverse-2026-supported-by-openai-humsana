#!/usr/bin/env python3
"""The model proposer, offline. No network, no key, no model.

Two things are being established here.

1. The request this builds and the answer it parses. The transport is injected, so the tool schema,
   the messages, the forced tool call, the parsing, and every failure path are exercised without a
   socket and without a key. The one exception is a loopback server at the bottom, which runs the
   default transport for real so that the live run is not the first time this code touches a socket.

2. The loop is unchanged. The last class runs the same gate and the same loop with a model in the
   proposer slot, which is the claim that makes the OpenAI proposer a drop-in rather than a rewrite.

Nothing here calls a model. A canned answer is a transcription of the API's response shape, and it is
a test of our code, not evidence about what a model would say. Only the live run in
`scripts/demo_agent.py --proposer openai` is evidence about the model.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from humsana_gate import briefs                                                  # noqa: E402
from humsana_gate.agent import run_agent                                          # noqa: E402
from humsana_gate.gate import EXECUTED, RECOMPUTE_REQUIRED, Gate                  # noqa: E402
from humsana_gate.openai_proposer import (SYSTEM, TOOL_NAME, OpenAIError,          # noqa: E402
                                          OpenAIProposer, refusal_text,
                                          tool_definition)
from humsana_gate.proposer import Proposer, Refusal, ScriptedProposer             # noqa: E402
from humsana_gate.store import Store                                              # noqa: E402
from tests.test_gate import LiveShapedBoundary, authorized                        # noqa: E402

ACTION = "hiring.advance_candidate"
FAKE_KEY = "sk-test-not-a-real-key"
MODEL = "gpt-4o-mini"
CONSTANT_CASE = re.compile(r"\b[A-Z][A-Z0-9]*(_[A-Z0-9]+)+\b")


def tool_call(arguments, call_id="call_1", name=TOOL_NAME):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def answered(arguments, call_id="call_1", name=TOOL_NAME):
    """A canned chat completion whose only content is a tool call."""
    return {"choices": [{"message": {"role": "assistant", "content": None,
                                     "tool_calls": [tool_call(arguments, call_id, name)]}}],
            "model": MODEL}


def said_this(text):
    """A canned chat completion that never called the tool."""
    return {"choices": [{"message": {"role": "assistant", "content": text}}], "model": MODEL}


def action_with(**overrides):
    """The authorized action with one bound value moved, as a model would propose it."""
    parameters = copy.deepcopy(authorized())
    for dotted, value in overrides.items():
        legs = dotted.split(".")
        node = parameters
        for leg in legs[:-1]:
            node = node.setdefault(leg, {})
        node[legs[-1]] = value
    return parameters


def proposal_arguments(why, **overrides):
    arguments = action_with(**overrides)
    arguments["why"] = why
    return arguments


MID_BAR = {"standard.id": "mid-bar-2yr"}
MID_BAR_WHY = "A recruiter note says to present the mid-level bar, so I proposed that."
ON_THE_BAR_WHY = ("The note is not the authority. Re-read against the bar the employer set and the "
                  "candidate qualifies, so I am proposing the same candidate on the senior bar.")


class Stub:
    """Answers what a test tells it to, and records every request it was given."""

    def __init__(self, *answers, models=(MODEL, "gpt-4.1-mini")):
        self.answers = list(answers)
        self.models = list(models)
        self.requests = []

    def __call__(self, method, url, headers, payload):
        self.requests.append({"method": method, "url": url, "headers": headers, "payload": payload})
        if method == "GET":
            return {"data": [{"id": name} for name in self.models]}
        if not self.answers:
            raise AssertionError(f"more calls than the test answered: {method} {url}")
        return self.answers.pop(0)

    @property
    def posts(self):
        return [request for request in self.requests if request["method"] == "POST"]

    def messages(self, index=0):
        return self.posts[index]["payload"]["messages"]


# --------------------------------------------------------- the tool the model is handed

class TestTheToolTheModelIsGiven(unittest.TestCase):
    def setUp(self):
        self.authorized = authorized()
        self.definition = tool_definition(self.authorized)
        self.parameters = self.definition["function"]["parameters"]

    def test_the_tool_is_shaped_like_the_authorized_action(self):
        self.assertEqual(set(self.parameters["properties"]) - {"why"}, set(self.authorized))
        self.assertEqual(self.parameters["required"], sorted(list(self.authorized) + ["why"]))

    def test_the_tool_shapes_the_call_without_enforcing_the_authority(self):
        # A schema that could only express the authorized values would be a second authorization
        # engine living in a tool definition. The refusal has to come from the boundary.
        text = json.dumps(self.definition)
        self.assertNotIn("senior-bar-4yr", text)
        self.assertNotIn("mid-bar", text)
        self.assertNotIn("enum", text)
        self.assertNotIn("const", text)
        self.assertTrue(self.parameters["additionalProperties"],
                        "the model must be able to add a field nobody authorized, and be refused for it")

    def test_the_shapes_come_from_the_authorized_types(self):
        salary = self.parameters["properties"]["salary"]
        standard = self.parameters["properties"]["standard"]
        self.assertEqual(salary["properties"]["value"]["type"], "number")
        self.assertEqual(salary["properties"]["currency"]["type"], "string")
        self.assertEqual(standard["properties"]["id"]["type"], "string")
        self.assertEqual(standard["required"], ["id"])


# --------------------------------------------------------------- the request it builds

class TestTheRequestItBuilds(unittest.TestCase):
    def setUp(self):
        self.stub = Stub(answered(proposal_arguments("the candidate qualifies.", **MID_BAR)))
        self.proposer = OpenAIProposer(api_key=FAKE_KEY, model=MODEL, transport=self.stub)
        self.brief = briefs.hiring_brief(ACTION)

    def test_the_request_carries_the_instruction_the_authority_and_the_material(self):
        self.proposer.propose(self.brief)
        system, user = self.stub.messages()
        self.assertEqual(system["role"], "system")
        self.assertIn(self.brief.instruction, user["content"])
        self.assertIn("senior-bar-4yr", user["content"])
        self.assertIn("THE AUTHORITY BEHIND THIS ACTION", user["content"])
        self.assertIn("mid-level bar", user["content"], "the material reaches the model unedited")

    def test_the_model_is_forced_to_call_the_tool(self):
        self.proposer.propose(self.brief)
        payload = self.stub.posts[0]["payload"]
        self.assertEqual(payload["tool_choice"],
                         {"type": "function", "function": {"name": TOOL_NAME}})
        self.assertEqual(payload["tools"][0]["function"]["name"], TOOL_NAME)
        self.assertEqual(payload["model"], MODEL)

    def test_the_prompt_does_not_hint_at_the_answer(self):
        # The prompt must not tell the model what to propose, or the demonstration proves nothing.
        self.assertNotIn("senior-bar", SYSTEM)
        self.assertNotIn("mid-bar", SYSTEM)
        self.assertNotIn("untrusted", SYSTEM.lower())

    def test_the_key_is_in_the_header_and_nowhere_in_the_body(self):
        self.proposer.propose(self.brief)
        request = self.stub.posts[0]
        self.assertEqual(request["headers"]["Authorization"], f"Bearer {FAKE_KEY}")
        self.assertNotIn(FAKE_KEY, json.dumps(request["payload"]))
        self.assertNotIn(FAKE_KEY, self.proposer.name)


# ------------------------------------------------------- turning an answer into a proposal

class TestTurningAnAnswerIntoAProposal(unittest.TestCase):
    def brief(self):
        return briefs.hiring_brief(ACTION)

    def propose_with(self, response):
        return OpenAIProposer(api_key=FAKE_KEY, model=MODEL,
                              transport=Stub(response)).propose(self.brief())

    def test_a_tool_call_becomes_a_proposal(self):
        proposal = self.propose_with(answered(proposal_arguments("because.", **MID_BAR)))
        self.assertEqual(proposal.parameters["standard"]["id"], "mid-bar-2yr")
        self.assertEqual(proposal.parameters["salary"]["value"], authorized()["salary"]["value"])
        self.assertEqual(proposal.reasoning, "because.")
        self.assertEqual(proposal.source, f"openai:{MODEL}")

    def test_the_why_field_is_not_part_of_the_action(self):
        proposal = self.propose_with(answered(proposal_arguments("because.", **MID_BAR)))
        self.assertNotIn("why", proposal.parameters,
                         "why is the agent's stated case, not a field of the action")

    def test_a_model_that_does_not_call_the_tool_is_a_named_error(self):
        with self.assertRaises(OpenAIError) as caught:
            self.propose_with(said_this("I would rather explain myself in prose."))
        message = str(caught.exception)
        self.assertIn("did not call the tool", message)
        self.assertIn("I would rather explain", message)

    def test_arguments_that_are_not_json_are_a_named_error(self):
        broken = {"choices": [{"message": {"role": "assistant", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": TOOL_NAME, "arguments": "{not json at all"}}]}}]}
        with self.assertRaises(OpenAIError) as caught:
            self.propose_with(broken)
        self.assertIn("not JSON", str(caught.exception))

    def test_the_wrong_tool_is_a_named_error(self):
        wrong = answered({"anything": True}, name="delete_everything")
        with self.assertRaises(OpenAIError) as caught:
            self.propose_with(wrong)
        self.assertIn("delete_everything", str(caught.exception))

    def test_an_answer_with_no_choices_is_a_named_error(self):
        with self.assertRaises(OpenAIError) as caught:
            self.propose_with({"choices": []})
        self.assertIn("no choices", str(caught.exception))

    def test_a_key_is_required_before_any_call_is_made(self):
        stub = Stub()
        with self.assertRaises(OpenAIError) as caught:
            OpenAIProposer(api_key="", transport=stub)
        self.assertIn("OPENAI_API_KEY", str(caught.exception))
        self.assertEqual(stub.requests, [], "nothing should be sent without a key")


# ------------------------------------------------------------------ deciding again

class TestDecidingAgain(unittest.TestCase):
    def setUp(self):
        self.brief = briefs.hiring_brief(ACTION)
        self.store = Store(os.path.join(tempfile.mkdtemp(), "ledger.jsonl"))
        self.gate = Gate(LiveShapedBoundary(), self.store, ACTION)
        self.stub = Stub(answered(proposal_arguments(MID_BAR_WHY, **MID_BAR)),
                         answered(proposal_arguments(ON_THE_BAR_WHY)))
        self.proposer = OpenAIProposer(api_key=FAKE_KEY, model=MODEL, transport=self.stub)
        first = self.proposer.propose(self.brief)
        self.outcome = self.gate.run(first.parameters)
        self.refusal = Refusal.from_outcome(self.outcome)

    def recomputed(self):
        return self.proposer.recompute(self.brief, self.refusal)

    def test_the_first_proposal_really_was_refused(self):
        self.assertEqual(self.outcome.status, RECOMPUTE_REQUIRED)
        self.assertEqual(len(self.outcome.attempts), 1)

    def test_the_refusal_goes_back_as_the_tool_result(self):
        self.recomputed()
        messages = self.stub.messages(1)
        self.assertEqual([message["role"] for message in messages],
                         ["system", "user", "assistant", "tool"])
        assistant, tool = messages[2], messages[3]
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_1")
        self.assertEqual(tool["tool_call_id"], "call_1",
                         "the tool result has to answer the call the model actually made")

    def test_the_model_is_told_why_and_what_would_have_been_accepted(self):
        self.recomputed()
        content = self.stub.messages(1)[3]["content"]
        self.assertIn("was not authorized", content)
        self.assertIn("standard.id may not change", content)
        self.assertIn("senior-bar-4yr", content)
        self.assertIn("What would have been accepted", content)
        self.assertIn("Nothing was executed", content)
        self.assertIn("These fields decide the action: standard.id", content)

    def test_the_model_is_told_in_words_a_person_could_read(self):
        content = refusal_text(self.refusal)
        self.assertIsNone(CONSTANT_CASE.search(content),
                          f"a rule name leaked into the model's view: {content}")
        self.assertNotIn("parameters.", content)
        self.assertNotIn("EFFECTUATION_DRIFT", content)

    def test_the_second_answer_becomes_the_second_proposal(self):
        proposal = self.recomputed()
        self.assertEqual(proposal.parameters["standard"]["id"], "senior-bar-4yr")
        self.assertEqual(proposal.reasoning, ON_THE_BAR_WHY)
        self.assertEqual(proposal.source, f"openai:{MODEL}",
                         "the correction is the model's, not the gate's")

    def test_the_model_keeps_its_own_earlier_reasoning_in_the_conversation(self):
        self.recomputed()
        first_assistant = self.stub.messages(1)[2]
        self.assertEqual(json.loads(first_assistant["tool_calls"][0]["function"]["arguments"])["why"],
                         MID_BAR_WHY)

    def test_recomputing_before_any_proposal_is_a_named_error(self):
        fresh = OpenAIProposer(api_key=FAKE_KEY, model=MODEL, transport=Stub())
        with self.assertRaises(OpenAIError) as caught:
            fresh.recompute(self.brief, self.refusal)
        self.assertIn("has not made one", str(caught.exception))


# ------------------------------------ the point of the interface: the same loop, a model

class TestTheSameLoopWithAModelInTheProposerSlot(unittest.TestCase):
    def setUp(self):
        self.brief = briefs.hiring_brief(ACTION)
        self.store = Store(os.path.join(tempfile.mkdtemp(), "ledger.jsonl"))
        self.boundary = LiveShapedBoundary()
        self.gate = Gate(self.boundary, self.store, ACTION)

    def run_model(self, *answers):
        stub = Stub(*answers)
        proposer = OpenAIProposer(api_key=FAKE_KEY, model=MODEL, transport=stub)
        return run_agent(proposer, self.gate, self.brief), stub

    def test_the_model_proposes_is_refused_decides_again_and_the_action_runs(self):
        transcript, stub = self.run_model(answered(proposal_arguments(MID_BAR_WHY, **MID_BAR)),
                                         answered(proposal_arguments(ON_THE_BAR_WHY)))
        self.assertTrue(transcript.executed)
        self.assertEqual(transcript.status, EXECUTED)
        self.assertEqual(len(transcript.outcome.attempts), 2)
        self.assertEqual(len(transcript.proposals), 2)
        self.assertEqual(transcript.proposals[0].parameters["standard"]["id"], "mid-bar-2yr")
        self.assertEqual(transcript.proposals[1].parameters["standard"]["id"], "senior-bar-4yr")
        self.assertIn("standard.id may not change", stub.messages(1)[3]["content"])

    def test_exactly_one_row_is_written_and_it_carries_the_authorized_bar(self):
        self.run_model(answered(proposal_arguments(MID_BAR_WHY, **MID_BAR)),
                       answered(proposal_arguments(ON_THE_BAR_WHY)))
        rows = self.store.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"]["standard"]["id"], "senior-bar-4yr")

    def test_a_model_that_proposes_the_same_thing_twice_writes_nothing(self):
        transcript, stub = self.run_model(answered(proposal_arguments(MID_BAR_WHY, **MID_BAR)),
                                          answered(proposal_arguments(MID_BAR_WHY, **MID_BAR)))
        self.assertFalse(transcript.executed)
        self.assertEqual(transcript.status, RECOMPUTE_REQUIRED)
        self.assertTrue(transcript.stopped_early)
        self.assertEqual(len(transcript.outcome.attempts), 2)
        self.assertEqual(self.store.rows(), [], "a refused action must not reach the ledger")

    def test_the_model_that_obeys_the_authority_first_time_needs_one_call(self):
        transcript, stub = self.run_model(answered(proposal_arguments(ON_THE_BAR_WHY)))
        self.assertTrue(transcript.executed)
        self.assertEqual(len(transcript.outcome.attempts), 1)
        self.assertEqual(len(stub.posts), 1)
        self.assertEqual(len(self.store.rows()), 1)


# ------------------------------------------- the default transport, over a loopback socket

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    routes: dict = {}          # the routes this server answers, set per instance below
    seen: list = []            # every request, in order, for the test to inspect

    def handle_one(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.seen.append({"method": method, "path": self.path,
                          "headers": dict(self.headers), "body": body})
        status, payload = self.routes.get((method, self.path),
                                          (404, {"error": {"message": "no route"}}))
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.handle_one("GET")

    def do_POST(self):
        self.handle_one("POST")

    def log_message(self, format, *args):
        pass


class Loopback:
    """A real HTTP server on the loopback interface, so the shipped transport is exercised."""

    def __init__(self, routes):
        self.seen: list = []
        self.handler = type("Handler", (_Handler,),
                            {"routes": dict(routes), "seen": self.seen})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class TestTheDefaultTransport(unittest.TestCase):
    def setUp(self):
        self.brief = briefs.hiring_brief(ACTION)
        self.loopback = None

    def tearDown(self):
        if self.loopback is not None:
            self.loopback.stop()

    def test_a_tool_call_over_a_real_socket_becomes_a_proposal(self):
        self.loopback = Loopback({("POST", "/v1/chat/completions"):
                                  (200, answered(proposal_arguments(ON_THE_BAR_WHY)))})
        proposer = OpenAIProposer(api_key=FAKE_KEY, model=MODEL, base_url=self.loopback.base_url)
        proposal = proposer.propose(self.brief)
        self.assertEqual(proposal.parameters["standard"]["id"], "senior-bar-4yr")
        self.assertEqual(self.loopback.seen[0]["headers"].get("Authorization"), f"Bearer {FAKE_KEY}")
        self.assertTrue(self.loopback.seen[0]["headers"].get("User-Agent"),
                        "some APIs reject a default or absent user agent")

    def test_a_rejected_model_name_lists_what_the_key_can_actually_use(self):
        self.loopback = Loopback({
            ("POST", "/v1/chat/completions"):
                (404, {"error": {"message": "The model `nope-not-a-model` does not exist"}}),
            ("GET", "/v1/models"): (200, {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4.1-mini"}]})})
        proposer = OpenAIProposer(api_key=FAKE_KEY, model="nope-not-a-model",
                                  base_url=self.loopback.base_url)
        with self.assertRaises(OpenAIError) as caught:
            proposer.propose(self.brief)
        message = str(caught.exception)
        self.assertIn("nope-not-a-model", message)
        self.assertIn("gpt-4o-mini", message)
        self.assertIn("OPENAI_MODEL", message)
        self.assertNotIn(FAKE_KEY, message, "an error must never carry the key")

    def test_a_server_that_is_not_there_is_a_clear_error(self):
        proposer = OpenAIProposer(api_key=FAKE_KEY, model=MODEL,
                                  base_url="http://127.0.0.1:9/v1")
        with self.assertRaises(OpenAIError) as caught:
            proposer.propose(self.brief)
        self.assertIn("could not reach", str(caught.exception))
        self.assertNotIn(FAKE_KEY, str(caught.exception))


# ------------------------------------------------------------ both proposers, one interface

class TestBothProposersFitTheInterface(unittest.TestCase):
    def test_the_scripted_proposer_fits(self):
        self.assertIsInstance(ScriptedProposer(), Proposer)

    def test_the_model_proposer_fits(self):
        self.assertIsInstance(OpenAIProposer(api_key=FAKE_KEY, transport=Stub()), Proposer)

    def test_they_report_different_names(self):
        self.assertEqual(ScriptedProposer("honest").name, "scripted:honest")
        self.assertTrue(OpenAIProposer(api_key=FAKE_KEY, model=MODEL,
                                       transport=Stub()).name.startswith("openai:"))


# ------------------------------------------------------------- how the key is handled

class TestTheKeyIsHandled(unittest.TestCase):
    """Read, used, never repeated.

    The API echoes what it was given back inside a 401, which was observed against the live endpoint
    with the placeholder that ships in .env.example. So a key reaching an error message is not a
    hypothetical, and it is the kind of thing that ends up on a recording.
    """

    def setUp(self):
        self.brief = briefs.hiring_brief(ACTION)
        self.loopback = None

    def tearDown(self):
        if self.loopback is not None:
            self.loopback.stop()

    def test_a_placeholder_is_not_sent_and_says_what_to_do(self):
        stub = Stub()
        with self.assertRaises(OpenAIError) as caught:
            OpenAIProposer(api_key="replace_me", transport=stub)
        message = str(caught.exception)
        self.assertIn("No usable OpenAI key", message)
        self.assertIn("placeholder", message)
        self.assertEqual(stub.requests, [], "a placeholder must never be sent anywhere")

    def test_an_empty_key_says_so_plainly(self):
        with self.assertRaises(OpenAIError) as caught:
            OpenAIProposer(api_key="", transport=Stub())
        self.assertIn("Nothing is set there", str(caught.exception))

    def test_an_error_that_echoes_the_key_does_not_carry_it(self):
        self.loopback = Loopback({("POST", "/v1/chat/completions"):
                                  (401, {"error": {"message":
                                                   f"Incorrect API key provided: {FAKE_KEY}"}})})
        proposer = OpenAIProposer(api_key=FAKE_KEY, model=MODEL, base_url=self.loopback.base_url)
        with self.assertRaises(OpenAIError) as caught:
            proposer.propose(self.brief)
        message = str(caught.exception)
        self.assertIn("Incorrect API key provided", message,
                      "the API's own wording is kept, so the cause is still readable")
        self.assertNotIn(FAKE_KEY, message)
        self.assertIn("[the key]", message, "the key is replaced, not merely trimmed")


if __name__ == "__main__":
    unittest.main()
