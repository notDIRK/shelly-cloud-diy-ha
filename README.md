# Shelly Cloud DIY — Home Assistant integration

![Shelly Cloud DIY](images/icon.png)

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/notDIRK/shelly-cloud-diy-ha)](https://github.com/notDIRK/shelly-cloud-diy-ha/releases)

> 🇩🇪 **Deutsch:** Eine deutschsprachige Version dieses Dokuments findest du in [`README.de.md`](README.de.md).

> ℹ️ **Release status:** `v0.4.x` is the current line, built on the **Cloud Control API** (self-service `auth_key`), and is **available in the [HACS default store](https://hacs.xyz)** — just search for *Shelly Cloud DIY*, no custom-repository URL needed. The `v0.2.x` tags are the legacy **Integrator API** implementation inherited from the engesin upstream and are kept for traceability only. Roadmap: [`docs/ROADMAP.md`](docs/ROADMAP.md) — Milestone 2 adds OAuth + WebSocket realtime.

---

## Why this fork exists — and the gap it closes

Shelly Cloud exposes **two** cloud APIs:

1. The **Integrator API** (`ITG_OSS` tag), which the popular community integration [`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha) uses. Shelly's own docs state: *"Licenses for personal use are not provided."* ([source](https://shelly-api-docs.shelly.cloud/integrator-api/)). Private users typically get stuck at the "email Shelly and wait for approval" step — see upstream [issue #1](https://github.com/engesin/shelly-integrator-ha/issues/1).
2. The **Cloud Control API**, which is **self-service**: the user generates an `auth_key` directly in the Shelly App under *User settings → Authorization cloud key*. No approval process, no waiting, available to any Shelly Cloud account holder.

The Cloud Control API **can see devices shared into the user's account by other Shelly users** (verified empirically against a real ECOWITT WS90 weather station shared from another account). The Integrator API's consent flow cannot do that because consent is granted per-owner — a device you were only *shared* doesn't belong to you and was never granted by the owner to any integrator. The official Home Assistant Shelly integration, meanwhile, is LAN-only and doesn't reach remote or shared devices at all.

### Comparison with existing projects

| Project | Auth | Realtime | Shared devices | Maintained |
|---|---|---|---|---|
| **`shelly-cloud-diy-ha`** *(this repo)* | `auth_key` (M1) / OAuth (M2) | HTTP poll 5 s (M1) → WebSocket push (M2) | ✅ | 🔄 active |
| [`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha) | Integrator API token *(gated — no private-use licences)* | WebSocket push | ❌ | ✅ |
| [HA Core — official Shelly integration](https://www.home-assistant.io/integrations/shelly/) | Local LAN / mDNS | LAN push | ❌ *(remote / shared devices unreachable over LAN)* | ✅ |
| [`StyraHem/ShellyForHASS`](https://github.com/StyraHem/ShellyForHASS) | Local LAN | LAN push | ❌ | ❌ **discontinued** per its README |
| [`vincenzosuraci/hassio_shelly_cloud`](https://github.com/vincenzosuraci/hassio_shelly_cloud) | Username/password *(reverse-engineered)* | HTTP polling | ? | ❌ last push 2019 |
| [HA YAML Blueprint (2025)](https://community.home-assistant.io/t/controlling-shelly-cloud-devices-in-home-assistant/928462) | `auth_key` | ❌ command-only, **no state read** | ? | ✅ |
| [`corenting/poc_shelly_cloud_control_api_ws`](https://github.com/corenting/poc_shelly_cloud_control_api_ws) | OAuth | WebSocket | ? | POC, not an integration |

No other maintained Home Assistant integration currently combines **Cloud Control API access**, **state reading**, **shared-device support**, and **Gen1 / Gen2 / BLE-gateway coverage** in one package. That is the gap this fork was made to close.

---

## What the integration does

A Home Assistant custom integration that:

- Connects to the Shelly Cloud using the self-service Cloud Control API path.
- Reads device state for **every device visible to your Shelly Cloud account**, including devices that have been shared with you and including BLE-bridged devices reported through a Shelly BLU Gateway.
- Exposes those devices as Home Assistant entities — switches, lights, covers, sensors, binary sensors, buttons.
- Does not require Home Assistant to be exposed to the public internet (no inbound webhook in Milestone 1).
- Works alongside Shelly Cloud and the Shelly App — it does not take over or lock out other clients.

---

## Requirements

- A Shelly Cloud account with at least one device paired to it.
- Home Assistant **2024.1** or newer.
- Outbound HTTPS reachability from Home Assistant to `*.shelly.cloud` (standard).
- No inbound internet reachability needed on the Home Assistant side (Milestone 1).

---

## Getting your credentials

The Cloud Control API is self-service. You do not need to contact Shelly, file a form, or wait for approval.

1. Open the **Shelly App**.
2. Go to **User settings → Authorization cloud key**.
3. Tap **GET KEY**.
4. You receive two values: an **`auth_key`** (a long opaque string) and a **server URI** (e.g. `shelly-42-eu.shelly.cloud`).
5. Both values are pasted into the Home Assistant config flow during setup.

> 🔐 **Security** — The `auth_key` grants control of every device visible to your Shelly Cloud account (including devices shared with you). Treat it like a password. To rotate: change your Shelly account password in the App — the old key invalidates server-side and a new one is generated.

---

## Rate limits and latency (open communication)

Shelly documents a rate limit of **1 API request per second per account** ([source](https://shelly-api-docs.shelly.cloud/cloud-control-api/)). The integration respects this budget by consolidating state fetches into a single `POST /device/all_status` call per poll cycle — one request returns the full state of every device visible to the account.

| | Milestone 1 (current scope) | Milestone 2 (future) |
|---|---|---|
| Transport | HTTP polling | WebSocket push |
| Default state-update latency (p50 / p99) | ~2.5 s / ~5 s | < 100 ms / < 500 ms |
| Outbound traffic (≈ 50-device account) | ≈ 12 KB/s avg at 5 s poll interval | 0 bytes steady |
| Commands (switch, dim, cover) | immediate HTTP POST, independent of poll timing | via WebSocket |
| Credentials required | `auth_key` + server URI | Shelly email + password (OAuth2 with `client_id=shelly-diy`) |

The 5-second poll default is chosen to stay well under the 1 req/s budget while leaving command headroom. Sensor values (temperature, energy, weather data) feel live; switch feedback in the UI feels gentle — Milestone 2's WebSocket push closes that gap. Users can tune the poll interval between 3 s (24 KB/s at 58 devices) and 60 s via the options flow.

Shelly also notes that the HTTP endpoints are *intentionally only lightly documented* and that parameter formats may change. The integration pins to the v1 endpoint shape and will react to changes if and when they occur — but this is a real long-term risk that you should be aware of.

---

## Installation (HACS)

The integration is in the **HACS default store**, so no custom-repository URL is needed.

1. Open **HACS** in Home Assistant.
2. Search for **Shelly Cloud DIY**.
3. Click **Download** and pick the latest version.
4. Restart Home Assistant.
5. Continue with *Setup* below.

### Upgrading from the custom repository

If you previously added this integration as a HACS *custom repository* (under the old
`notDIRK/shelly-integrator-ha` URL), moving to the default-store entry is safe — the integration
**domain stays `shelly_cloud_diy`**, so your config entry, entities (and therefore your dashboards)
are preserved. Notes from a real migration:

- **HACS removes the redundant custom-repository entry automatically** once the repo is served from
  the default store. If your custom entry "disappears on its own", that is expected — not a bug.
- When HACS asks during removal whether to **also remove the configuration**, **decline it**. That
  option deletes the Home Assistant config entry (devices, area assignments, entity IDs). Remove
  only the downloaded code; keep the config entry.
- **Order matters:** re-download from the default store **first**, then restart Home Assistant.
  Removing the files leaves the integration running in memory (it looks fine), so restarting in that
  state briefly marks all entities `unavailable`.
- The harmless log line *"custom integration shelly_cloud_diy which has not been tested by Home
  Assistant"* is normal for any `custom_components/` integration and is not an error.

---

## Setup

1. Home Assistant → **Settings → Devices & Services → Add Integration → "Shelly Cloud DIY"**.
2. Paste your `auth_key`.
3. Paste your `server URI` (e.g. `shelly-42-eu.shelly.cloud`).
4. Click **Submit**. Devices are fetched immediately and appear as entities.

---

## Roadmap summary

Full plan with per-milestone scope, non-goals, and limitations: [`docs/ROADMAP.md`](docs/ROADMAP.md) · German: [`docs/ROADMAP.de.md`](docs/ROADMAP.de.md).

- ✅ **M0 Foundation** — fork, security hardening, pivot research and verification, repo rename, CLOUD DIY branding
- ✅ **M1 Cloud Control API + `auth_key` + HTTP polling** — shipped (`v0.4.x`)
- ⏳ **M2 OAuth + WebSocket realtime** — push-based sub-second updates
- ✅ **M3 HACS default-store submission** — merged ([hacs/default#7101](https://github.com/hacs/default/pull/7101)); the integration ships its own `brand/` icons (HA 2026.3+), since `home-assistant/brands` no longer accepts custom-integration icons
- 💡 **M4 HA Core quality-scale polish** — diagnostics, repairs, test coverage

---

## License

MIT — see [`LICENSE`](LICENSE).

---

## Fork lineage

Forked from [`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha) (Integrator API implementation). The fork relationship is retained for git-history traceability only; the project has pivoted to a different API, and no upstream merges are expected.
