# Shelly Integrator HA - Project Onboarding

Copy this to `.cursor/rules/project.mdc` in your new repo.

---

```markdown
---
description: Shelly Integrator Home Assistant custom component project
alwaysApply: true
---

# Shelly Integrator HA

Custom Home Assistant integration using Shelly Cloud Integrator API.

## Credentials

- **TAG**: `ITG_OSS` (hardcoded in `const.py`)
- **TOKEN**: Stored securely - user enters during setup (never commit to git)

## API Endpoints

| Endpoint | URL |
|----------|-----|
| Get JWT | `POST https://api.shelly.cloud/integrator/get_access_token` |
| WebSocket | `wss://<HOST>:6113/shelly/wss/hk_sock?t=<JWT>` |
| User Consent | `https://my.shelly.cloud/integrator.html?itg=ITG_OSS&cb=<URL>` |

## Project Structure

```
shelly-integrator-ha/
├── hacs.json
├── custom_components/
│   └── shelly_integrator/
│       ├── __init__.py          # Integration setup, WebSocket client
│       ├── manifest.json        # HA integration manifest
│       ├── config_flow.py       # UI configuration flow
│       ├── const.py             # Constants (DOMAIN, etc.)
│       ├── coordinator.py       # DataUpdateCoordinator
│       ├── sensor.py            # Power/energy sensors
│       ├── switch.py            # Relay switches
│       ├── light.py             # Light entities
│       └── strings.json         # UI translations
└── README.md
```

## Tech Stack

- Python 3.11+
- Home Assistant Core APIs
- aiohttp (async HTTP/WebSocket)
- HACS compatible

## Key Implementation Details

### JWT Token Flow
1. POST to `/integrator/get_access_token` with `itg` and `token`
2. JWT valid for 24 hours
3. Refresh token before expiry

### WebSocket Events
- Device status updates (real-time)
- Online/offline notifications
- Settings changes

### WebSocket Commands
- Relay control: `{"id": N, "method": "relay", "params": {"id": 0, "on": true}}`
- Light control: `{"id": N, "method": "light", "params": {"id": 0, "on": true, "brightness": 100}}`

## HA Custom Component Conventions

- Use `async_setup_entry` for config entry setup
- Use `DataUpdateCoordinator` for data management
- Entity IDs: `shelly_integrator_<device_id>_<channel>`
- Device info via `DeviceInfo` class

## HACS Requirements

- `hacs.json` with `name`, `render_readme`, `homeassistant` version
- `manifest.json` with `domain`, `name`, `version`, `config_flow`
- README.md with installation instructions
```

---

## Quick Start Commands

```bash
# Create project
mkdir -p shelly-integrator-ha/custom_components/shelly_integrator
cd shelly-integrator-ha

# Initialize git
git init
git remote add origin https://github.com/engesin/shelly-integrator-ha.git

# Create cursor rules
mkdir -p .cursor/rules
# Copy the rule above to .cursor/rules/project.mdc
```

## Test JWT Token

```bash
curl -X POST 'https://api.shelly.cloud/integrator/get_access_token' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'itg=ITG_OSS' \
  -d 'token=<YOUR_TOKEN>'
```

## Reference Docs

- [Shelly Integrator API](https://shelly-api-docs.shelly.cloud/integrator-api/)
- [HA Custom Component](https://developers.home-assistant.io/docs/creating_component_index)
- [HACS Developer Docs](https://hacs.xyz/docs/developer/start)
