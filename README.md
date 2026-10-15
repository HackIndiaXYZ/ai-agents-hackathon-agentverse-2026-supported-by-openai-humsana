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
| `hiring.advance_candidate` | hiring | advance a candidate against the role standard | `standard.years_minimum` |
| `ops.reallocate_inventory` | operations | rebalance stock between approved sites | `units`, which may fall but never rise |
| `infra.deploy_change` | infrastructure | roll a service forward to a reviewed revision | `environment.name` |
| `commerce.issue_refund` | commerce | refund an order | `amount.value` |

One is wired to the live demo. The rest are a configuration line each, and the gateway code
does not know which domain it is in.

## Prove it in one command

```bash
python3 scripts/issue_key.py --save   # asks for a key and writes it to .env, mode 600
python3 scripts/slice.py              # the whole spine, live, against api.humsana.com
```

The slice authorizes a hiring action, executes it once, then presents the same action with the
standard lowered from 4 years to 3 and shows the refusal. Pass an action class name to run it
against another domain: `python3 scripts/slice.py infra.deploy_change`.

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
