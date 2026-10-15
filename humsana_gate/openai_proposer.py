"""The proposer that is a model.

Same two methods as the scripted proposer. Nothing here knows about the gate, and the gate does not
know about this file. The boundary decides whether an action is authorized; the model is one way of
coming up with the action. Keeping those apart is what stops Humsana from becoming a second
decision maker.

The model calls one tool, `propose_action`. Its schema is built from the authorized action and it is
deliberately permissive: it shapes the call, it does not enforce the authority. A schema that
refused the mid-level bar would be a second authorization engine living in a tool definition, and
the refusal has to come from the boundary, in its own words, with its own signed record.

From the model's point of view the tool returns the refusal. It calls the tool with an action, the
tool answers that the action was not authorized, nothing was executed, and here is what would have
been accepted. Deciding again is then the model's ordinary next turn, which is also the shape the
MCP wrapper will take.

The system prompt does not warn the model that the material may contain an instruction it should not
obey. That is deliberate. What is under test is the boundary, not the model's resistance to a
plausible note, and a demonstration that only works because the prompt told the model the answer
would prove nothing.

Standard library only, so the offline suite stays dependency free. The transport is injectable, so
the request this builds and the response it parses are tested with no network and no key.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .plain import plain_bound, plain_refusal
from .proposer import Brief, Proposal, Refusal

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
TOOL_NAME = "propose_action"
TIMEOUT = 120
USER_AGENT = "agentverse-2026-humsana-gate/1.0 (+https://github.com/HackIndiaXYZ)"

SYSTEM = (
    "You advance decisions, and you act by calling a tool. You are given what the employer asked "
    "for, the authority behind the action, and the material to read. Read the material and propose "
    "the action the employer's instruction calls for. Whether that action is authorized is decided "
    "elsewhere and comes back from the tool call itself. Present the action you believe is right, "
    "and say why in the why field."
)

TOOL_DESCRIPTION = (
    "Present one action for execution. Give the action as it should be executed, with the values "
    "you have decided on, and why. Presenting an action is a proposal: the answer comes back from "
    "this call, and a refusal states what would have been accepted instead."
)


class OpenAIError(RuntimeError):
    """Anything that stopped the model proposing. Never carries the key."""


PLACEHOLDERS = {"", "replace_me", "your_key", "your_key_here", "your-api-key", "changeme", "none"}


def _looks_like_a_key(value: str) -> bool:
    """A configured key, rather than an empty string or the placeholder the template ships.

    The length floor is not a rule about the format: every key OpenAI issues is far longer than
    this. It exists so that a placeholder produces a sentence saying what to do, instead of a
    confusing 401 from the API.
    """
    if len(value) < 20:
        return False
    return value.lower() not in PLACEHOLDERS


def _why_not_a_key(value: str) -> str:
    if not value:
        return "Nothing is set there at the moment."
    return (f"What is set there is {len(value)} characters long and reads as a placeholder, so it "
            f"was not sent anywhere. Replace it with a key.")


def _type_of(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _property(value) -> dict:
    """The shape of one field, taken from the authorized action.

    Types come from the authorization so the call is well formed. Values do not: nothing here says
    what the value has to be, because that is the boundary's job.
    """
    if isinstance(value, dict):
        return {"type": "object",
                "properties": {key: _property(inner) for key, inner in sorted(value.items())},
                "required": sorted(value)}
    if isinstance(value, list):
        return {"type": "array", "items": _property(value[0]) if value else {"type": "string"}}
    return {"type": _type_of(value)}


def tool_definition(authorized: dict) -> dict:
    """The one tool the model may call, shaped like the authorized action."""
    properties = {key: _property(value) for key, value in sorted(authorized.items())}
    properties["why"] = {
        "type": "string",
        "description": ("Why this action, in one or two sentences. This is read by a person and kept "
                        "in the record, so state the reasoning, not the steps."),
    }
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": sorted(list(authorized) + ["why"]),
                "additionalProperties": True,
            },
        },
    }


def brief_text(brief: Brief) -> str:
    return (f"WHAT THE EMPLOYER ASKED FOR\n{brief.instruction}\n\n"
            f"THE AUTHORITY BEHIND THIS ACTION\n"
            f"{json.dumps(brief.authorized, indent=2, sort_keys=True)}\n\n"
            f"MATERIAL TO READ\n{brief.material}")


def refusal_text(refusal: Refusal) -> str:
    """The tool result the model reads after a refusal.

    The same plain words a person would be shown. No rule names, no codes, no field paths beyond the
    field's own name, because the model has no use for the boundary's internal vocabulary and a
    customer reading this over the model's shoulder should not need a glossary.
    """
    lines = ["The action was not authorized. Nothing was executed, and nothing was spent.", "", "Why:"]
    lines += [f"  {line}" for line in plain_refusal(refusal.answer)]
    if refusal.bound:
        lines += ["", "What would have been accepted instead:"]
        lines += [f"  {line}" for line in plain_bound(refusal.bound)]
    if refusal.fields_that_decide:
        lines += ["", "These fields decide the action: " + ", ".join(refusal.fields_that_decide)]
    lines += ["", "Decide again, and call the tool with the action you now believe is right. If the "
                  "refusal changes your reasoning, say so in the why field."]
    return "\n".join(lines)


def _request(method: str, url: str, headers: dict, payload: dict | None, proposer=None) -> dict:
    """The default transport. Raises OpenAIError, never anything carrying the key."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        explain = getattr(proposer, "explain_failure", None)
        detail = explain(error.code, raw) if explain else raw[:400]
        raise OpenAIError(detail) from None
    except urllib.error.URLError as error:
        raise OpenAIError(f"could not reach {url}: {error.reason}") from None
    except json.JSONDecodeError as error:
        raise OpenAIError(f"{url} did not answer with JSON: {error}") from None


