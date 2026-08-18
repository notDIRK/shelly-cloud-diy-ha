# Device-Swap feature — planning snapshot (2026-06-23)

Kept as written. These are the documents the feature was designed from, not a
description of what ships today — read them together with the status below.

## Where this stands (2026-08-18)

- **Stage 1 (Fleet-Map)** shipped: the `shelly_cloud_diy.fleet_map` service and
  the cloud↔local matching carried in the integration's diagnostics.
- **Stage 2, cloud side** shipped: the `shelly_cloud_diy.replace_device`
  service.
- **Stage 2, native side** is upstream and stalled:
  [home-assistant/core#174581](https://github.com/home-assistant/core/pull/174581)
  is open with changes requested and has not moved since 2026-08-03.
- **Stage 3** is untouched.

Anything below that reads as "not yet" or "deferred" is a statement from June,
not from today.

- **[MOCKUP-STAGE1.md](MOCKUP-STAGE1.md)** — Stage-1 UX mockup (HA + Shelly Cloud)
- [PLAN-3-STAGES.md](PLAN-3-STAGES.md) — hardened 3-stage plan (multi-agent + critics)
- [PROPOSAL.md](PROPOSAL.md) — original feature proposal
- [UPSTREAM.md](UPSTREAM.md) — HA-Core upstream PR plan
- Feature highlights / USPs: [../../FEATURE-HIGHLIGHTS.md](../../FEATURE-HIGHLIGHTS.md) ([DE](../../FEATURE-HIGHLIGHTS.de.md))
