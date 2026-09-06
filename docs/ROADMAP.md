# Roadmap — Shelly Cloud DIY for Home Assistant

> 🇩🇪 **Deutsch:** Eine deutschsprachige Fassung dieses Dokuments findest du in [`ROADMAP.de.md`](ROADMAP.de.md).

## Project intent

`shelly-cloud-diy-ha` is a Home Assistant custom integration that connects
Home Assistant to the Shelly Cloud using the **Cloud Control API**, the
self-service API path that Shelly explicitly documents as being available
to DIY / private users. The project exists because the only pre-existing
community integration in the same space ([engesin/shelly-integrator-ha](https://github.com/engesin/shelly-integrator-ha))
uses the **Integrator API**, which Shelly documents as *"Licenses for
personal use are not provided."* — it requires a commercial-integrator
approval flow that most private users never get through.

This project is a hard fork of that upstream, retained for git-history
traceability only. No upstream merges are expected.

## Scope target

- **Achieved:** installable via **HACS** — the integration is in the HACS
  default store, so no custom-repository URL is needed.
- **Not a goal for now:** submission to **Home Assistant Core**. Code is
  kept Core-compatible in style (no personal references in source, English
  log messages, proper exception types, translations), but the full Core
  quality-scale requirements are not being built out on a schedule. Some of
  them have since landed anyway because they were independently useful — a
  sanitized diagnostics export in particular, which is what makes it
  possible to debug a user's device remotely from an issue report.

## Milestones

Status key: ✅ done · 🔄 in progress · ⏳ planned · 💡 aspirational

> **Where the project stands (2026-09-05, `v0.11.0`):** Milestones 0, 1 and 3
> are done — the integration is released, in the HACS default store, and has
> grown well past the original M1 scope (Gen1/Gen2/Gen3 devices, BLU family,
> energy monitoring, virtual components, an offline detector, a relay-fault
> detector, a repairs platform and device health checks). Milestone 2 has
> split: its **control** half — switching virtual components on owned devices
> over OAuth — is built and waiting for the next release, while its original
> **push** half is measured, narrower than assumed, and not built. See its
> section.

### Milestone 0 — Foundation  ✅

- Forked `engesin/shelly-integrator-ha` as `notDIRK/shelly-integrator-ha`.
- Security hardening: randomised per-install webhook id, local-gateway-URL
  SSRF guard, webhook-handler logging uses `logger.exception`.
- Correctness: deep-merge partial StatusOnChange updates, disabled dead 30 s
  polling timer, jittered WebSocket reconnect backoff.
- Consolidated codebase map at `docs/CODEBASE_MAP.md` (pre-pivot snapshot).
- Bilingual "Getting an API Token" section in the old README — now largely
  obsolete post-pivot.
- Pivot research: verified the Shelly Cloud Control API sees shared
  devices (tested against a real ECOWITT WS90 shared from another
  account); verified the Cloud Control API WebSocket rejects `auth_key`
  (`Token-Broken` close 4401) and requires OAuth; confirmed HTTP polling
  via `auth_key` returns full device status for all account-visible
  devices.
- Repo rename to `shelly-cloud-diy-ha`, Python domain to `shelly_cloud_diy`,
  CLOUD DIY branding applied to `images/icon.png`.
- Three historical release tags (`v0.1.0-notDIRK` … `v0.2.2-notDIRK`) kept
  on their Integrator-API commits for audit trail.

### Milestone 1 — Cloud Control API with `auth_key` + HTTP polling  ✅

**Goal:** First usable HACS release for private users. No Integrator-API
token, no Shelly support email, no consent webhook. User pastes their
`auth_key` + server URI from the Shelly App and everything works.

Changes:
- Replace auth layer: delete `api/auth.py` (JWT / integrator-token
  exchange), add `api/cloud_control.py` (HTTP client wrapping
  `POST /device/all_status`, `POST /device/status`, `POST /device/relay/control`,
  `POST /device/light/control`, `POST /device/relay/roller/control`, all
  authenticated via the `auth_key` form parameter).
- Rewrite `config_flow.py` — user step asks for `auth_key` + `server URI`;
  no consent step; options flow simplified accordingly.
- Rewrite `coordinator.py` to poll `/device/all_status` at a configurable
  interval (3–60 s, default 5 s), respecting the documented 1 req/s rate
  limit (a single consolidated poll beats per-device fan-out).
- Remove: consent webhook flow (`services/webhook.py`, `core/consent.py`,
  webhook-id migration logic in `__init__.py`), `api/websocket.py` (moved
  to M2 scope).
- Keep reusable: device-state merge logic, per-platform entity classes
  (sensor, switch, light, cover, button, binary_sensor), entity
  descriptions, historical CSV service (local-gateway path is unchanged).
- Add: entity mapping for BLE / gateway-bridged sensors seen in
  `/device/all_status` with `gen: "GBLE"` (Shelly BLU family, Shelly BLU
  H&T, SBWS-90CM weather station, etc. — one mapping table keyed by
  `_dev_info.code`).
- Update: translations and `strings.json` for the new config fields
  (`auth_key`, `server_uri` replacing `integrator_token`); German
  translation added (`translations/de.json`).
- Manifest: bump to `0.3.0`, update `iot_class` to `cloud_polling`
  (because push is no longer the mechanism), drop unused `dependencies: ["webhook"]`.
- Release: `v0.3.0` tagged without the `-notDIRK` suffix going forward —
  targeting HACS-default-store submission eventually.

Non-goals in M1:
- Real-time / sub-5-second update latency (that is M2).
- OAuth authentication (that is M2).
- Cloud-sourced historical energy data (the existing local-gateway path is
  preserved; cloud historical is a separate later scope if feasible).

M1 point-release follow-ups (all within the `auth_key` HTTP-polling model,
no OAuth needed):
- **v0.3.2** ✅ — Gen2/Gen3 device model detection (read `code` + `cloud.connected`
  from the top level of `/device/all_status`, not just from `_dev_info`).
- **v0.3.3** ✅ — User-set device name backfill via the Cloud Control API v2
  endpoint `POST /v2/devices/api/get` with
  `{auth_key, ids, select:["settings"], pick:{settings:["sys"]}}` — returns
  `settings.sys.device.name` (Gen2) / `settings.name` (Gen1). Lazy, batched,
  online-only, shares the 1 req/s budget with the main poll. This is the
  *device-local* name (set via the Shelly app / LAN RPC); in practice
  identical to the user-visible cloud label but not guaranteed.
- **v0.4.0** ✅ — Opt-in per-device entity creation (see below).

Everything after v0.4.0 is device- and platform-coverage work that was not
foreseen in the original M1 plan, driven mostly by user issue reports:
Gen2 dual-cover, H&T Gen3 battery sensors, RGBW2 colour control, Plus Uni
pulse counter, Pro 3EM energy monitoring, BLU Door/Window, and read-only
virtual components. See the [releases](https://github.com/notDIRK/shelly-cloud-diy-ha/releases)
for the per-version detail.

Opt-in entity creation (v0.4.0):
- Default auto-creation of every discovered device's entities is unfriendly
  for users who already run the HA Core Shelly integration over LAN — they
  get duplicate entities. v0.4.0 adds a device-picker step to the config
  flow (with a "create everything now" shortcut for greenfield users) and
  an options-flow toggle for enabling/disabling devices later. The
  coordinator still polls the whole fleet (single request), but entities
  are only materialised for enabled devices.

Explicitly documented limitations users must know:
- **1 request per second** rate limit per Shelly account (Shelly official
  doc).
- **Polling latency** at default 5 s means sensor values lag reality by up
  to ~5 seconds; switch actions fire immediately, latency only applies to
  state *observation*.
- **HTTP endpoints are documented by Shelly as intentionally
  underdocumented** (they reserve the right to change parameter formats)
  — this integration pins to the v1 endpoint shape and tracks changes
  reactively.

### Milestone 2 — OAuth: cloud control for owned devices, then push  🔄 (control built, push not)

**Goal, restated.** This milestone started out as "realtime push". Measuring it
changed the headline. Push turned out to be narrow, while the same OAuth
session turned out to unlock something the documented API cannot do at all:
**writing** to a device. So the milestone has two halves now, and the valuable
one is no longer the one it was named after.

#### 2.1 Cloud control for owned devices — shipped in v0.12.0  ✅

Off by default. What it does, and what it costs:

- Some things a Shelly can do have **no route** in the documented Cloud
  Control API. An irrigation controller's zones, or the boolean a script
  exposes, are *virtual components*: readable, not writable. Every documented
  write route answers "no such route" — measured, with a known-good
  `set/switch` call as the negative control on the same device.
- They *can* be written over the same cloud WebSocket relay the Shelly app
  uses, which is a generic JRPC relay. `Boolean.Set` on a virtual component
  succeeded over it on real hardware.
- The relay routes **only to devices the account owns**. A shared device is
  refused with `WRONG_ID`, and a deliberately malformed id gets the identical
  refusal — so this is a routing limit, not a formatting mistake. Ownership is
  not visible anywhere in the poll payload, so each device carrying such a
  component is probed once per session (never per poll) with
  `Shelly.GetDeviceInfo`, and the verdict goes into diagnostics — "why does my
  device have no switch" should be answerable from a bug report.
- The channel is **undocumented**, and Shelly's support stated on 2026-07-27
  that undocumented endpoints are not part of the supported API. It is
  therefore **opt-in and off by default**. Switching it on asks for the account
  sign-in the relay needs; the password is used once and never stored, only the
  resulting token is, and turning the option off again deletes it.
- The new switch is created **beside** the existing read-only sensor of the
  same component, never instead of it — replacing it would silently break
  automations already pointing at it.
- Failures are loud. A rejected command raises instead of reporting success,
  the state afterwards comes from the next poll rather than an optimistic
  guess, and if the control channel itself is down the switch goes unavailable
  rather than looking operable.
- **The poll is untouched.** With the option off nothing about the integration
  changes: no sign-in, no second connection, no probe, no new entity.

Confirmed on hardware before release, on a live Home Assistant against a real
account: the switch appears beside the read-only sensor, the command reaches
the device in about two seconds — verified by a second, independent
integration watching the same device over the local network — and the entity's
own state follows from the next poll rather than from an assumption.
Diagnostics reported the device as owned, none unclassified. One caveat stated
plainly: that run signed in from a token minted beforehand, so the sign-in form
itself is covered by tests rather than by that run.

[Issue #20](https://github.com/notDIRK/shelly-cloud-diy-ha/issues/20) stays
open regardless, until its reporter confirms it on the irrigation controller
that prompted the work — a Gen3 relay is not an FK-06X.

#### 2.2 Realtime push — measured, and deliberately not the headline

The OAuth WebSocket does stream `Shelly:StatusOnChange` sub-second, with no
subscribe frame at all — but only for devices the account **owns**. For a
device shared *to* the account, status requests answer `WRONG_ID`, subscribe
attempts `BAD_REQUEST`, and a passive listen yields no frames whatsoever.
Battery and BLU devices sleep and never push, regardless of ownership. The
device id inside a push frame is decimal where the HTTP inventory is hex;
`decimal == int(hex_mac, 16)` maps them (BLE-bridged `XB…` ids stay strings on
both sides).

The consequence that retired the original framing: the poll is **one
account-wide request**, not one per device. So long as a single shared or
sleeping device exists, no request falls away — push can *loosen* the poll
interval, never remove it. That makes push a latency improvement layered in
front of an unchanged poll, which is worth building and is not worth a
headline. It is not built.

#### 2.3 Signing in instead of pasting a key — measured, not built

A by-product of building the control channel, worth writing down before it is
forgotten. The OAuth access token minted for the relay also works on the
**documented HTTP API**, as a bearer header:

| Request | Answer |
|---|---|
| `POST /device/all_status`, `Authorization: Bearer <access_token>` | **200**, the full account snapshot |
| the same token sent as the `auth_key=` body parameter | 401 `invalid_token` |

Measured 2026-09-05 on a live account. So an installation could in principle be
set up with **one sign-in and no `auth_key` at all**, instead of a key *and* (for
control) a sign-in.

Two reasons it is not being built yet, in this order. It would put a **second
authentication path into the poll**, which is the one part of this integration
that has never broken; and the gain is convenience during setup, not capability.
The one real argument for it is a failure class it removes: a stored `auth_key`
is invalidated server-side when the account password changes, and nothing tells
the user — the integration simply starts answering 401. A token that refreshes
itself does not have that failure.

Non-goals in M2:
- Per-device webhook subscriptions (the relay delivers everything).
- An MQTT path. Home Assistant already ships an MQTT integration; a second one
  inside this one would be duplicated code with a worse story.

### Milestone 3 — HACS default-store submission  ✅

**Goal:** Entry in the [HACS default integration list](https://github.com/hacs/default),
so that users no longer need to add this as a custom repository URL.

**Done** — the integration is in the HACS default store and installable
without adding a custom repository. One caveat carried over: the icon in
the HACS overview list cannot be fixed from this repository (the
`home-assistant/brands` path no longer applies to custom integrations since
HA 2026.3); the integration ships a local `brand/` folder instead, which
HA serves for the device and entity pages.

Prerequisites (all met):
- Logo submission to [home-assistant/brands](https://github.com/home-assistant/brands)
  as `core_integrations/shelly_cloud_diy/{icon.png,logo.png}` — clean
  variants without the `notDIRK` wordmark and fork symbol will be
  generated at this point.
- First stable (non-`-dev`) release tag.
- README finalised and passing the HACS style review.
- Issue tracker with at least a few closed / triaged issues (to show
  active maintenance).
- Optional: simple GitHub Actions CI that runs lint and any existing
  tests on push / PR.

### Milestone 4 — Quality-scale improvements  🔄 (partly landed)

Path to HA Core quality-scale `silver` / `gold`:
- ✅ `async_get_config_entry_diagnostics` for sanitized export — shipped
  (`diagnostics.py`), and it is what makes remote issue triage possible.
- ✅ Repair issues for actionable states — shipped in v0.8.0 and grown
  since: a sustained rate limit, devices that vanished from the account, a
  failed history import, a welded relay contact (v0.9.0) and the device
  health thresholds (v0.11.0). Five cards, all informational and all
  aggregated per config entry. Note the module is `repair_issues.py`, not
  `repairs.py`: on HA 2025.1.4 the platform loader rejects a `repairs.py`
  that exposes no `async_create_fix_flow`, and none of these conditions can
  be fixed from inside Home Assistant anyway.
- ⏳ Test coverage target ≥ 70 %.
- 🔄 CI: lint, type-check (mypy), test matrix against supported HA versions
  — GitHub Actions runs hassfest + HACS validation today; local test runs
  cover the oldest and newest supported HA release.

(Not committed to a timeline — gated on whether a Core submission
materialises as a goal.)

## Differentiation vs existing projects

| Project | Auth method | Realtime | Shared devices | Maintained | Notes |
|---|---|---|---|---|---|
| **`notDIRK/shelly-cloud-diy-ha`** (this repo) | `auth_key` (shipped) / OAuth (M2, built) | HTTP poll 5 s today; WebSocket push for owned devices planned, with poll fallback | ✅ | 🔄 active | Full Gen1 + Gen2 + Gen3 + BLE-gateway coverage |
| [`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha) | Integrator API token (gated by Shelly) | WebSocket push | ❌ (consent-flow is per-owner) | ✅ active | Private users typically cannot obtain the token |
| [`home-assistant/core` Shelly integration](https://www.home-assistant.io/integrations/shelly/) | Local LAN (mDNS / direct IP) | LAN push | ❌ (remote / shared devices not reachable over LAN) | ✅ maintained by HA Core | Mainstream; requires LAN reachability |
| [`StyraHem/ShellyForHASS`](https://github.com/StyraHem/ShellyForHASS) | Local LAN | LAN push | ❌ | ❌ *"ShellyForHass will no longer receive further development updates"* per README | Folded into HA Core |
| [`vincenzosuraci/hassio_shelly_cloud`](https://github.com/vincenzosuraci/hassio_shelly_cloud) | Username/password (reverse-engineered browser calls) | HTTP polling | ? | ❌ last push 2019 | Switches only; README warns HTTP parsing is fragile |
| [HA YAML Blueprint](https://community.home-assistant.io/t/controlling-shelly-cloud-devices-in-home-assistant/928462) | `auth_key` (same as this project) | ❌ command-only | ? | ✅ community-maintained | *"The device state is not updated from the cloud"* — cannot read state back |
| [`corenting/poc_shelly_cloud_control_api_ws`](https://github.com/corenting/poc_shelly_cloud_control_api_ws) | OAuth | WebSocket push | ? | Explicitly labelled POC, not an integration | Reference implementation for the M2 OAuth flow here |

The short version: there is currently **no other maintained HA
integration that combines Cloud-Control-API-based access with state
reading AND shared-device support AND Gen1/Gen2/Gen3/BLE coverage**. The gap
is real, which is why this project exists.

## Rate limits, latency, and honest expectations

**Shelly's documented rate limit:** 1 API request per second per account
(source: [Shelly Cloud Control API docs, Getting Started](https://shelly-api-docs.shelly.cloud/cloud-control-api/)).

**Milestone 1 traffic profile:**
- A single `POST /device/all_status` returns the complete state snapshot
  of every device your account can see (owned + shared + BLE-bridged).
  58-device accounts return ≈ 60 KB per request.
- Default poll interval: 5 s → average traffic ≈ 12 KB/s outbound HTTPS.
  Configurable down to 3 s (24 KB/s at 58 devices) for snappier state or
  up to 60 s for low-traffic / battery-sensitive setups.
- User-initiated commands (switch on/off, dim, roller) are dispatched
  immediately via separate HTTP POSTs; they do not wait for the next
  poll cycle. Commands and polls share the 1 req/s budget, so the
  default 5 s interval leaves ~4 req/s of command headroom.
- Observed state-change latency: **p50 ≈ 2.5 s, p99 ≈ 5 s** at default
  poll interval. For weather station / energy metering use cases this is
  a non-issue; for light-switch feedback it can feel gentle.

**Milestone 2 traffic profile (revised twice, and the second revision matters):**
- Outbound poll traffic: **push does not reduce it at all**, unless you choose
  to loosen the interval. The poll is *one account-wide request* covering every
  device at once, so a single shared or sleeping device keeps that one request
  necessary and nothing falls away. What push buys is that a *longer* poll
  interval becomes acceptable — a saving the user makes, not one the code
  makes. An earlier version of this document promised "0 bytes steady state",
  and a later one "reduced, not eliminated"; both predate that arithmetic and
  both were wrong.
- Latency: **< 100 ms** for owned, mains-powered devices; unchanged poll
  latency (p50 ≈ 2.5 s) for shared and sleeping ones.
- Cost: one persistent WebSocket connection per Home Assistant instance, plus
  a token refresh roughly once a day. Cloud control already pays that cost
  when it is switched on; push would add no second connection.

## Security and data handling

- The `auth_key` is stored in `entry.data` (Home Assistant standard
  config-entry storage, plaintext at rest in `.storage/core.config_entries`).
  The key grants broad device control — treat it like a password.
- It is displayed by the Shelly App under **User settings → Authorization
  cloud key**. Changing your Shelly password invalidates it
  server-side, which is the intended rotation mechanism.
- Milestone 1 does not store email or password.
- Cloud control (Milestone 2) sends `sha1(password)` to
  `api.shelly.cloud/oauth/login` once, at sign-in. What `entry.data` holds
  afterwards is the resulting record — access token, refresh token, expiry —
  and nothing else. The password itself is never stored, and switching cloud
  control back off deletes the record.