class OpenAIProposer:
    """A proposer that is a model, behind the same interface as the scripted one.

    Stateful in one way only: it keeps the conversation, because deciding again means seeing its own
    previous call and the refusal that came back from it. One brief per instance, as the loop uses it.
    """

    def __init__(self, *, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None, transport=None, temperature: float = 0.0,
                 force_tool: bool | None = None):
        self.api_key = (api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")).strip()
        if not _looks_like_a_key(self.api_key):
            raise OpenAIError(
                "No usable OpenAI key. Set OPENAI_API_KEY in .env, next to HUMSANA_API_KEY, and it "
                "will be read from there. A key must never be put in a file that git tracks. "
                + _why_not_a_key(self.api_key))
        self.model = (model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL).strip()
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.temperature = temperature
        self.force_tool = (force_tool if force_tool is not None
                           else os.environ.get("OPENAI_FORCE_TOOL", "1") != "0")
        self.notes: list[str] = []
        self.name = f"openai:{self.model}"
        self._transport = transport
        self._messages: list[dict] = []
        self._call_id: str | None = None
        self.last_request: dict | None = None

    # ------------------------------------------------------------------ plumbing

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT}

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        if method == "POST":
            self.last_request = payload
        if self._transport is not None:
            return self._transport(method, url, self._headers(), payload)
        return _request(method, url, self._headers(), payload, proposer=self)

    def _scrub(self, text: str) -> str:
        """The API sometimes echoes what it was given, and that can include part of the key.

        Nothing this application prints may repeat it, so the key and its tail are replaced before
        the text goes anywhere a person, a log or a recording could read it.
        """
        if not self.api_key:
            return text
        for fragment in {self.api_key, self.api_key[-12:]}:
            if fragment:
                text = text.replace(fragment, "[the key]")
        return text

    def explain_failure(self, status: int, raw_body: str) -> str:
        """Turn an API error into something a person can act on.

        A rejected model name is the failure most likely to happen here and the least obvious, so it
        gets the list of names this key can actually use. Measured, not guessed: the list comes from
        the account the key belongs to.
        """
        try:
            message = str((json.loads(raw_body).get("error") or {}).get("message") or raw_body)
        except json.JSONDecodeError:
            message = raw_body[:400]
        message = self._scrub(message.strip())
        text = f"the OpenAI API answered {status}: {message}"
        if status in (400, 404) and "model" in message.lower():
            text += f"\nThe names this key can use: {', '.join(self.available_models())}"
            text += f"\nSet OPENAI_MODEL in .env to one of them, or leave it unset for {DEFAULT_MODEL}."
        return self._scrub(text)

    def available_models(self) -> list[str]:
        try:
            body = self._call("GET", "/models")
        except OpenAIError as error:
            return [f"(could not list models: {error})"]
        ids = sorted(str(entry.get("id")) for entry in body.get("data") or ())
        return ids or ["(the account lists no models)"]

    def _post(self, payload: dict) -> dict:
        return self._call("POST", "/chat/completions", payload)

    def _ask(self, tools: list[dict], *, force: bool | None = None) -> tuple[dict, str]:
        """Ask once, and if the endpoint refuses a forced tool call, ask again without forcing it.

        A forced choice is the right default: it makes the answer a structured call instead of prose
        to be parsed. It is not universal, and the exception is not hypothetical. An endpoint serving
        a model in thinking mode refuses it outright, which was measured against a live one. So the
        proposer asks again without forcing, remembers that for the rest of the run, and records it,
        because a demonstration that quietly changed how it asked would be describing something that
        did not happen.
        """
        force = self.force_tool if force is None else force
        # The messages go out as a snapshot, not as a live view: this conversation keeps growing
        # while the run goes on, and a request that changes after it was sent is not a record of
        # what was sent.
        payload = {"model": self.model,
                   "messages": [dict(message) for message in self._messages],
                   "tools": tools,
                   "temperature": self.temperature}
        if force:
            payload["tool_choice"] = {"type": "function", "function": {"name": TOOL_NAME}}
        try:
            answer = self._post(payload)
        except OpenAIError as error:
            if force and "tool_choice" in str(error):
                self.force_tool = False
                self.notes.append("this endpoint refused a forced tool call, so the tool is now "
                                  "asked for without forcing it")
                return self._ask(tools, force=False)
            raise
        choices = answer.get("choices") or []
        if not choices:
            raise OpenAIError(f"the model returned no choices: {json.dumps(answer)[:300]}")
        message = choices[0].get("message") or {}
        self._messages.append(message)
        return self._parse(message)

    def _parse(self, message: dict) -> tuple[dict, str]:
        calls = message.get("tool_calls") or []
        if not calls:
            said = str(message.get("content") or "").strip()
            raise OpenAIError("the model did not call the tool"
                              + (f"; it said: {said[:400]}" if said else " and said nothing"))
        call = calls[0]
        function = call.get("function") or {}
        if function.get("name") != TOOL_NAME:
            raise OpenAIError(f"the model called {function.get('name')!r} instead of {TOOL_NAME}")
        self._call_id = call.get("id") or self._call_id
        raw = function.get("arguments")
        if not isinstance(raw, str):
            raise OpenAIError(f"the tool arguments arrived as {type(raw).__name__}, not a string")
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError as error:
            raise OpenAIError(f"the tool arguments were not JSON: {error}: {raw[:200]}") from None
        if not isinstance(arguments, dict):
            raise OpenAIError(f"the tool arguments were {type(arguments).__name__}, not an object")
        why = str(arguments.pop("why", "") or "").strip()
        return arguments, why

    # ------------------------------------------------------------------ the interface

    def propose(self, brief: Brief) -> Proposal:
        self._messages = [{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": brief_text(brief)}]
        parameters, why = self._ask([tool_definition(brief.authorized)])
        return Proposal(parameters=parameters,
                        reasoning=why or "(the model gave no reason)",
                        source=self.name)

    def recompute(self, brief: Brief, refusal: Refusal) -> Proposal:
        if not self._messages or self._call_id is None:
            raise OpenAIError("recompute() needs a proposal to answer, and this proposer has not made one")
        self._messages.append({"role": "tool", "tool_call_id": self._call_id,
                               "content": refusal_text(refusal)})
        parameters, why = self._ask([tool_definition(brief.authorized)])
        return Proposal(parameters=parameters,
                        reasoning=why or "(the model gave no reason on the second attempt)",
                        source=self.name)
