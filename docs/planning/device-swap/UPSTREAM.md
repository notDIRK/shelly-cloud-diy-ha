# Upstream PR plan: device-replacement repair flow for HA Core Shelly integration

> **Status 2026-08-18.** This plan was carried out. The pull request is
> [home-assistant/core#174581](https://github.com/home-assistant/core/pull/174581),
> "Detect Shelly device replacements by name in the config flow" — open, with
> changes requested, and unmoved since 2026-08-03. What follows is the plan as
> written in June, kept for the reasoning; it is not a description of the PR
> that was actually submitted.

> Goal: add the ESPHome-style "device conflict / MAC change" repair flow to the
> **core** `homeassistant/components/shelly` integration, so that replacing a
> broken Shelly with a new unit (different MAC) preserves entity_ids, the HA
> device, history, and all automations/dashboards — for *every* HA user, not
> just ours. Based on the dissection of core PR #142507 (ESPHome).

## Why this is well-positioned (lead with this)

- Architecture discussion **#1088 ("Add support for device replacement")** was
  opened by **@thecode — a Shelly code-owner.** @frenck rejected a *generic*
  core-wide replace-device feature but the consensus **endorsed per-integration
  repair flows**, with @thecode explicitly citing **ESPHome PR #142507 as the
  pattern to copy** to other integrations.
- ESPHome shipped exactly this in **Core 2025.5** (PR #142507, bdraco).
- So a Shelly-specific repair flow is the *sanctioned* path, proposed by
  Shelly's own maintainer. This is not a feature out of nowhere.

## The user pain it fixes

Today the core Shelly integration **actively blocks** a hardware swap:
`aioshelly` raises `MacAddressMismatchError`, which the integration degrades
into an opaque `ConfigEntryNotReady` ("device_communication_error"). The
reconfigure flow aborts with `another_device`. Net result: users hit
**#125811 / #119933 / #131307** and have no remedy but manual `.storage`
registry surgery. Issue #125811 ("MacAddressMismatchError when replacing a
Shelly device") was closed *not planned*. This PR turns that dead-end into a
fixable repair.

## Step 1 — open an issue / architecture comment first (get buy-in before coding)

Post on architecture #1088 (or a new core issue tagged for Shelly), tagging
the code-owners **@bieniu @thecode @chemelli74 @bdraco**:

> Following the precedent set by ESPHome (#142507) and the conclusion of
> architecture discussion #1088 — where a generic core replace-device flow was
> declined in favour of per-integration repair flows — I'd like to add the same
> `device_conflict` repair to Shelly. Shelly already raises
> `MacAddressMismatchError` and currently degrades it to an opaque
> `ConfigEntryNotReady` ("device_communication_error"), leaving users (#125811,
> #119933, #131307) with no remedy but manual registry surgery. The repair would
> offer **Migrate** (transplant config-entry unique_id + device
> connections/identifiers + entity unique_ids to the new MAC, preserving
> history/automations) or **Manual** (rename/remove the duplicate). Mirrors the
> ESPHome implementation. OK to proceed?

Get a 👍 from a code-owner before writing the PR — high-traffic integration,
saves wasted work.

## Step 2 — implementation (mirror ESPHome PR #142507, adapt to Shelly)

ESPHome's transplant (`manager.py::async_replace_device`) does 4 things:
1. `hass.config_entries.async_update_entry(entry, unique_id=new_mac)`
2. `dr.async_update_device(device.id, new_connections={(CONNECTION_NETWORK_MAC, new_mac)})`
3. per entity: `er.async_update_entity(entity_id, new_unique_id=...)` — MAC-prefix substitution
4. update its own Store cache so the new MAC doesn't re-trigger the conflict

Shelly differences to handle (these are the review-critical bits):

- **Device identity:** Shelly keys the *main* device by **connection**
  `(CONNECTION_NETWORK_MAC, mac)` AND sub-devices (multi-channel: Pro 4PM →
  `{mac}-switch:0..3`; per-phase EM → `{mac}-{key}-a`) by **identifier**
  `(DOMAIN, f"{mac}-{key}")`. So unlike ESPHome you must rewrite **both**
  `new_connections` (main) **and** `new_identifiers` (sub-devices).
- **Entity unique_ids:** `f"{mac}-{key}"` (RPC) / `f"{mac}-{block.description}"`
  (Block), plus `-{attribute}` suffixes. Substitution: split off the leading
  `{mac}` token, guard with `startswith(old_mac)`. **Prefer the existing Shelly
  idiom `er.async_migrate_entries(hass, entry_id, fn)`** (already used in
  `__init__.py`) over ESPHome's manual loop — matches house style.
- **BLE/BLU sub-devices** use a *different* scheme keyed on the BLU device's BLE
  address (`format_ble_addr`), NOT the gateway MAC. A gateway swap must NOT
  touch them; migrate the bluetooth-derived MAC (`bluetooth_mac_from_primary_mac`)
  for the gateway itself though.
- **Detection point:** ESPHome detects in its live `_on_connect`. Shelly raises
  `MacAddressMismatchError` during setup *before a coordinator exists* — the
  message embeds both MACs but **don't string-parse it**. Detect at the same
  place ESPHome conceptually does: from the **config_flow zeroconf/DHCP
  discovery** step, where the discovered MAC is authoritative; raise the repair
  issue there. (Reuse the MAC-check hardening from PR #130833.)
- **False positives:** Pro 3EM reports different MAC on LAN vs WiFi (#132826).
  Gate the heuristic tightly: only when device *name/model* matches and the new
  MAC is a genuinely different unit (not the known alternate-interface MAC).

### Files to touch

| File | Change |
|---|---|
| `homeassistant/components/shelly/repairs.py` | NEW. `ShellyRepair(RepairsFlow)` base + `DeviceConflictRepair` with `async_step_init` (menu: `migrate` / `manual`), `async_step_migrate`, `async_step_manual`; `async_create_fix_flow()` routing on `issue_id.startswith("device_conflict")`. |
| `homeassistant/components/shelly/__init__.py` (or `coordinator.py`) | In the `except MacAddressMismatchError` blocks (`__init__.py:238` block / `:349` RPC), compute old (`entry.unique_id`) vs new MAC and `ir.async_create_issue(... is_fixable=True, translation_key="device_conflict", data={...})`. Add `async_replace_device()` (the 4-step transplant, Shelly-aware: connections + sub-device identifiers + entity unique_ids via `async_migrate_entries`). Clear issue on a subsequent matching connect. |
| `homeassistant/components/shelly/strings.json` | `issues.device_conflict` with `fix_flow` steps (`init` menu + `migrate` + `manual`), placeholders `{name} {model} {ip} {stored_mac} {mac}`. |
| `tests/components/shelly/test_repairs.py` | NEW/extend. Mirror ESPHome's three tests, for BOTH block (Gen1) and RPC (Gen2) fixtures: unknown-issue `ValueError`; migrate path (assert entry unique_id, device connection old-gone/new-present, sub-device identifiers + entity unique_ids rewritten); manual path (issue auto-clears). Add a multi-channel/sub-device fixture. |

### Code-owners to request review from
`@bieniu @thecode @chemelli74 @bdraco` (from `shelly/manifest.json`). bdraco
wrote the ESPHome original; thecode opened #1088 — strongest possible reviewers.

## Pre-empt the likely objections (address in the PR description)
1. "How do you know the new MAC?" → detect from zeroconf/DHCP discovery, not by
   parsing the aioshelly exception string.
2. "LAN vs WiFi dual-MAC false positives (#132826)" → tight heuristic
   (name/model match + genuinely different unit), reuse #130833 hardening.
3. "Sub-devices/BLU need more than a flat MAC loop" → migrate device
   *identifiers* + BLE-derived MAC + child devices; add a sub-device test.
4. "Match house style" → use `er.async_migrate_entries`, not a manual loop.

## Reference: our own Phase-1 implementation
Our cloud integration ships the same pattern as a service
(`shelly_cloud_diy.replace_device`, v0.5.0) — see
`custom_components/shelly_cloud_diy/services/replace_device.py`. Same 5 moves
(remove duplicates → re-point device identifier → rewrite entity unique_id
prefix → swap enabled list → reload), adapted to our cloud device-id scheme.
It's a working proof of the approach we can point to in the upstream discussion.
