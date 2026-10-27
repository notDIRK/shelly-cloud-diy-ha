# Changelog

Every stable release of **Shelly Cloud DIY**, newest first. Beta prereleases
are left out — each one is a step towards the stable entry above it.

The GitHub release page carries the same information in longer form, with the
full reasoning and the reporters' credits:
<https://github.com/notDIRK/shelly-cloud-diy-ha/releases>. This file exists so
the history is readable from a checkout alone, including on the Gitea mirror,
which has no release pages.

## v0.17.0 — 2026-10-27

- **New: calibrate a Shelly BLU TRV from Home Assistant.** Every BLU TRV
  behind a BLU Gateway Gen3 gets a **Calibrate** button. It sends Shelly's
  documented `TRV.Calibrate` through the gateway and is available while
  cloud control can reach the gateway, like the setpoint. During
  calibration the valve opens and closes fully once. If the gateway stops
  waiting for the valve's answer (`Error: Timeout`), the press is not
  reported as a failure: on real hardware the valve calibrated about a
  minute later anyway. Every other refusal still shows an error.
- **New: calibration problem sensor** per BLU TRV (diagnostic). It is on
  while the valve reports `not_calibrated`, which Shelly describes as "the
  TRV will not function properly". It works without cloud control. Tested
  on two valves in one installation, both calibrated successfully from the
  button. Requested in #55, tracked in #58.

## v0.16.4 — 2026-10-26

- **Fix: a Shelly BLU TRV shows its room temperature and setpoint again on
  gateways that report them only in the valve's remote status.** Some BLU
  Gateway Gen3 setups leave `current_C`, `target_C` and `pos` out of
  `blutrv:<id>` and carry them in `blutrv_rstatus:<id>` instead. The climate
  entity read only the first place, so the thermostat card stayed empty
  although setting a temperature worked. Each field is now read from
  `blutrv:<id>` first and from the remote status when it is missing there;
  gateways that already worked are unaffected. The diagnostics coverage
  report counts `blutrv_rstatus:<id>` as covered only while it supplies a
  value. Reported in #55.

## v0.16.3 — 2026-10-26

- **Fix: cloud control no longer asks to sign in again for a device in
  another home.** With an account split into several homes, the cloud relay
  answers `UNAUTHORIZED` for a device of a secondary home, and the
  integration took that as an expired sign-in and started re-authentication.
  `UNAUTHORIZED` now counts as an expired sign-in only for a device that has
  already answered on the current connection; for any other device it is a
  refusal for that device alone: no re-authentication, no cloud-control
  switches for it, one warning in the log. Devices of a secondary home are
  still not controllable over the cloud — the relay refuses them. Reported
  and confirmed on hardware in #52.

## v0.16.2 — 2026-10-12

- **Fix: several homes of one Shelly account can now be added.** An account
  split into several homes has one Authorization cloud key per home, and all
  of them live on the same cloud server. Setup tied an entry to the server
  alone, so adding the second home failed with "already configured". An entry
  is now one per key: the second home's key sets up a second entry, and
  adding the same key twice is still refused, including next to entries
  created before this release. Existing installations are unaffected.
  Reported in #52.

## v0.16.1 — 2026-10-09

- **Fix: a temperature below absolute zero is no longer recorded.** Right
  after a Home Assistant restart, a Bluetooth weather station behind a BLU
  gateway briefly reported -999 °C before its first real reading 5–10 seconds
  later. That one value went into the history and pulled the hour's long-term
  statistics down to -999. A temperature below -273.15 °C cannot be a
  measurement, so it now counts as "no reading" and the entity stays unknown
  until a real value arrives. The rule is the physical limit rather than a
  list of placeholder values, and it applies to every temperature the
  integration shows: Bluetooth temperature and dew point, Gen2+ temperature
  sensors and device temperatures, Gen1 temperature, and the BLU TRV's
  current and target temperature. Reported in #51.
