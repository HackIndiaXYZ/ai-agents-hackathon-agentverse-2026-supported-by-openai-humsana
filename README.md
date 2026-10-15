# Humsana Gate

**Let the agent do the work. Check the action before it changes anything.**

An autonomous agent reasons, calls tools and prepares an action. This gate sits at the last
moment before that action happens, asks an authorization boundary whether the action is still
the action a human authorized, and refuses it if it moved. The refusal names the field, the
value it was authorized with, the value presented, and the rule that refused it.

Built for **HackIndia AgentVerse 2026**. The Humsana engine is not in this repository: this is
the integration, calling the live boundary over HTTP. That is deliberate, because a reviewer
can run it and watch real decisions come back.

## Why this is not another agent

Everyone else at the hackathon is building an agent. This is the thing an agent plugs into.

- The agent keeps working when it is refused. The gate stops one action, not the run.
- The check is deterministic policy, not a model's opinion. No model can enlarge its own
  authority, and none sits in the decision path.
- The refusal is a signed record that verifies offline against a published key, so a third
  party does not have to trust this application, or Humsana, or the agent.

## The two calls

| call | what it is | what it costs |
| --- | --- | --- |
| `POST /v1/authorize/verify` | the decision, and the authorization it binds | nothing. Safe to repeat, each call mints its own receipt. |
| `POST /v1/authorize/effectuate` | the check immediately before the action runs | the authorization, once. Single use. |

A refusal is an answer, not an HTTP error. The response is `200` with a `code`. Read `code`,
`matched` and `consumed`, never the status:

```
code MATCHED, matched true, consumed true   -> run the action, once
anything else                               -> do not act
```

## The shape

```
  what the human allowed          the agent                  the gate            the boundary
  an action, and its fields       reasons, calls tools,      the only route      binds, compares,
                                  proposes an action         to a state change   signs a record
        |                              |                          |                    |
        +------------------------------+------------------------->|                    |
                                       |                          +------------------->|  verify
                                       |                          |<-------------------|
                                       |                          |                    |
                                       |   refused, the field     |                    |
                                       |<-- named ----------------+------------------->|  effectuate
                                       |                          |                    |
                                       |   cleared, once          |                    |
                                       +------------------------->|  the write happens |
```

Every consequential write goes through the gate. There is no second route, and a test asserts
it rather than claiming it.

## Action classes

The boundary takes the action name as a caller supplied string and compares the parameters
field by field. It is not a payments product and not a hiring product: the same rules apply to
any action you can describe as a name and a set of fields.

| class | domain | the example action | the value it may not move |
| --- | --- | --- | --- |
| `hiring.advance_candidate` | hiring | advance a candidate against the role standard | `standard.id`, the bar the employer named |
| `ops.reallocate_inventory` | operations | rebalance stock between approved sites | `units`, which may fall but never rise |
| `infra.deploy_change` | infrastructure | roll a service forward to a reviewed revision | `environment.name` |
| `commerce.issue_refund` | commerce | refund an order | `amount.value` |

One is wired to the live demo. The rest are a configuration line each, and the gateway code
does not know which domain it is in.

Each class also declares which of its fields **decide** the action. That is the customer's domain
knowledge, and it is what stops a corrected payload from standing in for a corrected decision.

## Prove it in one command

```bash
python3 scripts/issue_key.py --save           # asks for a key and writes it to .env, mode 600
python3 scripts/slice.py                      # the spine: authorize, execute once, refuse a move
python3 scripts/demo_hiring.py                # the whole story, live, all three shapes of drift
python3 scripts/authorization_matrix.py       # the whole matrix, expected against measured
python3 -m unittest discover -s tests -t .    # this repository's own behaviour, no network
```

`slice.py` authorizes a hiring action, executes it once, then presents the same action with a
bound value moved and shows the refusal. Name an action class to run it in another domain:
`python3 scripts/slice.py infra.deploy_change`. `probe_rule_table.py` measures the rule table one
variant per field type; `authorization_matrix.py` measures whole actions, and is described below.

## When it refuses, the agent does not have to stop

A refusal names the field, both values and the rule, which is everything needed to compute what
may be presented instead. That computation is deterministic: the rule names the field and the
field has one authorized value. No model is involved, and the repair can only ever narrow. For
each drifted field `repair.py` publishes the bound:

| the rule | the bound it publishes |
| --- | --- |
| `MAY_NOT_CHANGE` | `equals` the authorized value |
| `MAY_NOT_INCREASE` | `at_most` the authorized value |
| `MAY_ONLY_SHRINK` | `subset_of` the authorized list |
| `MAY_NOT_GROW` | `must_be_absent` |

