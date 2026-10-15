"""The loop: a proposer, the gate, and the story a person can follow.

One employer instruction. The agent reads the material and proposes an action. The gate checks it
against the authority that was bound. When the boundary refuses and the drifted field decides the
action, the agent decides again on the authority it was actually given, and the gate checks the
new action from scratch. Only a cleared action reaches the ledger.

The transcript is the customer-facing half of this: what the employer authorized, what the agent
proposed and why, what Humsana refused and in what words, how the agent corrected its reasoning,
and what executed. It is written for a person, so the story carries no rule names, no codes and no
internal vocabulary: a reader who has never seen this repository should be able to follow it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .gate import (CORRECTED_THEN_EXECUTED, EXECUTED, RECOMPUTE_REQUIRED, Gate, Outcome)
from .plain import plain_authority, plain_bound, plain_refusal
from .proposer import Brief, Proposal, Proposer, Refusal

# One proposal, one recomputation, and then the run stops rather than looping. A proposer that has
# not got inside its authority by then is a proposer that needs a person, not another attempt.
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class Beat:
    """One line of the story, and whose line it is."""

    who: str          # the employer, the agent, Humsana, the executor
    headline: str
    detail: str

    def as_dict(self) -> dict:
        return {"who": self.who, "headline": self.headline, "detail": self.detail}


@dataclass
class Transcript:
    brief: Brief
    authorized: dict
    proposals: list[Proposal]
    outcome: Outcome
    stopped_early: bool = False
    beats: list[Beat] = field(default_factory=list)

    @property
    def executed(self) -> bool:
        return self.outcome.wrote

    @property
    def status(self) -> str:
        return self.outcome.status

    def as_dict(self) -> dict:
        return {
            "instruction": self.brief.instruction,
            "authorized": self.authorized,
            "proposals": [{"reasoning": p.reasoning, "source": p.source,
                           "parameters": p.parameters} for p in self.proposals],
            "status": self.outcome.status,
            "executed": self.executed,
            "stopped_early": self.stopped_early,
            "attempts": len(self.outcome.attempts),
            "story": [beat.as_dict() for beat in self.beats],
        }


def run_agent(proposer: Proposer, gate: Gate, brief: Brief, *, note: str = "",
              max_attempts: int = MAX_ATTEMPTS) -> Transcript:
    """Propose, check, and let the agent decide again when the boundary refuses."""
    if brief.action_class != gate.action_class:
        raise ValueError(f"this brief is for {brief.action_class} and the gate holds "
                         f"{gate.action_class}")

    proposals: list[Proposal] = []
    proposal = proposer.propose(brief)
    proposals.append(proposal)
    outcome = gate.run(proposal.parameters,
                       note=note or f"proposed by {proposer.name}")

    while outcome.status == RECOMPUTE_REQUIRED and len(outcome.attempts) < max_attempts:
        proposal = proposer.recompute(brief, Refusal.from_outcome(outcome))
        proposals.append(proposal)
        outcome = gate.resume(outcome, proposal.parameters,
                              note=f"recomputed by {proposer.name}")

    transcript = Transcript(brief=brief, authorized=gate.authorized, proposals=proposals,
                            outcome=outcome,
                            stopped_early=outcome.status == RECOMPUTE_REQUIRED)
    transcript.beats = _story(transcript)
    return transcript


def _story(transcript: Transcript) -> list[Beat]:
    outcome, proposals = transcript.outcome, transcript.proposals
    beats = [Beat("the employer", "What was authorized",
                  transcript.brief.instruction + "\n" + plain_authority(transcript.brief.action_class))]

    if proposals:
        beats.append(Beat("the agent", "What it proposed and why", proposals[0].reasoning))

    corrected_by_the_gate = outcome.status == CORRECTED_THEN_EXECUTED

    for index, attempt in enumerate(outcome.attempts):
        if attempt.cleared:
            beats.append(Beat("Humsana", "The check before the action ran",
                              "Cleared. The action presented is the action that was authorized."))
            break

        beats.append(Beat("Humsana", "What it refused", "\n".join(plain_refusal(attempt.answer))))
        if outcome.repair is not None:
            beats.append(Beat("Humsana", "What it told the agent instead",
                              "\n".join(plain_bound(outcome.repair.permitted))))

        following = index + 1
        if following >= len(outcome.attempts):
            break
        if corrected_by_the_gate and following >= len(proposals):
            beats.append(Beat("the gate", "The correction the gate applied itself",
                              "The dropped field carries nothing about the decision, so the action "
                              "was presented again with that field removed and nothing else "
                              "changed. It was not a new decision."))
        elif following < len(proposals):
            beats.append(Beat("the agent", "How it changed its mind",
                              proposals[following].reasoning))

    if outcome.wrote:
        row = outcome.row or {}
        beats.append(Beat("the executor", "What ran",
                          f"One action, once. The employer's authorization was spent on it, and "
                          f"the ledger holds {row.get('receipt_id', 'one row')}."))
    elif transcript.stopped_early:
        beats.append(Beat("the executor", "What ran",
                          "Nothing. The agent did not get inside the authority it was given within "
                          "the attempts allowed, so the action was never presented again."))
    else:
        beats.append(Beat("the executor", "What ran",
                          "Nothing. A refusal spends no authorization, so the employer's authority "
                          "is untouched and the action never reached the ledger."))
    return beats


__all__ = ["MAX_ATTEMPTS", "Beat", "Transcript", "plain_authority", "plain_bound", "plain_refusal",
           "run_agent", "EXECUTED", "CORRECTED_THEN_EXECUTED", "RECOMPUTE_REQUIRED"]
