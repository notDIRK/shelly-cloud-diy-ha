# Shelly Cloud DIY — Home Assistant integration

<img src="https://raw.githubusercontent.com/notDIRK/shelly-cloud-diy-ha/main/images/icon.png" alt="Shelly Cloud DIY" width="128">

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/notDIRK/shelly-cloud-diy-ha)](https://github.com/notDIRK/shelly-cloud-diy-ha/releases)

> 🇩🇪 **Deutsch** · 🇬🇧 **English (you are here)** — [`README.de.md`](README.de.md) is the German mirror of this page.

**Shelly Cloud DIY** connects Home Assistant to your Shelly fleet through the
self-service **Cloud Control API** — reaching the devices a local-only
integration can never see (shared devices, remote sites, the Shelly BLU family
behind a gateway), while keeping your switching **local-first**: the cloud is an
overlay, never in the control path.

Available in the **[HACS default store](https://hacs.xyz)** — just search for
*Shelly Cloud DIY*, no custom-repository URL needed.

---

## Why this integration

The official Home Assistant Shelly integration is excellent — and **you should
keep using it** for the devices on your LAN. It is sub-second fast and works
when the internet is down. Shelly Cloud DIY does **not** replace it. It fills
the gaps the LAN integration cannot reach:

### No gatekeeping — start in 2 minutes

**You can set this up yourself, right now, without asking anyone for
permission.** Open the Shelly app, copy your cloud `auth_key`, paste it into
Home Assistant — done in about two minutes. There is no application form, no
support email, no approval queue, no waiting on a vendor's "yes". You own your
access.

That matters because the *other* cloud route — the **Integrator API** used by
`engesin/shelly-integrator-ha` — needs a token that **Shelly gates**. In
Shelly's own words, *"licenses for personal use are not provided"*, so a private
user must apply and **depend on Shelly's goodwill** — and may simply be refused.
This project never puts you in that position.

| | This project | Integrator-API route |
|---|---|---|
| **Auth** | `auth_key` you generate yourself (M1) / OAuth (M2) | Integrator API token, **gated by Shelly** |
| **Getting access** | **Self-serve, now** — copy & paste in ~2 min | **Ask, then hope** — apply, no private-use licences |

- **Shared devices.** Cloud Control API can see devices that other Shelly users
  have shared into your account — something a LAN integration structurally
  cannot do. (Verified empirically against a real ECOWITT WS90 weather station
  shared from another person's account.)
- **Remote / cloud-only devices.** Devices at another site, or any device that
  is only reachable through the cloud, become visible in Home Assistant.
- **Self-service access — no gatekeeper.** You generate an `auth_key` yourself
  in the Shelly app in seconds (OAuth is the Milestone 2 path; either way it is
  self-service). No gated Integrator-API token, no application form, no support
  email, no approval queue. Your access never hinges on a vendor's discretionary
  "yes" to a private user.

> **Local-first, cloud-optional.** For locally-controlled devices, keep the
> native local `shelly` integration — it is faster and offline-resilient. This
> integration adds cloud-only and shared devices, remote visibility, and
> migration tooling on top. The cloud is an **overlay for visibility**, never a
> dependency for turning your lights on. Entity creation is **opt-in**, so you
> don't get duplicate control entities for devices you already manage locally.

### A concrete example: a shared weather station

Imagine a neighbour or family member owns an ECOWITT WS90 weather station paired
to *their* Shelly account, and shares it with you in the Shelly app. The local
HA Shelly integration will never see it — the hardware isn't on your LAN and
isn't yours. With Shelly Cloud DIY, that shared station shows up in Home
Assistant as ordinary sensor entities (temperature, wind, rain, UV, …), ready to
drop onto a dashboard or drive automations — without ever touching the owner's
account beyond the share they granted you.

<img src="https://raw.githubusercontent.com/notDIRK/shelly-cloud-diy-ha/main/images/shared-weather-station-ws90.png" alt="Home Assistant dashboard of a shared ECOWITT WS90 weather station: temperature, feels-like and humidity gauges, wind compass, 24h temperature and pressure graph, station battery and voltage, hourly and 5-day forecast, UV and rain sensors" width="760">

*A shared ECOWITT WS90 weather station — paired to someone else's Shelly account — surfaced as native Home Assistant sensor entities via the Cloud Control API.*

### How it compares

| Project | Auth | Realtime | Shared devices | Maintained |
|---|---|---|---|---|
| **`shelly-cloud-diy-ha`** *(this repo)* | `auth_key` (M1) / OAuth (M2) | HTTP poll 5 s → WebSocket push (M2) | ✅ | 🔄 active |
| [`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha) | Integrator API token *(gated by Shelly — "licenses for personal use are not provided", so private users must apply and may be refused)* | WebSocket push | ❌ | ✅ |
| [HA Core — official Shelly integration](https://www.home-assistant.io/integrations/shelly/) | Local LAN / mDNS | LAN push | ❌ *(remote / shared devices unreachable over LAN)* | ✅ |
| [`StyraHem/ShellyForHASS`](https://github.com/StyraHem/ShellyForHASS) | Local LAN | LAN push | ❌ | ❌ **discontinued** per its README |
| [`vincenzosuraci/hassio_shelly_cloud`](https://github.com/vincenzosuraci/hassio_shelly_cloud) | Username/password *(reverse-engineered)* | HTTP polling | ? | ❌ last push 2019 |
| [HA YAML Blueprint (2025)](https://community.home-assistant.io/t/controlling-shelly-cloud-devices-in-home-assistant/928462) | `auth_key` | ❌ command-only, **no state read** | ? | ✅ |

No other maintained Home Assistant integration currently combines **Cloud
Control API access**, **state reading**, **shared-device support**, and **Gen1 /
Gen2 / BLE-gateway coverage** in one package. That is the gap this project closes.

---

## Features

| | What it does | Status |
|---|---|---|
| ☁️ **Cloud polling** | Reads device state for every device visible to your Shelly account — owned, shared, remote, and BLE-bridged (Shelly BLU family via a BLU Gateway). | ✅ shipped |
| 🔌 **Opt-in entities** | Switches, lights, covers, sensors, binary sensors, buttons — materialised only for the devices you choose, so you avoid duplicates with the LAN integration. | ✅ shipped |
| 📡 **Offline detection** | A per-device **Reporting** sensor that turns off when a device stops checking in — the signal `cloud.connected` cannot give you (see below). | ✅ shipped |
| ⚡ **Stuck-contact warning** | Warns when a relay reports itself as open while the device's own meter still sees a load — a welded contact (see below). | ✅ shipped |
| 📈 **Energy history import** | Imports historical energy data into Home Assistant long-term statistics. | ✅ shipped |
| ⚙️ **Config + options flow** | Paste your `auth_key`; tune the poll interval and per-device enablement later. | ✅ shipped |
| 🌍 **Localised UI** | English and German translations for every user-visible string. | ✅ shipped |
| 🎨 **Brand icon** | Ships its own brand icon (HA 2026.3+). | ✅ shipped |
| 🗺️ **Fleet-Map** | Read-only overlay matching cloud devices to their local twins by MAC. | 🧪 beta |
| 🔁 **Replace device** | Transplant a dead Shelly's HA identity onto a new unit of the same model. | 🧪 beta |
| 🧹 **Account cleanup** | Find (and opt-in remove) HA devices whose Shelly hardware is no longer in your account — sold, reset, or deleted. | 🧪 beta |

It runs **alongside** Shelly Cloud and the Shelly app — it does not take over or
lock out other clients — and **does not require** Home Assistant to be exposed to
the public internet.

### Knowing when a device dies

Every device gets a **Reporting** binary sensor (diagnostic category). It turns
off when the device stops checking in with Shelly Cloud — which is what a power
cut looks like from the cloud's side.

To be clear about what this does and does not buy you: for a device Home
Assistant can reach on your LAN, the native integration already notices when it
goes away, and probably sooner. The cloud view earns its keep for devices Home
Assistant has **no local path to at all** — a shared device, a second site, a
BLU sensor behind someone else's gateway — where there is otherwise no liveness
signal whatsoever. It is not a way to survive your own outage: if your network
is down, Home Assistant cannot reach the cloud either, and the sensor says so by
going unavailable rather than guessing.

It exists because the obvious signals do not work. Measured against a live
64-device account:

- `cloud.connected` still read **connected** thirteen minutes after a device was
  physically unplugged, and read connected for *every* device on the account —
  including ones silent for hours. It is a transport flag the cloud caches, not
  a liveness signal. (The separate `Cloud` sensor still exposes it, disabled by
  default, for anyone who wants the raw value.)
- Devices do eventually vanish from the cloud's fleet listing, but that took up
  to ten minutes, and the listing also omits healthy devices at random.

So the verdict is based on the one thing the cloud cannot fake — the device
having pushed a new snapshot — timed on Home Assistant's own clock.

**The window is per device, and adapts.** Normal cadences differ by orders of
magnitude: a metering plug reports every 60 s, an idle Plus RGBW PM went 29
minutes between reports, a BLE beacon three days — all perfectly healthy. So the
configured value (Options → *Report a device as offline after*, default 30 min)
is a **base**: a device that is naturally quieter earns a wider window on its
own, battery and BLE devices get their own, and until a device's cadence is
known it gets an hour of grace. Lowering the setting therefore speeds up
detection on devices that report continuously — a freezer's metering plug, say —
without turning your quiet devices into false alarms.

If polling itself breaks (cloud outage, rejected `auth_key`), the sensor goes
**unavailable** rather than reporting your whole fleet as dead.

### Knowing when a relay stops switching off

A switching Shelly reports what it *commanded* and what it *measures* in the
same payload. When the relay says open while the meter still sees a load, the
contact has welded shut — the actuator keeps accepting commands and keeps
reporting *off*, but the load never actually switches off. Anything built on
switching it off (an automation, a schedule, a motion sensor) silently stops
working, and nothing in Home Assistant says so.

Every switching channel that meters its own output therefore gets a **Relay
fault** binary sensor (diagnostic, `problem` class), and a repair notification
appears when one trips. The measured wattage rides along as an attribute,
because that number is the whole argument.

This was built against a unit that actually failed this way — a Shelly 1PM Mini
Gen3 reporting `output: false` and 85.2 W at the same time, for as long as it
was watched, with the lamp visibly on and unswitchable from software. Two things
that run taught, both of which are in the detector:

- **The moment right after a command lies.** One switch-off produced a reading
  of 0 W for about 45 seconds while the load was demonstrably still running. So
  the disagreement has to persist — five check-ins *and* two minutes — before
  anything is said, and a single agreeing reading does **not** retract a
  standing warning. Clearing needs five quiet minutes.
- **The energy counter is too coarse to help.** It stood still for 80 seconds
  and then jumped by 1.0 Wh at once. It is not part of the verdict.

Counted are the device's own check-ins, not our polls: a device that freezes
keeps re-serving its last payload, and counting polls would let one stale
snapshot be repeated into an accusation. A warning already raised is kept when a
device goes quiet — a welded contact does not unweld because the device dropped
off the cloud.

Deliberately conservative: anything under 5 W is ignored (snubbers, LED drivers
and meter noise all put a trickle on an open contact), clamp-metering devices
are never judged because their measurement has no defined relationship to any
contact next to it, and Gen1 roller-shutter devices are skipped entirely — there
"relay off, power flowing" is just a moving shutter. If a Shelly of yours sits in
a two-way circuit, where the other switch can push current through the meter
while the relay is genuinely open, switch the detector off in the options.

---

## Requirements

- A Shelly Cloud account with at least one device paired to it.
- Home Assistant **2024.1** or newer.
- Outbound HTTPS reachability from Home Assistant to `*.shelly.cloud` (standard).
- No inbound internet reachability needed on the Home Assistant side.

---

## Installation (HACS)

The integration is in the **HACS default store**, so no custom-repository URL is
needed.

1. Open **HACS** in Home Assistant.
2. Search for **Shelly Cloud DIY**.
3. Click **Download** and pick the latest version.
4. Restart Home Assistant.
5. Continue with *Getting your credentials* and *Setup* below.

> 🧪 **Want the device-swap beta?** The Fleet-Map and Replace-device features
> ship in beta releases (`0.5.0-beta…`). In HACS, open the integration's
> three-dot menu → **Redownload** → enable **Show beta versions**, then pick the
> latest `…-beta` build. Beta features are **opt-in**; the stable line keeps
> working unchanged if you stay on a non-beta version.

### Upgrading from the custom repository

If you previously added this integration as a HACS *custom repository* (under the
old `notDIRK/shelly-integrator-ha` URL), moving to the default-store entry is
safe — the integration **domain stays `shelly_cloud_diy`**, so your config entry,
entities (and therefore your dashboards) are preserved. Notes from a real
migration:

- **HACS removes the redundant custom-repository entry automatically** once the
  repo is served from the default store. If your custom entry "disappears on its
  own", that is expected — not a bug.
- When HACS asks during removal whether to **also remove the configuration**,
  **decline it**. That option deletes the Home Assistant config entry (devices,
  area assignments, entity IDs). Remove only the downloaded code; keep the config
  entry.
- **Order matters:** re-download from the default store **first**, then restart
  Home Assistant. Removing the files leaves the integration running in memory (it
  looks fine), so restarting in that state briefly marks all entities
  `unavailable`.
- The harmless log line *"custom integration shelly_cloud_diy which has not been
  tested by Home Assistant"* is normal for any `custom_components/` integration
  and is not an error.

### Updating

When HACS shows a new version:

1. In **HACS**, open **Shelly Cloud DIY** → three-dot menu → **Redownload** and
   pick the latest version.
2. **Restart Home Assistant** (Settings → System → Restart).

The restart is the easy-to-forget step. New entities from a release — for
example a sensor for a newly supported device — **only appear after the
restart**, so a missing entity right after updating is almost always "HACS
updated, but Home Assistant has not been restarted yet" rather than a bug. If an
entity is still missing after the restart, reloading the integration (Settings →
Devices & Services → Shelly Cloud DIY → ⋮ → Reload) forces a fresh cloud poll.

---

## Getting your credentials

The Cloud Control API is self-service. You do not need to contact Shelly, file a
form, or wait for approval.

1. Open the **Shelly App**.
2. Go to **User settings → Authorization cloud key**.
3. Tap **GET KEY**.
4. You receive two values: an **`auth_key`** (a long opaque string) and a
   **server URI** (e.g. `shelly-42-eu.shelly.cloud`).
5. Both values are pasted into the Home Assistant config flow during setup.

> 🔐 **Security** — The `auth_key` grants control of every device visible to your
> Shelly Cloud account (including devices shared with you). Treat it like a
> password. To rotate it, change your Shelly account password in the App — the
> old key invalidates server-side and a new one is generated.

You are installing code from a stranger and handing it an account-wide
credential, so **[what this integration does with your auth key](docs/AUTH_KEY.md)**
spells out where the key travels, where it is stored, where it never goes, and
how to verify every one of those claims yourself in about two minutes — with
commands you type, not a script I wrote. It also names the parts I am not happy
about, because a page that only listed good news would not be worth reading.

---

## Setup

1. Home Assistant → **Settings → Devices & Services → Add Integration →
   "Shelly Cloud DIY"**.
2. Paste your `auth_key`.
3. Paste your `server URI` (e.g. `shelly-42-eu.shelly.cloud`).
4. Click **Submit**. Devices are fetched immediately; pick which ones become
   entities.

---

## Rate limits and latency (open communication)

Shelly documents a rate limit of **1 API request per second per account**
([source](https://shelly-api-docs.shelly.cloud/cloud-control-api/)). The
integration respects this budget by consolidating state fetches into a single
`POST /device/all_status` call per poll cycle — one request returns the full
state of every device visible to the account.

| | Current (Milestone 1) | Future (Milestone 2) |
|---|---|---|
| Transport | HTTP polling | WebSocket push |
| State-update latency (p50 / p99) | ~2.5 s / ~5 s | < 100 ms / < 500 ms |
| Outbound traffic (≈ 50-device account) | ≈ 12 KB/s at 5 s poll interval | ~0 bytes steady |
| Commands (switch, dim, cover) | immediate HTTP POST, independent of poll timing | via WebSocket |
| Credentials | `auth_key` + server URI | Shelly email + password (OAuth2) |

The 5-second poll default stays well under the 1 req/s budget while leaving
command headroom. Sensor values (temperature, energy, weather data) feel live;
switch feedback in the UI feels gentle — Milestone 2's WebSocket push closes that
gap. You can tune the poll interval between 3 s and 60 s via the options flow.

Shelly also notes that the HTTP endpoints are *intentionally only lightly
documented* and that parameter formats may change. The integration pins to the v1
endpoint shape and will react to changes if and when they occur — a real
long-term risk worth being aware of.

---

## Troubleshooting

If a device misbehaves — wrong state, a value that won't change, an entity
that doesn't appear — the fastest way to a fix is to attach that device's
**diagnostics** to a bug report.

1. **Settings → Devices & Services → Shelly Cloud DIY**
2. Open the affected device → **⋮ → Download diagnostics**
3. Attach the `.json` file to a [new issue](https://github.com/notDIRK/shelly-cloud-diy-ha/issues/new/choose)

The download is the raw Shelly Cloud status the integration builds entity
state from, so it usually turns guesswork into a precise fix. Your privacy
is respected: device names and network identifiers (name, SSID, IP, MAC) are
redacted automatically, and the file lists exactly what was withheld under
`redacted_keys`. It also includes the integration's coordinator health (last
update success and last error) to speed up diagnosis.

If the question is about the **Reporting** sensor — a device flagged offline
that is demonstrably fine, or one that never flags at all — the same download
answers it directly: the `reporting` block states how long the device has
actually been silent, how much silence it is currently allowed, whether that
allowance is the configured one or a wider one the device earned, and whether
its cadence has been learned yet.

For errors, enabling debug logging for `custom_components.shelly_cloud_diy`
(Settings → Devices & Services → Shelly Cloud DIY → Enable debug logging)
and reproducing the problem adds a useful timeline.

---

## Roadmap

Full plan with per-milestone scope, non-goals, and limitations:
[`docs/ROADMAP.md`](docs/ROADMAP.md). Feature deep-dives:
[`docs/FEATURE-HIGHLIGHTS.md`](docs/FEATURE-HIGHLIGHTS.md).

### ✅ Milestone 1 — Cloud polling *(shipped, in the HACS default store)*

HTTP polling with `auth_key`; opt-in per-device entities; shared / remote /
BLE-bridged device support; historical energy import; English + German UI.

### 🧪 Device-swap overlay *(beta — `0.5.0-beta`, opt-in)*

An **opt-in, beta** overlay for people who run Shellys both locally and through
the cloud. Install via HACS **Show beta versions** (see above). Not finished —
try it, but don't depend on it yet.

- **Fleet-Map** — a *read-only* overlay that matches every cloud device to its
  local Home Assistant twin **by MAC** (both Wi-Fi Shellys and Bluetooth / BLU
  sensors), suggests names, and flags any device whose *control* would secretly
  depend on the cloud. Offline devices are included. It never touches the control
  path.
- **Replace device** — transplant a dead Shelly's Home Assistant identity (entity
  IDs, device, name, area, long-term history, and every automation / script /
  scene / dashboard reference) onto a **new unit of the same model**, so you
  don't reconfigure Home Assistant after a hardware swap. **Cloud devices are
  supported now.** The **upstream Home Assistant Core contribution** — an
  ESPHome-style device-replacement repair for the *native* Shelly integration —
  has been **submitted for review**:
  [home-assistant/core#174581](https://github.com/home-assistant/core/pull/174581).
  Native local support in this integration is planned next.
- **Account cleanup** — reconcile the devices this integration created against
  your live Shelly account and report any whose hardware is **no longer in the
  account** (sold, factory-reset, or deleted in the app). *Inform-only by
  default;* removal is strictly opt-in, previewed, and honestly disclosed as not
  automatically reversible. Safe by design: it decides absence only from your
  account's **full device inventory** — never a name list — and refuses to act
  when that inventory looks incomplete.
- **On-device config clone** — *planned.* Clone a Shelly's on-device schedules,
  scripts, and inputs onto its replacement over the LAN, for resilience that
  survives both an internet and a Home Assistant outage.

### 🔭 Milestone 2 — OAuth + WebSocket realtime *(planned)*

Push-based updates instead of polling: sub-second latency and ~0 steady-state
traffic, by authenticating via OAuth and subscribing to Shelly Cloud events over
a WebSocket.

---

## Local-first note

This integration is a deliberate **overlay**. The cloud is used for *visibility*,
*shared/remote devices*, and *migration tooling* — **never** as a dependency in
the control path of a device you can already control locally. For those devices,
keep using the native local `shelly` integration: it is sub-second and works
offline. Shelly Cloud DIY's job is to reach what the LAN cannot, and to stay out
of the way of what it can.

---

## How I work

I maintain this on my own, in my spare time, and I work with AI. I don't hide
that, because the result is what matters and the result is testable: I make the
decisions, I review every change before it ships, and I test against both ends of
the supported Home Assistant range — on real hardware wherever the bug allows it.
Nothing is released that I haven't checked myself. If anything in this code looks
like something I couldn't explain, ask me and I'll answer.

What it buys you is speed, and that is the part I care about most. I hate
waiting. So alerts for new issues reach my phone, and across the issues reported
here so far that has meant a median of **under four hours to a first answer** and
**half a day until the issue was closed** — usually with a beta you can install
straight away. The issue history is public, so check it rather than take my word
for it. The slowest one took five weeks; that was at the very beginning, and it
is the reason the alerts exist.

Two caveats. This is spare time and my own money — the tools aren't free and
nobody pays me for any of this. And I'm one person, so fast is what I aim for,
not something I can promise.

More about me: [github.com/notDIRK](https://github.com/notDIRK)

---

## Help the project

The most direct help costs nothing: a ⭐ on the repo, and a clear bug report or
feature request when something is off.

Enabling **Home Assistant Analytics** (Settings → System → *Analytics*) is also
worthwhile — it is anonymous and opt-in, and it helps the whole HA ecosystem.
One honest caveat: Home Assistant no longer lists *newly added* custom
integrations in its public per-integration statistics (a policy change in HA
2026.3 — the [brands](https://github.com/home-assistant/brands) registry stopped
accepting new custom integrations, and the analytics site only counts domains in
that registry). So enabling analytics won't surface Shelly Cloud DIY by name — but
it still helps the project it lives in, and a ⭐ remains the clearest signal that
this integration is worth maintaining.

---

## License

MIT — see [`LICENSE`](LICENSE).

---

## Fork lineage

Forked from
[`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha)
(Integrator API implementation). The fork relationship is retained for
git-history traceability only; the project has pivoted to a different API, and no
upstream merges are expected. The legacy `v0.2.x` tags are the inherited
Integrator-API implementation, kept for traceability.
