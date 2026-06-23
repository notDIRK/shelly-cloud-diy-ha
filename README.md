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

<!-- screenshot: shared weather station -->

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
| 📈 **Energy history import** | Imports historical energy data into Home Assistant long-term statistics. | ✅ shipped |
| ⚙️ **Config + options flow** | Paste your `auth_key`; tune the poll interval and per-device enablement later. | ✅ shipped |
| 🌍 **Localised UI** | English and German translations for every user-visible string. | ✅ shipped |
| 🎨 **Brand icon** | Ships its own brand icon (HA 2026.3+). | ✅ shipped |
| 🗺️ **Fleet-Map** | Read-only overlay matching cloud devices to their local twins by MAC. | 🧪 beta |
| 🔁 **Replace device** | Transplant a dead Shelly's HA identity onto a new unit of the same model. | 🧪 beta |

It runs **alongside** Shelly Cloud and the Shelly app — it does not take over or
lock out other clients — and **does not require** Home Assistant to be exposed to
the public internet.

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
  supported now;** native local Shelly support and an **upstream Home Assistant
  Core contribution** (modelled on the ESPHome replacement flow) are planned next.
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