- **Docs: the Firmware update flag works on sleeping battery devices too.**
  The feature table in both READMEs now says so: verified on an H&T Gen3, the
  flag is there while the device sleeps and survives a Home Assistant restart
  (#32) — the built-in integration loses it on restart.

## v0.16.0 — 2026-10-08

- **New: a "Firmware version" diagnostic sensor on every Bluetooth (BLU)
  device.** The cloud has always carried the version a BLU device runs, and
  nothing showed it: the Firmware update flag reads a block only Wi-Fi devices
  have, and the device page lists no software version. On the account it was
  measured against, 20 of 24 reported versions were 1.0.16 while two devices
  were still on the release candidate 1.0.9-rc3 — invisible until now. Read
  from the poll that already runs, so it costs no extra request. Five of the
  29 measured devices report an empty version; those get no entity until the
  version is known, the same rule the gateway signal sensor follows.

## v0.15.0 — 2026-10-03

- **Fix: Home Assistant no longer demands a new Authorization cloud key over a
  single unexplained refusal.** Shelly answers HTTP 401 in at least three
  different situations and names the credential in only one of them. Every
  401 that was not the known rate-limit body was treated as "your key is
  invalid" — immediately, on the first occurrence — which stops the
  integration until somebody pastes a key by hand, even when the next poll
  would have worked. Now a refusal that names the key still goes straight to
  re-authentication, and an unexplained one is an ordinary failed poll that
  only escalates after it has persisted across four attempts **and** five
  minutes. One successful poll clears it. Reported by
  [@Frido1980](https://github.com/Frido1980) (#50), with a second case on my
  own account; neither had a password change behind it.
- **New: the re-authentication dialog can correct the server URI as well.**
  Shelly can move an account to a different cloud server, which leaves the key
  working and the stored URI wrong — the one situation this dialog exists for
  that it could not previously repair, because it silently reused the URI. The
  field is prefilled, so for everyone else nothing changes.
- **New: diagnostics report the last authentication refusal** — HTTP status,
  whether Shelly actually named the credential, how many consecutive refusals,
  and a short excerpt of the response. The excerpt is capped and the key is
  redacted out of it. "Why did it ask me to re-authenticate" was unanswerable
  before, including for me.
- The re-authentication dialog no longer claims the key was rejected when the
  integration cannot tell that it was.

## v0.14.1 — 2026-10-03

- **Fix: the setup and re-authentication dialogs pointed at a menu that no
  longer exists.** The Authorization cloud key moved in the Shelly Smart
  Control app; it now lives under *Settings → Access and permissions → Get
  key*. Every place that named the old path — both dialogs, both READMEs and
  the key documentation — is corrected. Reported by
  [@Frido1980](https://github.com/Frido1980) (#49). The app puts that section
  under *Home* in some versions and under *User settings* in others, so the
  dialogs name the section rather than the menu above it.
- **Fix: `aiohttp` is no longer listed as a requirement.** Home Assistant
  ships it itself, and as of 2026-10-01 hassfest rejects a custom integration
  that lists it — the nightly validation had been failing since. Nothing
  changes for an installed integration; the import was always satisfied by
  Home Assistant. A test now guards the whole class, so the next such rule
  fails on the commit that breaks it rather than in the next morning's cron
  mail.

## v0.14.0 — 2026-09-27

- **New: virtual numbers, texts and dropdowns can be written, not just read.**
  A Gen2+ device's virtual `number`, `text` and `enum` components have been
  read-only sensors since v0.6.0. With cloud control switched on, each one now
  also gets a control entity beside its sensor — a number with the component's
  own range, step and unit, a text box capped at its own length limit, and a
  dropdown carrying its configured options. The sensors stay exactly where they
  are, so nothing that already points at one has to change (#48).
- **New: a script's running state, and a switch to start and stop it.** Every
  Gen2+ device that carries a script now reports whether it is running, for
  everybody — that one needs no opt-in, because the flag rides in the ordinary
  poll and a script that died on a device you can only reach through the cloud
  is otherwise invisible. With cloud control on, a switch starts and stops it.
  Both carry the script's own name (#48).
- The write paths were measured on real hardware before they were built, not
  inferred from the documentation: a virtual number, text and enum plus a
  throw-away script were created on a Shelly 1PM Mini G3, written through the
  cloud relay, and the device's own value read back locally after every write.
  The negative control — the same call with only the value invalid — was
  refused and changed nothing, so a success answer really does mean the write
  happened.
- Devices whose only writable component is a number, text, enum or script are
  now included in the ownership probe. Before this they were never asked about,
  so they could never have gained a control entity whatever the relay said.
- **Fix: BLU TRV valves now carry the name you gave them in the Shelly app.**
  They shipped as "BLU TRV" / "BLU TRV 2" in v0.13.0 because no payload
  appeared to carry the name. It is in the account's alias listing, where each
  valve has a child record of its own with its Bluetooth address and its name —
  the same listing this integration already requests for device names, so the
  valve names cost no extra request. Found and confirmed on real hardware by
  [@gerok1984](https://github.com/gerok1984), who also measured the two roads
  that do *not* work, which is why neither is attempted (#48).
- A command the device itself refuses now says why. Writing a value a component
  does not accept used to surface as `JRPC_ERROR`, which is the same string for
  every rejected value; the device's own sentence — "Invalid argument 'value':
  not in options!" — now reaches the UI with it.

## v0.13.0 — 2026-09-27

- **New: Shelly BLU TRV thermostatic valves.** A BLU Gateway Gen3 carries each
  paired valve as `blutrv:<id>`, and none of it produced an entity: no
  temperature, no setpoint, no battery. Every valve now gets a climate entity
  with the room temperature, the valve's own setpoint and whether it is
  currently open, plus battery and signal sensors of its own. A gateway whose
  only RPC components are valves is also recognised as Gen2 now — the same
  second half the smoke detector had in v0.12.1 (#48).
- **Setting the target temperature works, over the opt-in cloud relay.** The
  documented API cannot write it. The relay can, but only if the call is
  addressed to *the gateway* and wraps the valve's own RPC — `BluTrv.Call`
  carrying `TRV.SetTarget`. Sent to the valve's own id it is refused with
  `WRONG_ID`, indistinguishable from a device you do not own. With cloud
  control off the entity is still there and still reads; it just has no
  setpoint to drag.
- Both shapes come from a real sanitised cloud snapshot of a gateway with two
  valves, contributed by [@gerok1984](https://github.com/gerok1984), who also
  verified the write path against that hardware and published a working
  implementation first. This one is written against the same evidence (#48).
- Still uncovered on such a gateway, and deliberately so: `blutrv_rstatus:<id>`
  and `blutrv_rinfo:<id>`. They mirror the valve's own status and firmware
  info, and every reading in them that is worth an entity is already read from
  `blutrv:<id>` itself. The valve-error list in there has only ever been seen
  empty, so nothing is built from a field whose contents are unknown.

## v0.12.1 — 2026-09-20

- **Fix: a Gen2+ smoke detector now creates its alarm entity.** A Shelly Plus
  Smoke arrived with its battery, firmware, reporting and Wi-Fi diagnostics and
  without the one reading a smoke detector exists for. It reports detection as
  an RPC component, `smoke:<id>`, which the entity builders did not know —
  exactly the gap the Gen4 flood sensor had in v0.10.0 and the Gen1 flood and
  smoke sensors had in v0.11.0. A smoke-only device is now also recognised as
  Gen2 on its own, rather than by leaning on an incidental battery component.
  Reported by @alarmesdomotique (#47).
- That is the third report of the same shape, so the builder for these alarm
  components is now one table rather than a third copy of the same block: the
  next device of this kind costs a line, not a review.

## v0.12.0 — 2026-09-05

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
- Cloud control was confirmed end to end on real hardware before release: the
  switch appears beside the read-only sensor, the command reaches the device
  over the cloud in about two seconds (verified by a second, independent
  integration watching the same device locally), and the entity's own state
  follows from the next poll rather than from an assumption. Diagnostics
  reported the device as owned and none unclassified.
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
