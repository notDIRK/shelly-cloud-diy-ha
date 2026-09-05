# Changelog

Every stable release of **Shelly Cloud DIY**, newest first. Beta prereleases
are left out — each one is a step towards the stable entry above it.

The GitHub release page carries the same information in longer form, with the
full reasoning and the reporters' credits:
<https://github.com/notDIRK/shelly-cloud-diy-ha/releases>. This file exists so
the history is readable from a checkout alone, including on the Gitea mirror,
which has no release pages.

## Unreleased

- **New: switching virtual components over the cloud — opt-in, and off by
  default.** An irrigation controller's zones and a script's boolean can be
  read through the documented Cloud Control API but not written: every
  documented route answers "no such route" (measured, with a known-good call
  as the negative control). They can be written over the cloud WebSocket relay
  Shelly's own app uses, which is **undocumented** and which Shelly support
  declared unsupported on 2026-07-27 — so it is a switch the user turns on
  knowingly, not a default. Switching it on asks for the Shelly account
  sign-in the relay needs (the password is used once and never stored; only
  the resulting token is, and turning the option off again deletes it) and
  opens a second connection used for commands only. It works **only on devices
  the account owns** — Shelly's relay refuses to route to a shared device, and
  every device carrying such a component is asked once per session (never per
  poll), with the answer in diagnostics so
  "why has my device no switch" is answerable from a bug report. The new
  switch is created **beside** the existing read-only sensor of the same
  component, never instead of it: replacing it would silently break every
  automation already pointing at it. A failed command raises an error in the
  UI and in the automation trace instead of reporting a success, and the state
  afterwards comes from the next poll rather than an optimistic guess — a
  guess is exactly what would hide a valve that accepted the command and did
  not move; if the control channel itself is down, the switch goes unavailable
  rather than looking operable. **With the option off nothing about the integration changes:** no
  sign-in, no second connection, no probe, no new entity.
- The poll, the offline detector, the relay-fault detector and the health
  checks are untouched by all of the above, and their tests pass unchanged.
- **New: a Gateway signal sensor for Bluetooth (BLU) devices.** How well the
  bridging gateway hears the device — the only signal figure a BLU sensor has,
  present on every one of them and until now surfaced nowhere. Named after the
  gateway deliberately: a poor reading may mean the gateway sits in the wrong
  place rather than the sensor. The gateway's id rides along as an attribute,
  because a device can be picked up by a different one after a move.
- **New: a Firmware update flag on Gen2+ devices**, with the offered version as
  an attribute. A diagnostic flag, not an installer — flashing firmware stays
  with the local integration. Note this is deliberately not the same default as
  the health card's firmware finding, which stays opt-in: an entity waits to be
  looked at, a repair card interrupts, and on a typical account most devices
  have an update pending.

## v0.11.0 — 2026-09-04

- **New: a warning when a device is running hot, weak or short of memory.**
  Every poll already returned each device's Wi-Fi signal, its component
  temperatures, its free memory and storage, whether a restart was pending and
  whether any component was reporting its own error — and none of it was ever
  interpreted. One repair card now does, for the whole account rather than one
  per device, at no extra request to Shelly Cloud. Thresholds were measured
  against a real 64-device account rather than chosen in the abstract: signal
  at -70/-85 dBm, component temperature at 70/85 °C, free RAM and storage at
  20 %/10 %. Only a component's own heat is judged, never an external Add-on
  probe — a sensor in a boiler flow pipe is hot by design. Bluetooth devices
  are judged on the one figure they have, the signal their gateway reports;
  Gen1 devices are not judged at all, because no Gen1 payload exists to verify
  a threshold against. Pending firmware updates count as a finding only if you
  switch that on. The whole check has an off switch.
- **New: Wi-Fi signal sensors.** The descriptions had existed since the first
  release and no code ever looked them up, so signal strength was present in
  the data on every Gen2+ device and invisible in Home Assistant. Both the
  Gen2+ and the Gen1 sensor are now created. Device diagnostics also gained a
  coverage section naming the parts of a device's payload that still produce
  no entity, so the next gap of this kind shows up in a bug report instead of
  waiting for someone to notice.
