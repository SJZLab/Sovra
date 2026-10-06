# Groundwork v0.7.1 — operating amendments (proposed for the skill source)

Status: proven on Sovra between 2026-10-04 and 2026-10-06. Owner action: append this to the Groundwork skill source in your
claude.ai skill library. The synced copy in sessions is a 15-line stub whose v0.7.0 body ("attached file") is missing, so
`/groundwork` currently loads no procedure.

## A1. Pre-submit self-attack (relay)
Before submitting, the relay breaks every protection it added or changed, deletes it, and adds a bypass. It runs the test
file exactly as CI runs it and shows RED. Anything that reads host config is repeated on a neutral host. At least one
negative test must run the real default code against the attack input. The protection under test is never stubbed. The
fault → RED table goes in the submission.
*Evidence:* FTF-H007+04A06 took 5 rounds and FTF-0500C 5. Every return was a protection whose test was positive-only,
stubbed, or host-dependent.

## A2. Same-class sweep
A defect class named on one module is fixed and tested across every sibling module in the same package, with a
per-module result. *Evidence:* the cursor-cycle defect recurred in FTF-0500B after it was fixed in FTF-0500C.

## A3. Returned work first, stated inline
QUEUE.md Active item 0 lists returned items in order, with each one's DoD copied inline. The relay must not rely on
finding a verdict in a long certification log. Rule: "a newer verdict does not outrank list order".
*Evidence:* the relay skipped FTF-H007/0601 for 3 shifts, believing they were awaiting review.

## A4. Lanes and cadence
RELAY_LANES defaults to 3, running hourly at offset minutes. A lane claims immediately via a branch or a draft PR, and
lanes never work a claimed item. *Evidence:* 1 lane every 2 hours capped output at about 12 attempts a day.

## A5. Certifier scope and model
The certifier certifies merged work only, and runs both on PR-opened and on a schedule 30 minutes after each relay shift.
Independent verifier subagents use the mid-tier model, with the top model kept for security-critical items. Fault
matrices run with the CI script runner, never pytest, because check-list runners only fail in `main()`.

## A6. Redundant layers vs gaps
A fault that stays GREEN alone is acceptable only when a direct probe shows the outcome is unchanged (a redundant
layer), AND the pair of redundant faults turns the suite RED. Record both facts in the ledger.

## A7. Usage-limit awareness (AUDIT)
AUDIT reads each routine's `last_run`. Rate-limit or usage-limit failures are reported to the owner once, with the reset
time, and a check-in is scheduled after the reset. A stall caused by the limit is never reported as relay idleness.

## A8. Control-plane merge authority
The control plane resolves its own conflicts and merges its own verdict, queue and certification PRs on green. Owner
asks are limited to genuine owner-only actions: routine creation and edits, secrets, money, and repo settings.
