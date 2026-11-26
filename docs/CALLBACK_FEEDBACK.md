# Callback feedback and UI-worker isolation — v8.9.2

This follow-up is packaged in **v8.9.2**, not v8.9.1. No database migration is
introduced. Publishing the release makes it available to the existing Telegram
updater; it does not confirm that a hosted bot has installed it.

## Contract

Every callback entering `dispatcher.buttons` (user `u:`, admin `a:`, partner
`p:`, or unknown callback) now shares these mechanisms:

1. Start Telegram callback acknowledgement concurrently with routing. Give the
   task a scheduling opportunity before routing and bound acknowledgement to
   2 seconds. A failed/stalled acknowledgement does not cancel the action.
2. Keep blocking `afn.run` calls made directly by the callback task in an
   eight-worker UI pool, independent of asyncio's default background executor.
3. If no page has been delivered after 750 ms **inside the running bot**, attempt
   one temporary plain-text receipt. It says the request is being processed,
   not that it succeeded or that the user has any permission.
4. Stop the receipt watchdog after `common.render` successfully delivers a page
   (including an existing loading/analysis page), on error response, or on route
   completion. Delete only its own temporary receipt, best-effort.
5. Retain `BUTTON DONE` / `PAGE RENDERED` and add `BUTTON FIRST RESPONSE` for
   first page delivery and `BUTTON PROGRESS` for receipt delivery.

No business-operation timeout, automatic retry, quota bypass, permission cache
or duplicate-action suppression is introduced. Only feedback network requests
have deadlines. Existing authorization and transaction code still runs.

## Worker semantics

- Context variables, kwargs, results and exceptions are preserved by both
  executors. `afn.interactive()` scopes priority to its owning asyncio task.
- Spawned child/background tasks (for example an announcement worker) do not
  inherit UI priority. Async helpers awaited directly in the callback retain it.
- Helpers explicitly scheduled as separate tasks, including wrappers such as
  `asyncio.wait_for(afn.run(...))`, use the default pool unless they establish
  their own interactive scope. Heavy `asyncio.to_thread` work stays in the
  default pool.
- UI workers are created lazily and released on application shutdown. Pending
  writes are not cancelled by executor shutdown.

## Limits — this is not a zero-latency guarantee

The 750 ms threshold is when the program attempts feedback, not when Telegram
will deliver it. A frozen event loop, disconnected host, saturated Telegram
HTTP pool, Telegram rate limits, exhausted UI workers, a database lock, or
external network failure can still delay/fail feedback and final results.

Both pools still share CPU and the database. Eight simultaneous long UI reads
can queue the ninth. This change isolates one scheduling bottleneck; it does
not provide infinite capacity, an external-network SLA, or evidence that every
business action completes instantly.

The watchdog also cannot observe arbitrary direct `bot.send_photo` /
`reply_text` responses as first-page deliveries; common renderer calls and
route completion are observed. Existing result delivery is not intercepted.

A feedback send may race with final-page delivery. The watcher removes the
receipt after the in-flight send completes. Sends have a 2-second deadline and
deletes a 0.5-second deadline. Network timeouts can have uncertain delivery
outcomes, and failed deletion can leave a temporary receipt visible. Cleanup
is best-effort and never a reason to retry the actual operation.

## Verification

```sh
python -m pytest -q tests/test_callback_feedback.py
python -m pytest -q
```

The new suite exercises:

- UI reads and full callback rendering while the default executor is saturated;
- context/kwargs/exception parity, nested scopes, and child-task isolation;
- shared watchdog coverage across user/admin/partner/unknown prefixes;
- fast pages, existing loading pages, error responses and authorization denial;
- stalled acknowledgement, feedback failure/timeout and deletion timeout;
- cancellation cleanup, a send/final-page race, inaccessible old panels, and
  separation of callback feedback scopes;
- a structural scan preventing direct `db.conn()` / `db.tx()` calls inside async
  handler bodies (excluding sync helpers). Indirect service calls still need
  runtime profiling; this scan is not an exhaustive blocking-I/O proof.

These are deterministic offline tests, not a live benchmark of every button.
For production validation, record first-response and completion latency at
p50/p95/p99 while broadcasting/analysing, and verify the installed version.
