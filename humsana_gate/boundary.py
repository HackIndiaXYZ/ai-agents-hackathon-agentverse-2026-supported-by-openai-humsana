"""The client for Humsana's authorization boundary.

Two calls matter, and they are different in kind.

    verify_action()      the decision. Safe to repeat: nothing is spent and each call
                         mints its own receipt.
    effectuate_action()  the only call that spends the authorization, and it is single
                         use. Call it immediately before the action happens, with the
                         action as it is about to happen.

A refusal comes back as HTTP 200 carrying a `code`, because a refusal is an answer and not
an error. Read `code`, `matched` and `consumed`. An executor that checks only the status
will treat drift as success.

Standard library only, so the gate has no dependency it does not need.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.humsana.com"
CONTRACT = "humsana.authorization/1"

# The ladder, least to most severe. A decision is the most severe floor any rule asked for.
DECISIONS = ("ALLOW", "ALLOW_WITH_LIMITS", "VERIFY", "HUMAN_CONFIRM", "HOLD", "DENY")

# The codes that clear an executor to act. Everything else means do not act.
CLEARED = "MATCHED"


class BoundaryError(RuntimeError):
    """A call that failed, as opposed to an answer that refused."""

    def __init__(self, status: int, payload):
        self.status = status
        self.payload = payload if isinstance(payload, dict) else {"raw": payload}
        code = self.payload.get("code") or self.payload.get("detail") or f"HTTP_{status}"
        super().__init__(f"{code} (HTTP {status})")


class Boundary:
    def __init__(self, base_url: str = DEFAULT_BASE, api_key: str | None = None, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.timeout = timeout

    # ---------------------------------------------------------------- transport

    def _request(self, path: str, body=None, method: str = "POST", authenticated: bool = True):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("user-agent", "humsana-gate/0.1")
        if data is not None:
            request.add_header("content-type", "application/json")
        if authenticated:
            if not self.key:
                raise BoundaryError(0, {"code": "NO_CREDENTIAL_CONFIGURED",
                                        "message": "set HUMSANA_API_KEY, or ask for one with scripts/issue_key.py"})
            request.add_header("authorization", f"Bearer {self.key}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {"raw": raw}
            raise BoundaryError(error.code, payload) from None

    # ------------------------------------------------------------------ reads

    def health(self, authenticated: bool = False) -> dict:
        return self._request("/v1/health", method="GET", authenticated=authenticated)[1]

    # ------------------------------------------------------------- the two calls

    def verify_action(self, *, action_name, parameters, purpose, consequence_class="C1",
                      actor_id="agent-1", actor_type="AUTONOMOUS_AGENT",
                      principal_reference="operator:1", expiry="2027-12-31T00:00:00Z",
                      delegation=None) -> dict:
        """Ask for an authorization. Returns the receipt.

        The receipt's `binding.digest` is a pure function of the authorized tuple, and it is
        what the executor compares against later. `decision` is the authorization answer, not
        the comparison outcome.
        """
        body = {
            "consequence_class_declared": consequence_class,
            "actor": {"id": actor_id, "type": actor_type},
            "principal": {"type": "HUMAN", "reference": principal_reference},
            "purpose": {"text": purpose},
            "requested_action": {"name": action_name, "parameters": parameters},
            "expiry": expiry,
        }
        if delegation is not None:
            body["delegation"] = delegation
        status, payload = self._request("/v1/authorize/verify", body)
        return payload.get("receipt", payload)

    def effectuate_action(self, *, action_name, parameters, receipt_id=None, grant_id=None) -> dict:
        """Check the exact action about to happen, against the action that was authorized.

        Pass the same parameters you sent to verify_action, in the same representation. The
        currency and the numeric value are both part of the tuple and are compared as sent.
        """
        if not (receipt_id or grant_id):
            raise BoundaryError(0, {"code": "NO_AUTHORIZATION_NAMED",
                                    "message": "pass receipt_id or grant_id"})
        body: dict = {"final_action": {"name": action_name, "parameters": parameters}}
        if grant_id:
            body["grant_id"] = grant_id
        else:
            body["receipt_id"] = receipt_id
        return self._request("/v1/authorize/effectuate", body)[1]

    # --------------------------------------------------------------- helpers

    @staticmethod
    def clears(answer: dict) -> bool:
        """True only when the executor may act. All three must hold."""
        return (answer.get("code") == CLEARED
                and answer.get("matched") is True
                and answer.get("consumed") is True)

    @staticmethod
    def drift(answer: dict) -> list:
        """Every field that moved, with its authorized value, its presented value and the rule.

        Read the answer's top level first: in this version the machine readable itemization
        can also arrive inside `effectuation.drift`.
        """
        return answer.get("drift") or (answer.get("effectuation") or {}).get("drift") or []
