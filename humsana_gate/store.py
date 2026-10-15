"""The demo's downstream system, and the one door into it.

The point of this file is that the store cannot be written without a clearance, and a clearance
can only be minted from an answer in which the boundary cleared the action. So "every
consequential write goes through the gate" is a property of the code rather than a convention,
and `tests/test_gate.py` proves both halves: a refusal cannot mint a clearance, and a write
without one is refused.

Being honest about the strength of that guard: it makes an accidental bypass impossible, and a
deliberate one a matter of importing a private name. That is the right level for a demo whose
store sits in the same process as its gate. A real deployment puts the downstream system behind
its own process or its own credential, and then the guard is a network boundary as well.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

# The seal that makes a clearance unforgeable by accident. See the module docstring for what
# this is and is not.
_SEAL = object()


class NoClearance(PermissionError):
    """A write that does not carry a clearance the boundary issued."""


class Clearance:
    """Evidence that the boundary cleared one exact action, once."""

    __slots__ = ("receipt_id", "binding_digest", "code", "at")

    def __init__(self, *, seal=None, receipt_id, binding_digest, code, at):
        if seal is not _SEAL:
            raise NoClearance("a clearance is issued by the boundary's answer, not constructed")
        self.receipt_id = receipt_id
        self.binding_digest = binding_digest
        self.code = code
        self.at = at

    @classmethod
    def from_answer(cls, answer: dict) -> "Clearance":
        """The only way to mint one. A refusal, an expiry, a wasted grant: all refused here."""
        code = (answer or {}).get("code")
        if not (code == "MATCHED"
                and answer.get("matched") is True
                and answer.get("consumed") is True):
            raise NoClearance(
                f"the boundary did not clear this action (code {code!r}, "
                f"matched {answer.get('matched')!r}, consumed {answer.get('consumed')!r})")
        return cls(seal=_SEAL, receipt_id=answer.get("receipt_id"),
                   binding_digest=answer.get("binding_digest"),
                   code=code, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def __repr__(self) -> str:
        return f"<Clearance {self.code} {self.receipt_id} at {self.at}>"


class Store:
    """An append-only ledger that stands in for whatever system the action changes.

    JSON lines, so a reviewer can read the file the demo wrote instead of trusting the screen.
    """

    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)

    def write(self, clearance: Clearance, action: dict, note: str = "") -> dict:
        if not isinstance(clearance, Clearance):
            raise NoClearance(
                "this write carries no clearance; consequential writes go through the gate, "
                "which mints one from an answer the boundary cleared")
        row = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cleared_at": clearance.at,
            "receipt_id": clearance.receipt_id,
            "binding_digest": clearance.binding_digest,
            "action": action,
            "note": note,
        }
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    def rows(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
