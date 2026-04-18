# Shelly Integrator for Home Assistant (notDIRK fork)

![Shelly Integrator (notDIRK fork)](images/icon-notdirk-v2.jpeg)

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

- Shelly Integrator API token
- Devices must be connected to Shelly Cloud
- Home Assistant must be accessible externally (for webhook callback)

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
