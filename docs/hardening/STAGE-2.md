# Stage 2: delete the unused kiwi-sin1 and kiwi-hil1 (prepared, NOT executed)

Script: `ops/stage2/delete-idle-nodes.ps1`. Preview by default (Hetzner GETs + public Kiwi list + DNS only).

| Server | Hetzner id | Type / location | Firewall | Cost |
|---|---|---|---|---|
| kiwi-sin1 | 175465282 | cpx02, Singapore | 12016756 `kiwi-sin1` | $9.59/mo |
| kiwi-hil1 | 175465419 | cpx11, Hillsboro | 12016766 `kiwi-hil1` | $21.09/mo |

Preview run 2026-11-14: all checks passed. Names and IPv4 unchanged; out this period 11.6 MB / 7.7 MB; each firewall is applied
only to its own server; no volumes, floating IPs, snapshots or DNS names; the public Kiwi list is Germany, Helsinki 1 and
United States (Ashburn) only. Evidence that both are unused: `STAGE-0.md` section D.

The check stops on anything unexpected. Its first run stopped on a false match ("sin" inside "Helsinki"); the pattern is now exact.

## What -Execute does (owner at a terminal, after typing `delete kiwi-sin1 175465282 and kiwi-hil1 175465419`)
1. Re-runs every check; stops if any fails.
2. Per server: delete+rebuild protection off → DELETE server (primary IPs have auto_delete) → wait for success → DELETE its firewall.
3. Prints what to check afterwards: `/servers` lists hel1, fsn1, ash1 only; Helsinki/Germany/Ashburn still online in `/stats`.

Risk: not reversible (the IPs are released). Nothing is stored on either server. Rollback: none needed; `kiwi-infra.mjs create`
could rebuild a node later (new IPs). Billing stops at deletion (Hetzner bills hourly, capped monthly).

Approval: "Approve Stage 2: delete kiwi-sin1 (175465282) and kiwi-hil1 (175465419) and their firewalls." Then run
`powershell -File ops\stage2\delete-idle-nodes.ps1 -Execute` yourself (or with `!` in this session).
