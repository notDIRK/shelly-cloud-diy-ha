# Shelly Integrator for Home Assistant (notDIRK fork)

![Shelly Integrator — CLOUD DIY (notDIRK fork)](images/icon.png)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/notDIRK/shelly-integrator-ha)](https://github.com/notDIRK/shelly-integrator-ha/releases)

Custom Home Assistant integration for Shelly devices using the **Cloud Integrator API**.

> This is a personal fork of [engesin/shelly-integrator-ha](https://github.com/engesin/shelly-integrator-ha) maintained by [@notDIRK](https://github.com/notDIRK). Upstream stays the source of truth; this fork adds fork-specific fixes and tracks releases via `vX.Y.Z-notDIRK` tags.

## Features

- Real-time device status via WebSocket
- Switch control for Shelly relays
- Light control with brightness
- Power and energy monitoring sensors
- Cloud-to-cloud integration (no local network access needed)
- Automatic device discovery when users grant access

## Requirements

- Shelly Integrator API token (see [Getting an API Token](#getting-an-api-token--api-token-beziehen) — this is gated by Shelly and not self-service)
- Devices must be connected to Shelly Cloud
- Home Assistant must be reachable from the public internet (Shelly Cloud needs to POST to the consent webhook). That usually means a port-forward or a reverse proxy on `https://your-ha.example.com`.

## Getting an API Token / API-Token beziehen

> ⚠️ Upfront reality check: the Shelly Cloud **Integrator API** used by this integration is not a self-service API. The [official Shelly Integrator API docs](https://shelly-api-docs.shelly.cloud/integrator-api/) state literally: *"Licenses for personal use are not provided."* There is **no button on `my.shelly.cloud`** where you can generate this token yourself — you have to request it from Shelly directly, and approval for hobby use is not guaranteed. See upstream issue [engesin/shelly-integrator-ha#1](https://github.com/engesin/shelly-integrator-ha/issues/1) for community context.

### English

1. Contact Shelly / Allterco Robotics and request a token for the **`ITG_OSS`** (open-source) integrator tag:
   - E-mail **`support@allterco.com`**, **or**
   - Fill out the partner form linked from the [official Integrator API docs](https://shelly-api-docs.shelly.cloud/integrator-api/).
2. State clearly that you want to use the open-source `engesin/shelly-integrator-ha` integration (tag `ITG_OSS`) for Home Assistant and that you are a private user. Some users have been approved, others have been pointed to the Cloud Control API instead — your mileage may vary.
3. If Shelly approves, you will receive the pair `(tag, token)`. This integration already hardcodes `tag = ITG_OSS`; you only need to paste the **`token`** (a long opaque string) into the HA config flow.
4. Fallback if Shelly declines: Shelly recommends the [Cloud Control API](https://shelly-api-docs.shelly.cloud/cloud-control-api/) (OAuth, per-user) for private use — **this integration does not speak that API**. You would then need a different community integration or build your own.

Once you have the token:

1. Home Assistant → **Settings → Devices & Services → Add Integration → "Shelly Integrator"**
2. Paste the token into the **API Token** field.
3. Continue with the consent step (see [Setup](#setup) below).

The token is stored in HA's config entry (plaintext at rest — standard HA practice). It grants broad control of every device your Shelly account shares with the integrator — treat it like a password, and never commit it to git.

### Deutsch

1. Shelly / Allterco Robotics direkt kontaktieren und einen Token für das **`ITG_OSS`**-Integrator-Tag (Open Source) anfragen:
   - E-Mail an **`support@allterco.com`**, **oder**
   - Partner-Formular aus den [offiziellen Integrator-API-Docs](https://shelly-api-docs.shelly.cloud/integrator-api/) ausfüllen.
2. In der Anfrage klar schreiben, dass du die Open-Source-Integration `engesin/shelly-integrator-ha` (Tag `ITG_OSS`) mit Home Assistant nutzen möchtest und Privatanwender bist. Einige User haben einen Token bekommen, andere wurden auf die Cloud Control API verwiesen — eine Zusage ist offiziell nicht garantiert (die Shelly-Docs sagen: *"Licenses for personal use are not provided."*).
3. Wenn Shelly zustimmt, bekommst du ein Paar `(tag, token)`. Diese Integration hat `tag = ITG_OSS` bereits fest verdrahtet; du brauchst nur den **`token`** (ein langer undurchsichtiger String), den du im HA-Konfigurations-Dialog einfügst.
4. Falls Shelly ablehnt: Shelly empfiehlt für Privatnutzung die [Cloud Control API](https://shelly-api-docs.shelly.cloud/cloud-control-api/) (OAuth, pro Nutzer) — **diese Integration spricht diese API nicht**. Du bräuchtest dann eine andere Community-Integration oder müsstest selbst eine bauen.

Sobald der Token da ist:

1. Home Assistant → **Einstellungen → Geräte & Dienste → Integration hinzufügen → "Shelly Integrator"**.
2. Token in das Feld **API Token** einfügen.
3. Weiter mit dem Consent-Schritt (siehe [Setup](#setup) unten).

Der Token landet im HA-Config-Entry (Klartext auf Disk — HA-Standard). Er gibt weitreichende Kontrolle über alle Geräte, die dein Shelly-Account mit dem Integrator teilt — behandle ihn wie ein Passwort und commite ihn niemals in git.

## Installation

### HACS (Custom Repository)

1. Open **HACS** in Home Assistant.
2. Click the three-dots menu (top-right) → **Custom repositories**.
3. Paste the repository URL: `https://github.com/notDIRK/shelly-integrator-ha`
4. Select category **Integration** and click **Add**.
5. Close the dialog, then find **Shelly Integrator** in the HACS integration list and click **Download**.
6. Pick the latest release (e.g. `v0.1.0-notDIRK`) and confirm.
7. Restart Home Assistant.
8. Continue with **Setup** below.

### Manual

1. Copy `custom_components/shelly_integrator` to your HA `config/custom_components/` directory.
2. Restart Home Assistant.

## Setup

### Step 1: Add the Integration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for "Shelly Integrator"
3. Enter your API Token

### Step 2: Grant Device Access

After setup, you'll see a **persistent notification** in Home Assistant with a link to grant device access:

1. Click the **"Grant Device Access"** link in the notification
2. Log into your Shelly Cloud account
3. Select the devices you want to share with Home Assistant
4. Click "Allow"

Your devices will automatically appear in Home Assistant!

> **Note:** You can use the link again anytime to add more devices.

## How It Works

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Home Assistant │     │  Shelly Cloud   │     │  Your Devices   │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         │  1. User grants       │                       │
         │     device access     │                       │
         │<──────────────────────│                       │
         │                       │                       │
         │  2. WebSocket         │                       │
         │     connection        │                       │
         │──────────────────────>│                       │
         │                       │                       │
         │  3. Real-time         │  Device status       │
         │     updates           │<──────────────────────│
         │<──────────────────────│                       │
         │                       │                       │
         │  4. Commands          │  Control commands    │
         │──────────────────────>│──────────────────────>│
         │                       │                       │
```

## Supported Devices

Any Shelly device connected to Shelly Cloud that supports:
- Relays (switches)
- Lights with brightness control
- Power/energy metering

## API Documentation

- [Shelly Integrator API](https://shelly-api-docs.shelly.cloud/integrator-api/)

## Maintaining this fork

This repo tracks `engesin/shelly-integrator-ha` as `upstream`. To pull in upstream changes and cut a new fork release:

```bash
# 1. Fetch upstream
git fetch upstream

# 2. Merge upstream main into local main (resolve conflicts if any)
git checkout main
git merge upstream/main

# 3. Bump version in custom_components/shelly_integrator/manifest.json
#    (e.g. "0.1.0" -> "0.2.0") and commit:
git add custom_components/shelly_integrator/manifest.json
git commit -m "chore(release): bump manifest version to 0.2.0"

# 4. Push main to fork origin
git push origin main

# 5. Tag and push release — HACS picks up the latest tag
git tag -a v0.2.0-notDIRK -m "Release 0.2.0-notDIRK"
git push origin v0.2.0-notDIRK

# 6. (Optional) Create a GitHub release from the tag
gh release create v0.2.0-notDIRK --title "v0.2.0-notDIRK" --notes "Synced with upstream + fork changes"
```

HACS installs the **latest release tag**, so every update needs a new tag/release. Keep the manifest `version` field in sync with the tag (without the `-notDIRK` suffix if you prefer strict SemVer, or with it — both work for HACS).

## License

MIT