- Gen1 flood and smoke sensors (Shelly Flood `SHWT-1`, Shelly Smoke `SHSM-01`)
  now create their alarm entity. As above, the description existed from the
  first release but was never wired to a status key, so these devices arrived
  with their temperature and battery readings and without the one reading they
  exist for (#42).

## v0.10.0 — 2026-09-04

- **New: wet/dry for Gen2+ flood sensors.** A Shelly Flood S Gen4 reports its
  alarm as an RPC component (`flood:<id>`), which the entity builders did not
  know, so the device arrived with its battery and reporting diagnostics and
  nothing else — the one reading it exists for was missing. Every `flood:<id>`
  now creates a moisture binary sensor, and a flood-only device is recognised
  as Gen2 without leaning on its battery component. Contributed by
  [@JTertin](https://github.com/JTertin), reported by @mrhappy79 (#41, #40).
- Gen1 energy meters (Shelly 3EM, `SHEM-3`) now create the returned-energy
  sensors. These meters keep two cumulative counters per clamp channel —
  `total` for consumption and `total_returned` for grid feed-in — but only the
  consumption half was ever instantiated, so anyone feeding PV back into the
  grid lost that reading entirely. One `Energy Returned` sensor per channel, in
  Wh, ready for the Energy dashboard's "Return to grid". Reported by @Pasimk
  (#38).
- Repair cards no longer cut a device name mid-word (#37).

## v0.9.2 — 2026-08-18

- The device picker's bulk action ("tick all" / "untick all") no longer looks
  like it saved. It never did save — it updates the ticks and waits for a
  second, deliberate submit, so an accidental "untick all" cannot silently
  remove every entity — but it re-rendered the identical form, which read as
  "done". The updated list now arrives as its own labelled step, and both
  picker screens show how many devices are currently ticked.

## v0.9.1 — 2026-08-18

- Diagnostics now report what the integration is configured to do: effective
  poll interval, offline window, detector settings, and `gated_out` — how many
  devices the cloud serves that create no entities because the device
  selection excludes them. Credentials stay out of it; device ids appear only
  as the short fingerprints the fleet map already uses.

## v0.9.0 — 2026-08-18

- **New: a warning when a relay stops switching off.** Every switching channel
  that meters its own output gets a *Relay fault* binary sensor plus a repair
  card. A welded contact is otherwise silent: the actuator keeps accepting
  commands and keeps reporting *off* while the load never switches off. Built
  against hardware that failed exactly this way (`output: false` at 85 W), and
  confirmed on it. Conservative by construction — under 5 W is ignored,
  clamp-metering devices and Gen1 roller mode are never judged, and a two-way
  circuit is the one false positive the payload cannot see, so the detector
  can be switched off.

## v0.8.0 — 2026-08-18

- **New: repair issues for persistent polling problems** — the Shelly Cloud
  rate limit throttling your polls, devices disappearing from the account's
  status response, and a failed energy-history import now raise a Home
  Assistant repair card instead of only appearing in the log. (#19)
- Battery devices on permanent power no longer flap between available and
  unavailable. An H&T Gen3 on USB-C keeps waking on its old schedule, but
  external power was taken as proof that a device stays awake, so availability
  fell back to a flag that is false about half the time. (#32)
- No more deprecation warning at startup: the coordinator receives its config
  entry explicitly. (#31)

## v0.7.0 — 2026-08-16

- **New: a *Reporting* sensor for every device.** Shelly Cloud has no honest
  liveness flag — measured on a live 64-device account, `cloud.connected` still
  read *connected* thirteen minutes after a device was unplugged. The sensor is
  built on the one thing the cloud cannot fake: that the device pushed a new
  snapshot. Its window adapts per device, because healthy cadences differ by
  orders of magnitude. New option: "Report a device offline after"
  (default 30 min).
- A device that first appeared while Home Assistant was running got only one
  entity, with no second chance afterwards. (#30)
- Duplicate unique-ID errors when a healthy device was briefly omitted from a
  poll. (#29)

## v0.6.14 — 2026-08-16

- Gen2/Gen3 switches expose their cumulative energy counter and device
  temperature — the energy counter as `total_increasing`, usable in the Energy
  dashboard. Contributed by **@walnuss0815** (#24), the first outside code
  contribution to this project.
- Irrigation zones (e.g. Shelly FrankEver FK-06X) show the names set in the
  Shelly app instead of "Boolean 200". Naming only — switching those zones is
  not possible over the Cloud Control API's HTTP path (#20).

## v0.6.13 — 2026-08-10

- The three "Bulk action" labels in the device picker were German in every
  language. They were never translatable — the text sat in the source and
  skipped Home Assistant's translation lookup entirely. (#18)

## v0.6.12 — 2026-08-05

- 2-channel meters (Shelly Pro EM-50) report their energy: per channel active
  and apparent power, current, voltage, frequency, power factor and both
  energy counters.
- Relay-less meters are recognised as Gen2 devices, so they produce entities at
  all. A Pro 3EM only ever worked because it also reports a temperature.
- New: `pm1` power meters (PM Mini Gen3 and the metered channels of some Gen3
  devices).

## v0.6.11 — 2026-08-02

- The *Cloud* diagnostic sensor now ships disabled by default, matching Home
  Assistant's built-in Shelly integration. On a deep-sleep device its value is
  a boot-timing artifact, not a state. Existing entities are unaffected.

## v0.6.10 — 2026-08-02

- Deep-sleep battery devices (H&T Gen3 and friends) no longer go permanently
  unavailable. Availability came from the cloud's transport flag, which is
  meaningless for a device that is awake for seconds every wake-up period; it
  now follows whether the device keeps checking in. Mains devices are
  untouched. (#13)
- Battery devices get their Shelly-app name again — the lookup was gated on the
  same wrong assumption.

## v0.6.9 — 2026-07-21

- **New: virtual components (read-only).** Gen2/Gen3 `number` / `enum` / `text`
  / `boolean` components created by scripts or a Wall Display become entities,
  enriched with the user-set name, a number's unit and an enum's options.
  Writing is not reachable over the Cloud Control API at all — use the local
  integration for that. (#9)

## v0.6.8 — 2026-07-19

- **New: Shelly BLU Door/Window** — door/window binary sensor and tilt angle
  for gateway-bridged sensors. (#9)

## v0.6.7 — 2026-07-17

- **New: Shelly Pro 3EM** — per phase and total: active and apparent power,
  current, voltage, frequency, power factor, and cumulative plus returned
  energy. Previously only the temperature sensor appeared. (#8)

## v0.6.6 — 2026-07-16

- **New: Shelly Plus Uni COUNT IN** pulse counter and pulse frequency. (#7)
- RGBW2 lights dim correctly in colour mode (brightness drives `gain`) and are
  now full RGBW. (#6)

## v0.6.4 — 2026-07-14

- **New: per-device diagnostics** — download the raw Shelly Cloud status for a
  single device, with control fields kept and identifiers redacted.

## v0.6.3 — 2026-07-14

- Lights and switches no longer flicker or snap back: a command now holds an
  optimistic override until the cloud confirms it or a 10 s window expires, so
  a poll landing before the cloud propagates cannot revert it. (#6)

## v0.6.2 — 2026-07-14

- Dimming no longer fails with a bogus auth error. **Shelly signals its
  1 req/s rate limit as HTTP 401** with a `max_req` body, not 429, so every
  rate-limit hit was reported as an invalid key. The client now reads the body
  and backs off. (#6)

## v0.6.1 — 2026-07-14

- Light entities crashed on every on/off/dim — `light.py` passed a keyword the
  command path never accepted. (#6)

## v0.6.0 — 2026-07-13

- Gen2/Gen3 battery sensors (Shelly H&T Gen3) create entities: temperature,
  humidity and battery. They report no switch/cover/input components and were
  misrouted by the generation detector. (#4)
- **New: `detect_orphans` service** — find, and on explicit confirmation
  remove, Home Assistant devices whose hardware has left the Shelly account.

## v0.5.8 — 2026-07-03

- Gen2 dual-cover devices controlled the wrong channel: both cover entities
  drove cover 0, because the legacy cloud roller endpoint has no per-channel
  selector. Gen2 covers now use the v2 endpoint. (#3)

## v0.5.7 — 2026-07-02

- Docs and packaging: releases ship a HACS-installable ZIP asset, and the
  "Help the project" section was corrected — Home Assistant no longer lists
  newly added custom integrations in its public analytics.

## v0.5.6 — 2026-06-26

- Docs: an *Updating* section — after a HACS update, restart Home Assistant,
  because new entities only appear then.

## v0.5.5 — 2026-06-25

- **New: Shelly BLU Motion** motion binary sensor (validated against 21 live
  devices) and the Gen2 `voltmeter` component as a voltage sensor. (#1, #2)

## v0.5.4 — 2026-06-24

- The brand icon renders without a white box on dark themes (RGBA with a
  transparent background, served from the package's own `brand/` folder).

## v0.5.0 – v0.5.3 — 2026-06-23 … 2026-06-24

- **New: the device-swap overlay** (v0.5.0) — a read-only layer connecting
  cloud devices to their local Home Assistant twins.
- Docs releases: self-service authentication foregrounded (v0.5.1), the
  ECOWITT WS90 example (v0.5.2), and the upstream Home Assistant Core
  submission of the native device-replacement repair (v0.5.3).

## v0.4.x — 2026-04-18 … 2026-06-23

The device-picker and packaging line. Highlights: the v2 name lookup was
dropped after it resolved only 2 of 30 devices (v0.4.3); the device selection
UX went through several shapes before settling on the bulk-action-plus-list
form still in use (v0.4.4 – v0.4.8 — v0.4.8 is where a bulk action stopped
saving immediately and started updating the list instead); the brand icon
moved into the package (v0.4.9); CI workflows and the bilingual README
followed (v0.4.10 – v0.4.12).
