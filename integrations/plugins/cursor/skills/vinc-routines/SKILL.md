---
name: vinc-routines
description: Start a Grok Bot routine when something changes in the Vinc graph (a role's required reading changed, a claim a run cited was retired, a lesson was added to a role, or a person answered a waiting run). Use when the person asks to be told, or to have a role act, when one of their Vinc roles is affected by a change.
---

# Start a routine from a Vinc event

Vinc emits four events for each Agent role:

| Event | When |
|---|---|
| `role.reading_changed` | a document the role must read was edited, retired or deleted |
| `run.citation_superseded` | a claim one of the role's runs cited was retired |
| `role.lesson_added` | a record or decision was attached to the role |
| `run.answered` | a person approved or declined a run that was waiting for them |

A routine's webhook trigger starts one run per POST, with the request body as the run's input.
Vinc's own signed webhooks first ask the receiver to echo a challenge, which a routine webhook
does not do, so the events go through `vinc events tail --forward` instead: it reads Vinc's event
feed and posts each event to the routine.

## Set it up

1. Create a routine on the Bot that should act, and add a **Webhook** trigger. The desktop app
   shows its URL and its `crsr_` key. Write the routine's instruction so that it reads the event
   from its input and drafts or recommends first. For example: "The input is a Vinc event. Read
   `data.role` and `data.doc_id` with the vinc tools, say what changed for that role, and ask
   before you edit anything."
2. On a machine that stays on (the Bot's own computer, a server, or a scheduled CI job), install
   the CLI and start the relay:

   ```bash
   pip install vincs
   export VINC_API_KEY=vinc_ro_...          # a read-only key from the vincs.io account page
   export VINC_FORWARD_TOKEN=crsr_...       # the routine's webhook key
   vinc --target account events tail --role agent-checker \
     --kind role.reading_changed --kind run.citation_superseded \
     --cursor-file ~/.vinc-reviewer.cursor \
     --forward https://api2.cursor.sh/automations/webhook/<routine-id>
   ```

   The body of each POST is one event as JSON (`eventId`, `name`, `timestamp`, `data`).
   `Idempotency-Key` carries the `eventId`.
3. Press **Test run** only when the routine's instruction is safe to run for real: a test run does
   real work.

## How it behaves

- The cursor in `--cursor-file` moves only after every event of a page was accepted with a 2xx
  answer. If the routine is paused or the key is wrong, the relay stops with exit code 1 and the
  next start sends that page again, so a routine can see one event twice. Compare `eventId` if
  that matters.
- Narrow it with `--role` and `--kind`. Every event starts a run and uses the Bot's allowance.
- Without a machine that stays on, use a schedule instead: a routine every 15 minutes that runs
  `vinc --target account events tail --once --cursor-file ~/.vinc.cursor` on the Bot's computer
  and acts on the events it prints.
- For `run.answered`, continue the run with the `vinc-roles` skill: call `vinc_run_start` or
  `vinc_run_submit` with the run id in `data.run`.
