# Stufe 1 — Mockup: So erlebst du es in HA + Shelly Cloud

> Erstellt 2026-06-23 via Workflow (Entwurf → UX/Realismus-Kritiker → finale Fassung). Nur HA-Oberflächen, die eine Custom-Integration real erzeugen kann.

All facts verified. Key correction confirmed for M1: entity_id is `switch.treppe_og_5432044e9768_switch` (channel 0 → `_attr_name = "Switch"`, slugified after the device name; no `_0` suffix on channel 0). Here is the final mockup.

---

# Stage 1 "Fleet-Map" — final HA UX mockup (Dirk's POV)

Read-only cloud⇄local overlay. Three surfaces a custom integration can render today: a **persistent-notification report** (the shipping first cut) + an **informational Repairs entry** + **diagnostics JSON** as the machine-readable backing store. One optional, off-by-default cosmetic write (native rename) is gated behind its own flag.

All values use the verified conventions:
- cloud `device_id` = **lowercase MAC** (`5432044e9768`)
- native unique_id = `{MAC-UPPER}-{key}`; native main device keyed by `CONNECTION_NETWORK_MAC`
- our device name = `"{name} ({id})"` (base.py:113); `_attr_has_entity_name = True`
- our channel-0 switch `_attr_name = "Switch"` (switch.py:106) ⇒ rendered **`switch.treppe_og_5432044e9768_switch`** (no `_0` on channel 0; channel 1 → `..._switch_2`). unique_id stays `{device_id}_switch_{channel}`.

> Build note (B1): Stage 1 must add `custom_components/shelly_cloud_diy/diagnostics.py` exporting `async_get_config_entry_diagnostics(hass, entry)`. Diagnostics is auto-discovered from that module — **no `manifest.json` change**.

---

## 0. Where Stage 1 lives in HA — entry points

```
Settings ▸ Devices & Services ▸ Shelly Cloud DIY (overlay)
  │
  ├─ [Configure]                         (existing options flow — unchanged)
  ├─ ⋮ ▸ Download diagnostics            ← Fleet-Map table (machine-readable, redacted)
  │                                        NEW: backed by diagnostics.py (B1)
  └─ Developer Tools ▸ Actions
        shelly_cloud_diy.fleet_map       ← runs the scan, writes the report below
```

The scan is **always read-only**. There is no `dry_run` toggle (L1): the only write Stage 1 can perform is the cosmetic native rename, gated behind its own explicit `apply_native_name_suggestions` flag (default off). One flag, no redundant booleans.

```
Developer Tools ▸ Actions ▸ shelly_cloud_diy.fleet_map
┌────────────────────────────────────────────────────────────┐
│  Apply native name suggestions            [   ]  ← default off│
│                                          [ Perform action ]   │
└────────────────────────────────────────────────────────────┘
```

---

## 1. Fleet-Map overview — cloud ⇄ local matching

The action writes a `persistent_notification` (same mechanism `replace_device` already uses, replace_device.py), body in the fenced `plan_text` style so it renders monospaced and aligned. Online state carries **both** a glyph and a text token (`on`/`off`), since a notification has no color or tooltip (L3).

