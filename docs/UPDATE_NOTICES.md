# Maintenance notices and release archive (v8.10.1)

## Ordering and scope

`updater.self_update(..., before_install=hook)` resolves the release, downloads its
ZIP, verifies the published checksum when available (or requires it when
`UPDATE_REQUIRE_SHA256=1`), and validates package structure. Only then does it
invoke the hook, before the rollback snapshot and any live-file extraction.
No hook runs for `no_update`, failed download, bad checksum or invalid package.
A structurally valid package is not proof that the new process will boot.

Telegram latest/pinned upgrades, downgrades and reinstalls use
`services.update_notices.install`. Its thread lock rejects another Telegram
installation while the first worker runs. The async bridge awaits all notice
attempts before allowing extraction. Cancelling the initiating handler waits
for the worker; it does not restart an installation or release its lock early.
The event loop remains free to process other bot requests during delivery.

The direct entry point also supplies a boot-time hook using a short-lived Bot
client, before it replaces files. This is best-effort with existing configured
credentials/database. The first-ever installation has no registered recipients.
The original no-hook API remains available to offline scripts.

**The running OLD code controls the update INTO v8.9.3.** Installing the package
cannot retroactively send a pre-install notice for that first transition.
Subsequent updates made by v8.9.3 have the new hook. Offline local snapshot
rollback (`/update rollback`) does not use this remote-package hook. Deployments
made outside the bot also do not trigger it. Rolling back to older code removes
these new features; boot auto-update can re-upgrade unless `AUTO_UPDATE=0`.

## Audience, delivery, and branding

- Unblocked registered users other than the configured administrator; all enabled
  `broadcast_channels` and `chart_channels`. The administrator already receives
  the update status and success/failure through the initiating interaction.
- Operational notices are independent of signal/marketing preferences. No
  preference, balance, subscription, permission, quota or schema is modified.
- Numeric ID normalization and case-insensitive username deduplication avoid
  duplicate destinations across channel registries. A username and a numeric ID
  for the same channel cannot be resolved without extra Telegram API calls;
  register consistently if this matters.
- `fysb/ui/assets/update_emblem.png` is the owner's exact original PNG attachment
  (1254 × 1254, SHA-256 `19b837fc40d3f646c8dee1f6ce4ac62f7a2652f39d0c9b8b7e9538d384d656ca`).
  No crop, resize, recoloring, regeneration or re-encoding is applied. The older
  generated emblem is no longer selected by this version.
- If that file is missing, unreadable or empty, resolve the bot's own current
  profile photo once per announcement (`get_me` + `get_user_profile_photos`,
  bounded by request deadlines). Prefer the largest size and reuse its file ID.
  No bot profile photo is changed by this feature. If no avatar is available,
  send HTML text; cancellation notices stay text-only.
- First successful original-photo delivery provides a Telegram file ID reused
  for the remainder of that announcement. Invalid photo requests fall back to
  HTML text; forbidden/ambiguous network outcomes never trigger a duplicate.
  Telegram itself may compress/re-encode `send_photo`; byte identity is
  guaranteed for the packaged source and first-upload bytes, not Telegram's
  rendered/downloaded photo variants. Sending an original document would be a
  different delivery mode and is not silently added here.
- The OLD running version still controls the pre-install notice into v8.10.1.
  That one notice may retain the old image; subsequent notices from the new
  code use the approved image. No package can retroactively alter a message
  already sent by old code.
- Send attempts are sequential, paced at up to 20/second, each with an 8-second
  request deadline. One `RetryAfter` delay up to 30 seconds may be honored. A
  forbidden destination or unknown network delivery outcome is not retried with
  a second message. Counts are best-effort, not delivery/read receipts.
- There is no overall recipient cap. An update waits for delivery attempts;
  large audiences or failing destinations can make this take minutes or longer.
  Simultaneous broadcasts elsewhere may still cause Telegram flood limits.
- Notification errors do not veto a verified installation. Delivery is logged
  as `UPDATE NOTICE`; Telegram admin success status includes sent/failed counts.
- No success broadcast is made: process restart/readiness is not yet proven.
  On an ordinary installer error after notification, the Telegram path attempts
  a second, explicitly non-success notice only to reached destinations. A power
  loss, process kill or boot-time failure cannot guarantee this follow-up.

## Archive

`a:updates:<page>` requests six published GitHub releases per REST page; no fixed
latest-six or latest-eight archive limit. Historical releases without an install
ZIP are included for reading notes, without an install button. GitHub/network
failures show an unavailable/empty state with navigation back. Atom fallback is
limited to page 1 and never silently repeats it on older pages.

`a:updnotes:<tag>:<part>` shows published release notes, including fixes and
improvements, rather than inventing changes. A simple Markdown cleanup removes
headings/bold/code markers. Text is HTML-escaped one character at a time and split
into chunks of at most 2200 escaped UTF-16 units without splitting entities or
surrogate pairs. Empty notes explicitly say no notes were published. Catalog and
last details are cached per administrator session; there is no bulk history
prefetch or permanent database cache. Authorization remains at the dispatcher
and archive/detail functions. `/update list` links to the complete archive.

## Verification

Offline tests cover ordering with verified/broken/equal packages; unchanged
runtime-file preservation in the existing updater suite; recipient filters and
deduplication; successful photo/file-ID reuse; forbidden, network, timeout,
invalid-photo and flood-wait cases; concurrent installations and cancellation;
post-notice failure; release pages without ZIPs; notes escaping, long pagination,
dispatcher routes and authorization. They do not prove live Telegram delivery or
hosted process readiness. Verify the actual installation with `/ping`.