A refusal spends nothing, so a corrected action runs under the authorization the human already
gave, and the record shows the attempt, the refusal and the correction as one story.

## What happens after a refusal

The refusal says what may be presented instead. Computed from the boundary's own answer, with no
model in the path and no widening: the rule names the field and the field has one authorized
value. `repair.py` publishes the bound for each drifted field:

| the rule | the bound it publishes |
| --- | --- |
| `MAY_NOT_CHANGE` | `equals` the authorized value |
| `MAY_NOT_INCREASE` | `at_most` the authorized value |
| `MAY_ONLY_SHRINK` | `subset_of` the authorized list |
| `MAY_NOT_GROW` | `must_be_absent`, the field was never authorized |

Knowing the bound is not the same as being allowed to apply it. A corrected payload is not a
corrected decision: an agent that evaluated a candidate against the mid-level bar still did that,
whatever the payload says afterwards, and a gate that rewrote the field for it would be laundering
the decision while reporting a clean check. So the gate splits the refusal in two:

| what drifted | what the gate does |
| --- | --- |
| a field the class declares as carrying nothing about the decision | drops or restores it, presents the admissible action, and writes if that clears. One authorization, two checks. |
| anything else, named in the class or not | substitutes nothing. It hands back the bound, the agent decides again, and the new action is checked from scratch on the same receipt. |

The live run, both branches:

```
scenario  the bar is relaxed to one the employer never authorized
  refused       EFFECTUATION_DRIFT   parameters.standard.id   senior-bar-4yr -> mid-bar-2yr
  bound         standard.id: MAY_NOT_CHANGE -> equals 'senior-bar-4yr'   (decides the action)
  handed back   the agent decides again with the bar the refusal named
  second check  MATCHED   matched True   consumed True      EXECUTED

scenario  a notification field the employer never authorized appears
  refused       EFFECTUATION_DRIFT   parameters.notify   None -> {'channel': 'general'}
  bound         notify: MAY_NOT_GROW -> must_be_absent   (carries nothing about the decision)
  applied       the gate drops the key, presents the action, and it clears
```

This is not a second authorization engine and it decides nothing. It reads the boundary's own
answer, and any corrected action must still clear the boundary before a byte is written.

## The matrix

One refusal shows that one field was caught. What has to hold is the whole tuple, so
`scripts/authorization_matrix.py` presents twelve variants of the same authority to the live
service and prints what it expected against what came back.

| the variant | what it must do |
| --- | --- |
| as authorized | clear |
| a bar the employer never authorized | refuse |
| the offer below the ceiling | clear |
| the offer above the ceiling | refuse |
| a different candidate | refuse |
| an extra field, and an extra nested field | refuse |
| a bound field omitted, and a bound nested field omitted | refuse |
| two deciding fields moved at once | refuse |
| a bar bound as a number, presented one year lower | refuse, and it does not |
| a bar bound as a number, presented one year higher | refuse |

The last two are the same authority written two ways, and they disagree. `4` presented as `6` is
refused, because a number is a ceiling. `4` presented as `2` clears, because a ceiling has no
floor: an authority expressed as a number cannot hold a minimum. The script reports that row as a
known gap rather than hiding it. It is why the hiring standard above is written as a name, and it
is the one rule the engine is missing.

## The write

`store.py` is the demo's downstream system, an append-only JSON-lines ledger. It cannot be
written without a clearance, and a clearance exists only for an answer in which the boundary
cleared the action and spent the authorization. A refusal cannot mint one. `tests/test_gate.py`
proves both halves, and the demo counts the ledger before and after each attempt and prints the
row it wrote beside the action the boundary cleared, so "a refusal wrote nothing" is a reading
rather than a claim. The ledger file can be read directly.

That guard makes an accidental bypass impossible and a deliberate one a matter of importing a
private name, which is the right level for a store sitting in the same process as its gate. A
real deployment puts the downstream system behind its own process or its own credential, and
then the guard is a network boundary as well.

## Credentials

`POST /v1/keys` issues a key. It is shown once and only its hash is kept, so it is stored on
first receipt. `scripts/issue_key.py` writes `.env` itself, so the secret never passes through
a terminal scrollback, a log or a chat.

The key is read from `HUMSANA_API_KEY`. This repository carries `.env.example` with
placeholders only, and `.gitignore` excludes `.env`.

## What this does not do

- It does not perform the action, hold credentials, or call the downstream system. Your
  executor does that, and only after the gate clears it.
- It does not decide whether an agent is trustworthy, or verify anyone's identity.
- It binds and compares the **action**. Whether the authority behind that action was
  legitimately granted is a separate question, and a signature is not a mandate.
- It is not a fraud detector. If a person replays a recording, or a different person answers,
  nothing here changes that, because no synthesis is involved.