```
┌─ Notifications ─────────────────────────────────────────────────────────────┐
│  🔔  Shelly Cloud DIY — Fleet-Map                                  [ Dismiss ]│
│ ─────────────────────────────────────────────────────────────────────────────│
│  Scanned 61 cloud devices · matched 54 to your local Shelly integration       │
│  Generated 2026-06-23 14:07 (Europe/Berlin) · read-only                       │
│                                                                               │
│  ```                                                                          │
│  LEGEND  ⇄ both paths   ☁ cloud-only   ⌂ local-only                          │
│          ● on / ○ off  (filled = reachable)                                   │
│                                                                               │
│  ⇄  BOTH PATHS (54) — same hardware seen locally AND in the cloud            │
│  ────────────────────────────────────────────────────────────────────────  │
│   name                     cloud id          local     cloud    control      │
│   Treppe OG             5432044e9768       ● on      ● on      LOCAL  ✓      │
│   Küche Decke           80157669366571     ● on      ● on      LOCAL  ✓      │
│   Wohnzimmer Rollo      a8032ab1c4d2       ● on      ○ off     LOCAL  ✓      │
│   Büro Steckdose        c45bbe7788aa       ○ off     ● on      LOCAL  ✓      │
│   ... (50 more)                                                              │
│                                                                              │
│   ⚠ Each of these is ONE physical Shelly shown as TWO HA device cards:       │
│     • the LOCAL card (native Shelly integration, keyed by MAC) — controls    │
│       it sub-second and keeps working with NO internet. DO NOT DELETE IT.    │
│     • the CLOUD overlay card (this integration) — remote VISIBILITY only.    │
│     The cloud overlay card always carries the "(id)" suffix in its name —    │
│     that is how you tell it apart from the native card, and how to pick the  │
│     right one when HA autocompletes both in an automation editor.            │
│     Example pair:  Treppe OG                                                  │
│       LOCAL  device  5432044E9768  →  switch.treppe_og                       │
│       CLOUD  device  5432044e9768  →  switch.treppe_og_5432044e9768_switch   │
│                                                                              │
│  ☁  CLOUD-ONLY (5) — no local twin on your LAN                              │
│  ────────────────────────────────────────────────────────────────────────  │
│   Wetterstation WS90    d8bfc019aa55       —        ● on     SENSOR  ok      │
│      (shared from another Shelly account · gen=GBLE · sensor-only)          │
│   Gartenhaus Relais     e1aa20ff7733       —        ● on     CLOUD ⚠        │
│      (controllable, reachable ONLY via cloud — see Resilience below)        │
│   ... (3 more)                                                              │
│                                                                              │
│  ⌂  LOCAL-ONLY (7) — on your LAN but NOT in this cloud account              │
│  ────────────────────────────────────────────────────────────────────────  │
│   Garage Tor            34987a55c1e0       ● on     —        LOCAL  ✓       │
│   (info only — nothing for this integration to do)                          │
│  ```                                                                         │
│                                                                              │
│  ▸ Full machine-readable table: Settings ▸ … ▸ Download diagnostics          │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Under the hood.** Join `build_fleet_map`: keys come from `coordinator.devices` (lowercase ids, present after first poll — coordinator.py:219), matched against a one-pass index of native devices via `dr.format_mac(...)` normalisation of each native main device's `CONNECTION_NETWORK_MAC`. Match rule `cloud_id.upper() == native_mac.upper()` — names are *labels only* (`coordinator.device_names`); the match never reads them. `local ●/○` = native main device reachability; `cloud ●/○` = `coordinator.devices[id]["online"]` (coordinator.py:192–196). `control` is derived as in §3. The "two cards" entity_ids are the **real rendered** ids: device-name slug + channel-0 `Switch` ⇒ `switch.treppe_og_5432044e9768_switch` (M1, verified against switch.py:105–106 + base.py:113).

A persistent counterpart lands in **Repairs**, explicitly timestamped so a stale count can't read as live (B2):

```
┌─ Settings ▸ System ▸ Repairs ──────────────────────────────────────────────┐
│  ℹ  Shelly Cloud DIY: Fleet-Map (as of 2026-06-23 14:07)        [ Learn more ]│
│     54 of 61 cloud devices matched · 1 control-path risk found               │
│     This is a snapshot. Re-run the fleet_map action to refresh.               │
└────────────────────────────────────────────────────────────────────────────┘
```

(Informational `issue_registry` entry, `is_fixable=False`, dismissible. The card text and Learn-more body both state "snapshot — re-run to refresh" so the count is never mistaken for live. The actionable/fixable repair issue stays deferred to Stage 1.5 until the Category-2 classifier is false-positive-proven on the live 61-device account — R2.)

---

## 2. Name-Sync — Dirk renames once in the Shelly app, HA follows

Strictly **pull** (Shelly app → HA, names only). Two distinct experiences.

### 2a. OUR overlay device — automatic (already coordinator behaviour)

Dirk renames `Flur` → `Flur Erdgeschoss` in the **Shelly app**.

```
BEFORE (Settings ▸ Devices ▸ Shelly Cloud DIY)
┌───────────────────────────────────────────────┐
│  Flur (a8032ab1c4d2)                  [Shelly] │   ← name (id), base.py:113
│  Shelly Plus 1PM                               │
│  Entity:  switch.flur_a8032ab1c4d2_switch      │
└───────────────────────────────────────────────┘

        … next online poll resolves the v2 name (≤ poll interval + name-gap) …

