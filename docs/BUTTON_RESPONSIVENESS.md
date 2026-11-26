# Button responsiveness — first implementation pass

Date: 2026-11-26. Baseline: `ef12a4c40143197036639988a717dd0bdbdb1bbb`.

## Goal and scope

Reduce the time to a useful button response, particularly while background
signal delivery or a broadcast is active. This is not a claim of zero-second
Telegram latency or that every endpoint has been optimized.

This work is packaged as v8.9.1. No schema migration, permission cache,
financial formula change, or Telegram rate increase is part of this change.
Publishing a release makes it available to the existing Telegram updater; it
does not itself confirm that an owner's hosted bot has installed the update.

## Implemented

- **Shared event-loop protection:** offload signal quota writes, personal-card
  database fallback, delivery reference writes, failed-send quota release, and
  opposite-direction advisory database reads to worker threads. The advisory
  still precedes the new signal; delivery filters and quota transactions remain.
- **Broadcasts:** move database access and progress persistence off the event
  loop. Claim `PENDING -> RUNNING` atomically before sending, so concurrent
  confirmation taps cannot start duplicate workers. Register the abort flag
  before awaiting that claim. Preserve the existing sequential send order and
  configured rate; this is deliberately not an unbounded concurrency change.
- **Admin signal list:** compute the global chronological sequence with one
  `ROW_NUMBER() OVER (ORDER BY id)` alongside the existing full-history query.
  Eliminate one connection and count query for every displayed signal. Rank is
  calculated BEFORE filtering and limiting, including gaps/deletions correctly.
- **Admin header:** combine six independent counters into one SQL request;
  keep the optional radar counter separate to preserve the existing fallback.
- **Reports:** combine per-table counters using conditional aggregation. Empty
  tables, admin exclusion, approved revenue, and all status definitions retain
  their existing meaning.
- **User home and dashboard:** batch delivery usage, analysis counts, and radar
  counts while retaining per-user and daily boundaries. Do not cache quotas.
- **Partner statistics:** combine referral counters and commission aggregates;
  gross revenue still includes every status, while pending/paid commission sums
  use their original status predicates.
- **Admin text inputs:** offload the remaining direct setting writes for free
  quota, demo starting balance, and signal expiry.

## Evidence

Local SQLite comparison against the baseline, using 300 synthetic users and
600 synthetic signals, warm settings, and identical returned data:

| Function | SELECTs before | SELECTs after | Connections before → after |
|---|---:|---:|---:|
| `reports.dashboard` | 22 | 8 | 1 → 1 |
| `common._admin_home_snapshot` | 7 | 2 | 1 → 1 |
| `_query_admin_signals` (100 displayed rows, no creator ids) | 103 | 3 | 101 → 1 |

A diagnostic run injected **1 ms per SELECT** through SQLite's trace callback,
then measured the median of three calls:

| Function | Baseline | Updated |
|---|---:|---:|
| Reports | 26.41 ms | 10.70 ms |
| Admin header | 8.90 ms | 3.30 ms |
| Admin signals | 220.02 ms | 16.49 ms |

These times include **simulated query latency**, not a real PostgreSQL server,
Telegram request, production benchmark, or promised speedup. The query counts
and equality of returned data are the useful evidence. Creator metadata adds
its existing two batch queries when signals have positive creator ids.

The new tests also pin warm-cache query budgets for user home (2 SELECTs) and
user dashboard (4 SELECTs), excluding the dispatcher's identity/security lookup.

## Verification

```sh
python -m pytest -q tests/test_button_responsiveness.py
python -m pytest -q
```

The new suite covers query budgets, empty aggregates, status and financial
semantics, global numbering with non-contiguous ids and filters, user isolation,
DB thread identity for successful/failed signal delivery and advisories,
concurrent broadcast confirmation, abort during claim, and callback
acknowledgement while routing is still waiting.

No live Telegram, broker, blockchain, or PostgreSQL integration was exercised.
For hosted verification, compare the existing `BUTTON DONE` / `PAGE RENDERED`
logs before and after under the same traffic, including tail latency while
broadcasting, rather than reporting only the fastest tap.

## Remaining work (not claimed complete)

- Admin signal queries still load history for exact metadata and filtering;
  moving all filtering/aggregation/pagination into SQL needs separate semantic
  parity tests.
- Heavy analysis/chart jobs and UI work still share the default thread pool;
  worker saturation and provider request coalescing need load measurements.
- External market/news requests, rendering/uploading media, and Telegram RTT
  remain real sources of latency.
- Multi-timeframe analysis, digest/quarantine calculations, and full broadcast
  throughput have not been redesigned in this pass.
- A deployment/restart and real-host measurements are needed before declaring
  the user's running bot faster.
