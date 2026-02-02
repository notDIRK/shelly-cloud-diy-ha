<p align="center">
  <img src="images/icon.webp" alt="Shelly Integrator" width="128">
</p>

# Shelly Integrator for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/engesin/shelly-integrator-ha)](https://github.com/engesin/shelly-integrator-ha/releases)

Custom Home Assistant integration for Shelly devices using the **Cloud Integrator API**.

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

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/engesin/shelly-integrator-ha` as **Integration**
4. Search for "Shelly Integrator" and install
5. Restart Home Assistant

### Manual

1. Copy `custom_components/shelly_integrator` to your HA config directory
2. Restart Home Assistant

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

## License

MIT