AFTER  (no action from Dirk; entity_id unchanged)
┌──────────────────────────────────────────────────────────┐
│  Flur Erdgeschoss (a8032ab1c4d2)              [Shelly]     │
│  Shelly Plus 1PM                                          │
│  Entity:  switch.flur_a8032ab1c4d2_switch  (unchanged)    │
└──────────────────────────────────────────────────────────┘
```

**Under the hood.** Unchanged from today. The single load-bearing call is `dr.async_update_device(device_entry.id, name=f"{resolved} ({did})")` (coordinator.py:290) — HA ignores `DeviceInfo.name` after first registration (the comment at coordinator.py:266–271 says exactly this), so the explicit registry update is what makes the rename appear. `async_update_listeners()` (coordinator.py:299) only re-renders entity **state**, NOT the device name (B3). Only `name` is written, never `name_by_user`, so a manual HA rename always wins (coordinator.py:273–275). The `entity_id` never moves because it derives from unique_id, not the display name. Stage 1 adds nothing here; `fleet_map` only *reports* it.

### 2b. NATIVE device — advisory only (no apply path that self-defeats)

The native Shelly integration rewrites a device's technical `name` from the device's own config on **every** entry setup. So writing `name` on a native card is guaranteed to be clobbered on the next reload/restart — making an "apply `name`" feature visibly fail for most native devices (M4). Stage 1 therefore **does not write `name` to native cards**. Two honest options, and we take the conservative one:

- **Cut 1 (shipping): advisory-only.** The report *suggests*; Dirk applies the rename himself in the HA UI (which writes `name_by_user` — the only durable field).
- **Optional flag (`apply_native_name_suggestions: true`):** if enabled, the action writes **`name_by_user`** (not `name`) — justified because it's an explicit, user-initiated, off-by-default action, and it's the only write that survives a native reload. The report says this loudly.

```
In the Fleet-Map report:
  ```
  NATIVE NAME SUGGESTIONS (advisory — not applied)
  ──────────────────────────────────────────────────────────────────────
   local card        current HA name       cloud alias        suggestion
   Treppe OG         "Shelly Plus 2PM"     "Treppe OG"        rename
   Küche Decke       "shellyplus1-xxxx"    "Küche Decke"      rename
  (2 native devices have a model-default name but a friendly cloud alias.)

  RECOMMENDED: rename these on the LOCAL card yourself in
     Settings ▸ Devices ▸ <device> ▸ ✎  — that sets a user override
     that survives the native integration reloading.

  OR re-run with  apply_native_name_suggestions: true  to have this
     integration set the user-override (name_by_user) for you. This is the
     only write that sticks; it does NOT touch the technical name and is
     fully reversible (clear the name in HA to revert).
  Skipped automatically: any native device you already renamed yourself.
  ```
```

```
AFTER apply (native card; apply_native_name_suggestions:true)
┌──────────────────────────────────────────┐
│  Treppe OG                     [Shelly]   │  ← was "Shelly Plus 2PM"
│  (native integration · LOCAL control)     │     name_by_user set (survives
│  switch.treppe_og  (unchanged)            │     native reload; reversible)
└──────────────────────────────────────────┘
```

**Under the hood.** `suggest_native_names` emits a suggestion only where the matched native `DeviceEntry.name_by_user is None and name != cloud_name`. Apply path (off by default) calls `dr.async_update_device(native.id, name_by_user=...)` — exclusively from the manual service flag (L1/R1). The `name_by_user is None` precondition is the invariant that must never regress (SC2): we never overwrite a name the user already chose.

---

## 3. Resilience-Check — is the cloud ever in a CONTROL path?

Same report, dedicated section. Three states, not two — because the operator's mixed fleet has the dormant-local-twin case the binary classifier misses (H3).

```
  ```
  RESILIENCE CHECK — is the cloud ever in a CONTROL path?
  ═══════════════════════════════════════════════════════════════════════

  ⚠ CATEGORY 1 — cloud-only control (1 device)
  ────────────────────────────────────────────────────────────────────────
   Gartenhaus Relais   (cloud id e1aa20ff7733 · gen=G2)
     CONTROLLABLE but has NO local twin on your LAN.
     If the internet or Shelly Cloud is down, you CANNOT switch it from HA.
     • This is a LAN-capable Shelly (Gen2) — if it is YOUR device on YOUR
       LAN, add it to the native Shelly integration so control stays local.
       The cloud should be a backup view, never the only way to switch it.
     • If it is shared from another account you can't reach locally —
       no action needed; cloud is expected to be the only path.

  ⚠ CATEGORY 1b — local twin present, but no LOCAL control entity (1 device)
  ────────────────────────────────────────────────────────────────────────
   Keller Pumpe   (cloud id b4e62d9911ff)
     A native device with this MAC exists, but it exposes no enabled
     switch/light/cover entity (the control entity may be DISABLED, or the
     native device is input/sensor-only). Cloud control is currently the
     only working path from HA. → Enable the native control entity, or add
     the device to the native integration.

  ✓ CATEGORY 2 candidates — cloud control used despite a working local twin
                            (best-effort scan — see caveat)
  ────────────────────────────────────────────────────────────────────────
   Treppe OG  (5432044e9768) — a working LOCAL twin exists, but HA logic
     references the CLOUD side:
        automation.flur_abends_an
          via entity:   switch.treppe_og_5432044e9768_switch  (CLOUD overlay)
          local instead: switch.treppe_og                     (native, sub-second)
        script.gute_nacht
          via device_id: <cloud HA device of Treppe OG>        (device action)
          → re-target to the native device / native entity.
     → Repoint these to the LOCAL side. They then keep working with no
       internet and switch instantly.

  CAVEAT — this scan checks: entity_id references AND device_id-targeted
  actions/triggers pointing at OUR cloud HA device. It does NOT catch:
  area_id / label_id targeting, Jinja templates, blueprints, or references
  inside add-ons / Node-RED. Absence here is NOT proof of safety — manually
  confirm that automations on dual-path devices use the LOCAL (native) side.
  ```
```

If the automation/script walk can't run, Category 2 degrades honestly:

```
  ✓ CATEGORY 2 — cloud control despite local twin
  ────────────────────────────────────────────────────────────────────────
   Automation/script scan unavailable in this HA build — section omitted.
   Device-level twin status (Categories 1 / 1b) is still authoritative.
   Manually check that automations on dual-path devices reference the
   LOCAL (native) entity or device.
```

**Under the hood.** `scan_resilience` over the FleetMap:
- `has_cloud_control` = our entities (this entry) filtered on `CONTROL_DOMAINS = (switch, light, cover)`.
- `has_local_control` = `er.async_entries_for_device(native_id, include_disabled_entities=True)` filtered on the same `CONTROL_DOMAINS` (H3 — disabled control entities counted as "present but dormant", not "absent").
- **Category 1** = `has_cloud_control and no matched native device` (authoritative, tested — SC3).
- **Category 1b** = matched native device exists but has **no** control entity (disabled or input/sensor-only) — the dormant-twin case folded out of Category 1 so it doesn't misfire (H3).
- **Category 2** = matched device with a working local twin where HA logic references the cloud side, found by a `try/except`-wrapped walk that checks **both** (a) cloud entity_id substrings AND (b) `device_id == <our cloud HA device id>` in automation/script triggers & actions (H1 — device-id targeting is the most common HA wiring and was the single highest-value gap). Never claimed exhaustive; omittable (T1).

**Category 1 advice is gated on `device_gen(status)` (const.py:86, verified).** For `gen == "GBLE"` (BLE / gateway-bridged) or shared devices, the "add it to the native integration" line is suppressed — those have no LAN-RPC path by design (H2). The shared **WS90** (`gen=GBLE`, sensor-only, no control domain) is correctly absent from every category — the operator's USP device is never falsely alarmed (verified against `device_gen` + `CONTROL_DOMAINS`). No control call is made anywhere; the only command path is `coordinator.send_command` (coordinator.py:303), untouched — SC4 holds by architecture.

---

## 4. Backing diagnostics (machine-readable, MAC-fingerprinted not prefix-redacted)

`Download diagnostics` → `diagnostics.py:async_get_config_entry_diagnostics` (NEW — B1) → `to_diagnostics(...)`. MACs are not exposed, but a stable non-reversible fingerprint appears on **both** sides so a reader can confirm a match is correct without leaking the MAC (M3 — partial-prefix redaction both leaked the prefix AND broke correlation; fixed):

```jsonc
{
  "fleet_map": {
    "generated": "2026-06-23T14:07:02+02:00",
    "cloud_device_count": 61,
    "matched_count": 54,
    "scope": "enabled_and_polled",   // resilience assessed only for enabled devices (L2)
    "entries": [
      {
        "mac_fp": "9f3a1c0b",                 // sha1(mac)[:8] — same on both sides if matched
        "cloud_name": "Treppe OG",
        "gen": "G2",
        "native_ha_device_id": "a1b2c3...",   // HA device id, not MAC
        "our_ha_device_id": "d4e5f6...",
        "materialized": true,                 // entity opt-in state (L2)
        "has_cloud_control": true,
        "has_local_control": true,
        "resilience": null
      },
      {
        "mac_fp": "71e0d8aa",
        "cloud_name": "Gartenhaus Relais",
        "gen": "G2",
        "native_ha_device_id": null,
        "our_ha_device_id": "99aa...",
        "materialized": true,
        "has_cloud_control": true,
        "has_local_control": false,
        "resilience": "cloud_only_control"     // Category 1
      },
      {
        "mac_fp": "c1f4...",
        "cloud_name": "Keller Pumpe",
        "native_ha_device_id": "ee77...",
        "our_ha_device_id": "ab12...",
        "has_local_control": false,            // matched, but no enabled control entity
        "resilience": "local_twin_no_control"  // Category 1b (H3)
      }
    ],
    "name_suggestions": [
      { "native_ha_device_id": "a1b2c3...", "current": "Shelly Plus 2PM",
        "suggested": "Treppe OG", "name_by_user_set": false }
    ],
    "automation_scan": "best_effort_ok",       // or "unavailable"
    "automation_scan_checks": ["entity_id", "device_id"]   // NOT area/label/template (H1)
  }
}
```

**Under the hood.** Same `FleetMap` / `ResilienceReport` dataclasses feed both the report and this dict. `mac_fp = sha1(mac)[:8]` on each side preserves auditability without exposing the MAC (M3); HA-internal device ids are not PII. Resilience scope is **enabled/materialized** devices — un-enabled cloud devices are polled but their control-path isn't assessed (`er.async_entries_for_device` finds nothing for them), so they're labelled `materialized:false` rather than silently passing (L2). Scoped to the calling entry's `coordinator.devices` (E2). Zero standing entities — lowest review surface (R2).

---

## B. Relationship to the Shelly Cloud app

```
   SHELLY APP (phone)                 HOME ASSISTANT
   ─────────────────                  ──────────────
   Rename "Flur"                      v2 alias poll (lazy, online-only)
     → "Flur Erdgeschoss"   ───────▶  ─ OUR card:    auto-renamed (name only)
                                       ─ NATIVE card: suggested; you apply it
                                         (writes name_by_user, the durable field)

   Toggle a relay        ✗ NOT a path HA depends on for control
                            (HA controls via the NATIVE LAN integration)

   Shares a WS90 from           ───▶  appears as CLOUD-ONLY sensor in Fleet-Map
   someone else's account             (no local twin expected; gen=GBLE; not alarmed)

   Cloud / internet DOWN        ───▶  OUR cloud entities → unavailable
                                      NATIVE entities, commands, automations:
                                      UNAFFECTED (never traverse our code)
                                      Fleet-Map still matches from local registry
```

Direction is strictly **pull** (Shelly app → HA, names only). HA never writes back to the cloud or the device. The cloud app is the *source of friendly names* and the *origin of shared/remote devices*; it is never a control path HA leans on. The resilience check exists precisely to make any accidental drift from that rule visible — including the dormant-twin and device-id-targeting cases that look safe but aren't.

---

### Design notes for the build

- **New file required:** `custom_components/shelly_cloud_diy/diagnostics.py` (B1). No `manifest.json` change.
- **First-cut surfaces:** persistent-notification report + one **timestamped** informational Repairs entry (B2) + diagnostics. No summary sensor, no *fixable* repair issue yet (deferred to Stage 1.5 until the Category-2 classifier is validated false-positive-free on the live 61-device account — R2).
- **The only writes Stage 1 performs:** (1) our own device `name` via the existing coordinator path (unchanged, never `name_by_user`); (2) optional, off-by-default native **`name_by_user`** — the durable field, not the clobber-prone `name` (M4).
- **Single highest-priority correctness items, applied:** Category-2 scan now includes `device_id`-targeted automations/scripts against OUR cloud HA device id (H1); the Repairs card is timestamped and labelled a snapshot (B2).
- **Resilience classifier counts disabled control entities** via `include_disabled_entities=True`, and splits the dormant-twin case into Category 1b (H3); Category 1 advice is gated on `device_gen()` so BLU/GBLE/shared devices aren't told to "add to native" (H2).
- **i18n:** every user-visible string (action name/description/field, report headers, Repairs `translation_key`) needs `strings.json` + `translations/{en,de}.json` — mirroring the `replace_device` block already in `services.yaml`.

**Invariants that must never regress:** pull-only / names-only for OUR devices (SC1); never overwrite `name_by_user is not None` (SC2); Category-1 device-level classification is authoritative and tested (SC3); no control call in any Stage-1 path — single command path at coordinator.py:303 untouched (SC4).