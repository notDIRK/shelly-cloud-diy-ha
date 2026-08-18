# Shelly Cloud+Local Overlay — 3-Stufen-Plan (Multi-Agent, mit Kritikern)

> Erstellt 2026-06-23 via Workflow `shelly-3stage-plan` (12 Subagenten: pro Stufe Planer→Kritiker→Überarbeiter, + Mockup-Kette). Jede Stufe wurde adversariell gegen das **lokal-first**-Leitprinzip geprüft. Leitprinzip: die Cloud liegt NIE im Steuerweg — diese Integration ist ein Overlay.



---

# Stufe 1 — Fleet-Map: cloud<->local matching + name-sync (pull) + resilience-check

## Finaler (gehärteter) Plan

All critic claims are confirmed against the source: lazy/online-only name lookup (224-227), single-flight + swallowed errors (228-254), own-device name write with `name_by_user` preservation (276-296), single control path (303+), own-device identifier `(DOMAIN, device_id)` with no MAC connection (79), and the correct path `api/cloud_control.py`. I accept the critic's must-fixes 1-8. Here is the final plan.

---

# STAGE 1 — Fleet-Map: cloud↔local matching + name-sync (pull, OWN-only) + resilience-check

## 1. Goal & success criteria

**Goal.** Add a read-only *overlay* to `shelly_cloud_diy` that, for every device the cloud account sees, (a) determines whether the same physical Shelly is also present via the HA-Core native `shelly` integration (trivial MAC match via `dr.format_mac`), (b) surfaces the cloud-side alias **without mutating native rows** (pull-to-own-devices only, plus an advisory *suggestion* for native), and (c) flags any device whose *control* path runs over the cloud, distinguishing the unavoidable case from the operator-fixable case. The cloud is never in the control path; this layer reads registries + the already-fetched coordinator snapshot and writes, at most, the `name` of **our own** devices (which the coordinator already does).

**Success criteria (observable / testable):**

- **SC1 — Matching (name-independent).** A diagnostics dump and the service report expose, per cloud `device_id`, the matched native HA `device_id` (or `null`), derived purely from `dr.format_mac(cloud_id) == dr.format_mac(native_mac)`. The match set depends **only** on `coordinator.devices` keys (always present after the first successful refresh) and the local device registry — never on `coordinator.device_names`. The matched count is stable across reloads and does not change when cloud names arrive late.
- **SC2 — Name handling (own-only write; native = suggestion).** For **our own** devices, the report confirms the existing `f"{name} ({id})"` convention is honored and `name_by_user` is never touched (this is already coordinator behavior; Stage 1 does not duplicate or fight it). For **native** devices, Stage 1 **never writes** their `name`; it only *reports* a suggested alias the operator can apply manually. With the (default-off) manual native-apply action, a native device whose `name_by_user` is set is always skipped.
- **SC3 — Resilience-check (device-level authoritative; automation scan best-effort).** The report and diagnostics list, as a first-class authoritative category, every device whose control is cloud-backed with **no local twin** (`cloud_only_control`) and, separately, every device that **has** a local twin but where an automation/script references the cloud control entity (`cloud_control_despite_local_twin`). The device-level classification is authoritative and tested; the automation/script attribution is explicitly marked **best-effort** (wrapped in `try/except`, may be incomplete) and never claimed to be exhaustive.
- **SC4 — Local-first invariance (by construction).** Our code never calls a cloud control endpoint outside `coordinator.send_command` (coordinator.py:303), which only platform command handlers invoke. Stage 1 adds zero control calls. Killing the cloud leaves native entity states, native command latency, and automations on native entities entirely unaffected — this holds *by architecture*, not by a test artifact.
- **SC5 — Idempotence / safety.** The scan is read-only by default. The optional manual native-apply pass, run twice, produces no second-pass write. Diagnostics generation never writes anything.

## 2. User-facing behaviour

Cut to the **lowest-review-surface viable Stage 1** (critic R2): **diagnostics + one manual service with a notification report.** The standing summary sensor and persistent repair issues are **deferred** until the resilience classifier is proven free of false positives on the live account.

- A new **service** `shelly_cloud_diy.fleet_map` (manual, modeled on `replace_device`) that runs the scan on demand and writes a `persistent_notification` containing the full report: matched / cloud-only / local-only lists, each match shown as an explicit pair ("Treppe OG — local device X ⇄ cloud overlay device Y, same hardware, do **not** delete the local one"), the suggested native aliases (advisory text only, not applied), and the two resilience categories.
  - Schema: `dry_run` (default `true`), `apply_native_name_suggestions` (default `false`), `name_sync_targets` retained only as `native_main_only` (no `cloud_and_*` that touches our own — own devices are handled by the coordinator, not here).
  - With `apply_native_name_suggestions: true, dry_run: false` the service performs a **manual, reversible** write of native `name` (never `name_by_user`) for matched main devices only. This is the **only** path that ever writes a native row, it is operator-initiated, off by default, and **not** on any coordinator/poll callback.
- **Diagnostics** (`async_get_config_entry_diagnostics`): the full machine-readable Fleet-Map table, MAC-partial-redacted via `homeassistant.components.diagnostics.async_redact_data`. Zero standing entities, lowest review risk.
- **Deferred (documented as Stage 1.5):** the `sensor.shelly_cloud_diy_fleet_map` summary entity and the `cloud_control_despite_local_twin` repair issue. Specs kept in §3 so they can be added once the classifier is validated, but they do **not** ship in the first Stage 1 cut.

**Name direction.** Strictly **pull**, and for *our own* devices only as an automatic write (already done by the coordinator). We never write back to the cloud or the device. The native-facing direction is **report/suggest**, optionally apply on explicit manual request. Source is the v1 `/interface/device/list` alias via `ShellyCloudControl.get_device_names` (`api/cloud_control.py:227`).

## 3. Technical design

### NEW files

**`custom_components/shelly_cloud_diy/services/fleet_map.py`** — the engine. Mirrors `services/replace_device.py` (module-level handler bound to `hass` via `functools.partial`, registered in `__init__.py:_register_services`).

Public entry point:
```python
async def async_handle_fleet_map(hass: HomeAssistant, call: ServiceCall) -> None
```

**Single MAC normalization helper (critic T4):**
```python
from homeassistant.helpers import device_registry as dr

def _norm_mac(value: str) -> str:
    """Canonical comparison key for both cloud_id and native MAC."""
    return dr.format_mac(value).replace(":", "").upper()
```
Used on **both** sides everywhere. The two divergent inline expressions from the original plan are deleted. `dr.format_mac` handles colon/case/separator drift; cloud `device_id` (`5432044e9768`) and native `CONNECTION_NETWORK_MAC` (`54:32:04:4e:97:68`) both normalize to `5432044E9768`.

Pure, unit-testable functions:

- `build_fleet_map(hass, coordinator) -> FleetMap` — the read-only join, **decoupled from names** (critic F2):
  ```python
  @dataclass(frozen=True)
  class FleetEntry:
      cloud_id: str                  # lowercase hex; the join key (always present)
      cloud_name: str | None         # eventually-consistent; may be None — NEVER part of matching
      native_ha_device_id: str | None
      our_ha_device_id: str | None
      has_cloud_control: bool        # we expose switch/light/cover for it
      has_local_control: bool        # native exposes switch/light/cover/climate
  ```
  `cloud_name` is read from `coordinator.device_names.get(cloud_id)` and explicitly documented as best-effort/optional; it is **never** an input to the match. The match set is `{cloud_id → native_ha_device_id}` computed from `coordinator.devices` keys and the device registry alone.

- `_index_native_shelly(dev_reg) -> dict[str, dr.DeviceEntry]` (critic T3) — walk the registry; for each device belonging to a config entry of `domain == NATIVE_SHELLY_DOMAIN`, take the **main device's** `CONNECTION_NETWORK_MAC` directly and key by `_norm_mac(mac)`. Sub-device identifier parsing (`(shelly, "{mac}-{key}")`) is a **try/except fallback only**, used solely if a device has no MAC connection — never the primary path. The parent MAC is already on the main device, so this fallback rarely fires.

- `match(cloud_id, native_index) -> str | None` — `native_index.get(_norm_mac(cloud_id))`. The trivial, verified join.

Native name handling (critic L1 + R1 — **no automatic native writes anywhere**):
- `suggest_native_names(fleet, coordinator) -> list[NameSuggestion]` — for each matched entry with a `cloud_name`, where the native `DeviceEntry.name_by_user is None` and `name != cloud_name`, emit an **advisory** suggestion (for the report). This computes; it does not write.
- `apply_native_name_suggestions(dev_reg, suggestions)` — only called from the manual service when `apply_native_name_suggestions=True and dry_run=False`. Writes `dr.async_update_device(id, name=...)`, never `name_by_user`. This is the sole native-write path; it is operator-initiated and reversible (the operator can clear it or set their own `name_by_user`).
- Our **own** devices' names are intentionally **out of scope here** (critic E3) — the coordinator's `_refresh_device_names` already maintains them every poll; duplicating that in fleet_map would race it. fleet_map only *reports* own-device names.

Resilience-check (critic L2 — split into two categories):
- `scan_resilience(hass, fleet) -> ResilienceReport`:
  - `has_cloud_control` = our integration has ≥1 entity whose domain ∈ `CONTROL_DOMAINS = ("switch","light","cover")` for the device (`er.async_entries_for_device`, filter on `entity_id.split(".")[0]`).
  - `has_local_control` = matched native device has ≥1 entity with domain ∈ `CONTROL_DOMAINS | {"climate"}`.
  - **Category 1 — `cloud_only_control`** (authoritative): `has_cloud_control and not has_local_control`. May be *unavoidable* (shared/remote Shelly). Reported, not alarmed.
  - **Category 2 — `cloud_control_despite_local_twin`** (actionable): a matched device where a local twin exists **and** an automation/script references our cloud control entity. This is the genuine, common, fixable misconfiguration ("a local twin exists — point the automation at it"). This is the category that *would* drive a repair issue once the classifier is validated (deferred per R2).
- Automation/script attribution (critic T1 — demoted to best-effort): there is no stable, import-safe public API for "all entity_ids referenced by every automation/script." Therefore Stage 1:
  - leads with the **device-level** classification (the high-value 80%, fully testable);
  - performs the automation scan **only** as a best-effort enrichment, wrapped in `try/except` that downgrades to "unknown / not attributed" on any failure, and the report labels this section **"best-effort, may be incomplete"**;
  - does **not** claim "appears within one coordinator tick" or any "it just works" guarantee. If a robust public API is unavailable at implementation time, the automation scan is omitted entirely and only Category 1 + the device-level twin existence are reported.

Reporting:
- `format_report(fleet, suggestions, resilience) -> str` for the notification (explicit local⇄cloud pairing + delete warning, critic L3).
- `to_diagnostics(fleet, suggestions, resilience) -> dict` for diagnostics.

**`custom_components/shelly_cloud_diy/diagnostics.py`** — `async_get_config_entry_diagnostics(hass, entry)` returning `to_diagnostics(...)`, MACs redacted via `async_redact_data`. Scoped to **this entry's** `coordinator.devices` (critic E2).

### DEFERRED files (Stage 1.5, spec retained, not in first cut)

**`sensor_fleet.py`** — a single `FleetMapSensor(SensorEntity)` per config entry, state = number of `cloud_control_despite_local_twin` devices. Recomputes from `build_fleet_map` on a slow `async_track_time_interval` (5 min) and on a **debounced** `SIGNAL_FLEET_REFRESH` (critic T2: coalesce via `async_call_later`; **never write registries from the signal path**). Returns `STATE_UNKNOWN` until `coordinator.last_update_success` is true — **never publishes a transient 0** (critic E1). unique_id `f"{entry.entry_id}_fleet_map"`, attached to a synthetic service device `identifiers={(DOMAIN, f"{entry.entry_id}_fleet")}`. Ships only after the classifier is validated on the live account.

**Repair issue** `cloud_control_despite_local_twin` via `issue_registry`, advisory/dismissible — deferred until proven false-positive-free.

### MODIFIED files

- **`__init__.py`** — `_register_services`: add a `fleet_map` block modeled on `replace_device` (`__init__.py:162-176`), handler `partial(async_handle_fleet_map, hass)`, schema:
  ```python
  vol.Schema({
      vol.Optional("dry_run", default=True): cv.boolean,
      vol.Optional("apply_native_name_suggestions", default=False): cv.boolean,
  })
  ```
  No coordinator/poll-path changes (critic L1: the original `coordinator.py` auto-sync hook is **removed entirely**).
- **`const.py`** — add `NATIVE_SHELLY_DOMAIN = "shelly"`, `CONTROL_DOMAINS = ("switch","light","cover")`, `CLIMATE_DOMAIN = "climate"`. Deferred-cut constants (`SIGNAL_FLEET_REFRESH`, `ISSUE_CLOUD_CONTROL_DESPITE_TWIN`) added only when the sensor/issue ship. **No** `CONF_ENABLE_NAME_SYNC` automatic toggle (the only native write is the manual service flag).
- **`coordinator.py`** — **no change in the first cut.** The original plan's `async_dispatcher_send(SIGNAL_FLEET_REFRESH)` line and the opt-in auto native-sync are both removed (critic L1/T2). If/when the deferred sensor ships, the only addition is a single debounced dispatcher send after `_refresh_device_names`, which fires sensor recompute but performs **no registry write**.
- **`config_flow.py`** — **no change in the first cut** (no standing options needed for a manual service). The deferred sensor would add no options either; native apply stays a per-call service flag, keeping it explicitly operator-initiated.
- **`services.yaml`** — add a `fleet_map:` block (name/description/fields: `dry_run`, `apply_native_name_suggestions`), same style as `replace_device`.
- **`strings.json` + `translations/en.json` + `translations/de.json`** — add the service name/description/fields. (Deferred sensor/issue strings added with those features.) HACS-default-store requires every user-visible string translated (CLAUDE.md).

### HA registry/coordinator APIs used (all public)
- `device_registry`: `async_get`, `DeviceEntry.connections`, `CONNECTION_NETWORK_MAC`, `format_mac`, `async_update_device(name=…)`, `async_get_device(identifiers=…)`.
- `entity_registry`: `async_get`, `async_entries_for_device`, `async_entries_for_config_entry`.
- `homeassistant.components.diagnostics.async_redact_data`.
- (Deferred) `issue_registry`, `event.async_track_time_interval`, `event.async_call_later`, `dispatcher` — only with the deferred sensor/issue.
- Automation/script reference enumeration: best-effort only, wrapped in `try/except`; no claim of API stability.

### Data flow
```
coordinator.devices (lowercase cloud_id, post-first-refresh)  ─┐  (JOIN KEY)
device_registry (native shelly: CONNECTION_NETWORK_MAC)        ─┼─▶ build_fleet_map ─▶ FleetMap (match independent of names)
device_registry (our devices: (DOMAIN, device_id))            ─┤
entity_registry (CONTROL_DOMAINS entities, both sides)        ─┘        │
coordinator.device_names (cloud alias) ····(optional, eventually-consistent, REPORT ONLY)····▶ suggest_native_names
build_fleet_map ─▶ scan_resilience ─▶ {cloud_only_control, cloud_control_despite_local_twin(best-effort)}
service(apply_native_name_suggestions=True, dry_run=False) ─▶ dr.async_update_device(name=)  [manual, native main only, reversible]
```
No new network calls. Matching never reads names.

## 4. Local-first compliance

- Stage 1 only **reads** the device/entity registries and the coordinator's already-fetched snapshot. The **only** write is the cosmetic `name` field, and (a) for our own devices it is already done by the coordinator, (b) for native devices it happens **exclusively** via the manual, dry-run-default, off-by-default service call — never on the poll/coordinator hot path (critic L1).
- No control entity is created, removed, or re-pointed; no native config entry is touched; no cloud command is issued. Our sole control path remains `coordinator.send_command` (coordinator.py:303), which Stage 1 never calls.
- When the cloud/internet is DOWN: the coordinator poll fails (UpdateFailed/ConfigEntryAuthFailed), our cloud entities go `unavailable`, the Fleet-Map service still produces matches from local registries + last in-memory snapshot. Native entities, native commands, and native automations are unaffected by construction — they never traverse our code. The resilience check *surfaces* any place this invariant is at risk, making local-first auditable.

## 5. Edge cases & failure modes

- **Names not yet resolved / cloud flaky** (critic F2): `coordinator.device_names` may be partial or empty (lazy, online-only, single-flight, error-swallowing — coordinator.py:224-254). Matching is unaffected (MAC-only). `cloud_name=None` → no suggestion, report marks it null. The match set does not churn when names arrive late.
- **Pre-first-refresh** (critic E1): service raises `ServiceValidationError("Fleet not loaded yet")` if `not coordinator.last_update_success`. The deferred sensor returns `STATE_UNKNOWN` (never a transient 0).
- **Multiple cloud entries** (critic E2): everything is scoped to the calling entry's `coordinator.devices`. `_index_native_shelly` is global (native devices aren't per-cloud-entry) but matches are only emitted for this entry's cloud_ids, so no cross-attribution.
- **Two device cards per physical Shelly** (critic L3): expected — our device uses `(DOMAIN, device_id)` with no MAC connection (base.py:79); native uses `CONNECTION_NETWORK_MAC`. The report explicitly pairs them and warns: *do not delete the local card* (deleting it breaks local control/history). Stage 1 does not merge them (out of scope).
- **Cloud-only controllable device** (shared/remote Shelly): no native match → `cloud_only_control` (Category 1, may be unavoidable). A shared **sensor-only** WS90 (operator USP) has no control domain → not flagged.
- **Local-only device**: appears in `local_only`; no cloud alias → no suggestion; no flag.
- **Multi-channel / sub-devices** (critic T3): index off the main device's `CONNECTION_NETWORK_MAC`; a Pro4PM folds to one match. Native name suggestion targets the **main** device only. Sub-device identifier parsing is a try/except fallback, not the primary path.
- **BLU / BLE gateway-bridged** (`gen=="GBLE"`): BLU MAC isn't a Wi-Fi MAC and native usually has no LAN device → no match; sensor-only → not flagged; the report explains why no local twin exists.
- **`name_by_user` set**: native apply always skips it (SC2) — the one rule that must never regress.
- **Automation scan unavailable** (critic T1): if no robust read-only enumeration exists at build time, Category 2's automation attribution is omitted; the report still shows device-level twin existence and Category 1. No false "it just works" claim.
- **Stage 2/3 guards** (`MacAddressMismatchError`, `another_device`, on-device RPC) are out of scope; Stage 1 rewrites no unique_ids and does no LAN RPC.

## 6. Testing & verification

- **Pure-function unit tests** (no HA loop), the primary safety net: `_norm_mac` (colon/case/separator drift), `_index_native_shelly` (main-device MAC; sub-device fallback), `match`, `suggest_native_names` (the `name_by_user` skip), `scan_resilience` device classification (Cat 1 vs Cat 2). Feed synthetic `DeviceEntry`/`RegistryEntry` fixtures.
- **Live dry-run (61 devices):** call `shelly_cloud_diy.fleet_map` with defaults. Verify matched count equals a manual `_norm_mac` cross-check on a spot sample (`5432044e9768` ⇄ `54:32:04:4e:97:68`). Confirm **zero** registry writes (diff `.storage/core.device_registry`). Confirm match count is unchanged on a second run after names have resolved (proves F2 decoupling).
- **Manual native-apply test:** on **one** matched native device still showing a model default, run `apply_native_name_suggestions: true, dry_run: false`; confirm `name` changed, `name_by_user` still null; set a user name in the UI, re-run, confirm skipped (idempotence + override safety).
- **Resilience classification test:** with a known cloud-only controllable device, confirm it lands in `cloud_only_control`; with a device having both twins and an automation pointed at the cloud entity, confirm `cloud_control_despite_local_twin` (or, if the automation scan is omitted, confirm twin existence is reported and the section is labeled best-effort/omitted).
- **SC4:** asserted **by architecture** — Stage 1 adds no call to `send_command` or any control endpoint (verified single path at coordinator.py:303). No iptables theatre; the original `iptables -d <server_uri>` test is dropped as misleading (critic E4): `server_uri` is a URL, and native control survives trivially because it never touches our code.

## 7. Risks & mitigations

- **Native-integration internals dependency:** native domain `"shelly"`, `CONNECTION_NETWORK_MAC` on the main device, sub-device identifier shape. All verified on the live account and are stable public registry conventions. Mitigation: isolate every assumption in `_index_native_shelly` behind `getattr`/`isinstance`/`try/except` that downgrades to "no match" rather than raising; log the native index size at debug for drift visibility.
- **Writing another integration's `name`** (critic R1 — the headline review risk): de-risked by making it (1) **never automatic**, (2) manual + dry-run-default + off-by-default, (3) reversible, (4) `name` only, never `name_by_user`/`connections`/`identifiers`. The native↔cloud "flap war" concern (native reasserting its default on reload) is real and **acknowledged as unverified**; therefore native apply is opt-in and documented as "may be overwritten by the native integration on its reload; clear it or set name_by_user to make it stick." This keeps the integration shippable as a cooperative overlay rather than a hijacker.
- **Automation scan fragility** (critic T1): never authoritative; best-effort, try/except, omittable. The high-value device-level signal does not depend on it.
- **Scope/review surface** (critic R2): first cut is diagnostics + one manual service + notification — zero standing entities. Sensor + repair issue deferred until the classifier is validated, avoiding persistent false-positive annoyance.
- **HACS rules** (CLAUDE.md): `ServiceValidationError`/`HomeAssistantError`, English logs, full translations, matching `replace_device`.

## 8. Out-of-scope (Stage 1) & cross-stage dependencies

**Out of scope:**
- Any rewrite of native unique_ids / config-entry data / host / connections (Stage 2 unified `replace_device`, incl. aioshelly `MacAddressMismatchError` / `another_device` handling and the upstream core-`shelly` repair-flow PR to @thecode/@bieniu/@chemelli74/@bdraco).
- Any LAN-RPC read/write of on-device config, schedules, scripts, KVS, webhooks (Stage 3).
- Push (HA → cloud) name writes; we are pull-only.
- Auto-creating native entities or merging the two device cards into one HA device.
- **Automatic** native name writing (any poll-path or options-triggered auto-sync) — explicitly excluded for local-first + review reasons.

**Dependencies / hand-offs:**
- `build_fleet_map`, `_index_native_shelly`, `_norm_mac`, `match` are the **reusable substrate** Stage 2 needs to identify the native target device and Stage 3 needs to find the new device's LAN host. Build them as standalone, importable functions in `fleet_map.py`.
- The `cloud_only_control` and `cloud_control_despite_local_twin` lists are the candidate sets Stage 2 offers for unified replace and Stage 3 offers for config-clone.
- No new hardware; buildable now against the live 61-device account.

**Key grounding references (paths corrected per critic F1):** matching uses `coordinator.devices` keys (lowercase) vs native `CONNECTION_NETWORK_MAC` via `dr.format_mac`; lazy/online-only/single-flight/error-swallowing name lookup at `coordinator.py:224-254`; own-device name write + `name_by_user` preservation at `coordinator.py:276-296`; single control path at `coordinator.py:303`; name source `ShellyCloudControl.get_device_names` → v1 `/interface/device/list` at `api/cloud_control.py:227-277`; service registration pattern at `__init__.py:162-176` and `services/replace_device.py`; own-device identity `(DOMAIN, device_id)` with no MAC connection at `entities/base.py:79`.

---

## Changes made vs critic

- **L1/R1 (ACCEPTED, must-fix #1):** removed all automatic native writes. The coordinator auto-sync hook is deleted; the only native `name` write is the manual, dry-run-default, off-by-default `fleet_map` service flag. Our own devices' names stay with the coordinator (no duplication, critic E3).
- **T1/SC3 (ACCEPTED, #2):** automation/script scan demoted to best-effort (try/except, omittable, labeled "may be incomplete"); device-level classification leads. Dropped the "appears within one coordinator tick" guarantee.
- **L2 (ACCEPTED, #3):** resilience split into `cloud_only_control` (maybe unavoidable) vs `cloud_control_despite_local_twin` (actionable); only the latter would drive the (deferred) repair issue.
- **F2 (ACCEPTED, #4):** matching fully decoupled from `device_names`; `cloud_name` is report-only and eventually-consistent; match set never churns on late names.
- **T4 (ACCEPTED, #5):** one `_norm_mac` helper built on `dr.format_mac`, used both sides; divergent inline expressions deleted.
- **R2 (ACCEPTED, #6):** first cut = diagnostics + one manual service + notification; summary sensor and repair issue deferred (spec retained as Stage 1.5).
- **E1/E2 (ACCEPTED, #7):** deferred sensor returns `STATE_UNKNOWN` pre-first-refresh (no transient 0); everything scoped per config entry.
- **T2/T3 (ACCEPTED, #8):** when the deferred sensor ships, `SIGNAL_FLEET_REFRESH` is debounced and never writes registries; native indexing is off the main device's `CONNECTION_NETWORK_MAC` with sub-device parsing as try/except fallback only.
- **L3 (ACCEPTED):** report explicitly pairs the two device cards per physical Shelly and warns against deleting the local one.
- **E4 (ACCEPTED):** dropped the misleading `iptables -d <server_uri>` test; SC4 is asserted by architecture (single control path at coordinator.py:303).
- **F1 (ACCEPTED):** corrected `cloud_control.py` → `api/cloud_control.py` everywhere.
- **Config-flow options (PARTIALLY REJECTED the original, ALIGNED with critic):** the original `enable_name_sync` options toggle is removed entirely rather than kept default-off. Justification: an options toggle implies a standing/automatic behavior, which is exactly the local-first hazard the critic flagged; making native apply a per-call service flag is strictly safer and keeps the action explicitly operator-initiated each time.
- **One nuance retained against a literal reading of the critic (justified, not a rejection):** the critic floated cutting native name handling *entirely* from Stage 1. I keep it, but only as **report/suggest by default** plus a **manual, reversible apply** — because surfacing the cloud alias for native devices is the operator's stated overlay value (naming), and confining the write to an explicit, off-by-default, reversible service call satisfies every concern the critic raised (no hot path, no standing option, no `name_by_user` touch) without discarding a core feature.

Final plan file paths referenced: `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/services/fleet_map.py` (new), `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/diagnostics.py` (new), and modifications to `__init__.py`, `const.py`, `services.yaml`, `strings.json`, `translations/en.json`, `translations/de.json` under `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/`.


<details><summary>Kritiker-Review (Rohfassung)</summary>


I now have enough verified ground truth. Here is my adversarial review.

---

# ADVERSARIAL REVIEW — Stage 1 "Fleet-Map"

I read the actual source. Most factual claims in the plan check out, but several are wrong or sloppy, and the design has real local-first and review-blocker problems. Findings below, severity-tagged, evidence-based.

## Factual-grounding errors in the plan itself (these undermine confidence)

**F1 — WRONG FILE PATH (MINOR, but it means the author didn't open the file).** The plan repeatedly cites `cloud_control.py:227` and "cloud_control.py:227-277". The file is at `api/cloud_control.py`. The line numbers happen to match, but the path is wrong everywhere in the plan, including the "Key grounding references" section. Minor on its own; it signals the grounding was done from memory/notes, not the tree — which matters because other claims are wrong (see F2, M3).

**F2 — `coordinator.device_names` is NOT a reliable matching source, and the plan conflates it with the join key (MAJOR).** The plan's `FleetEntry.cloud_name = coordinator.device_names.get(cloud_id)` and "Every input is already in memory" are both overstated. `device_names` is populated *lazily, online-only, best-effort*: `_async_update_data` only schedules lookups for devices where `info.get("online")` is true (coordinator.py:224-227), the lookup is guarded by a single-flight flag `_name_lookup_in_flight` (line 228), and on any auth/transport hiccup it silently returns `{}` (lines 247-252). So on a fresh start, or whenever the cloud is flaky, `device_names` is partially or wholly empty. The plan acknowledges this for offline devices in §5 but then claims SC1 ("count of matched devices is non-zero and stable across reloads") and "No new network calls." Matching by MAC is indeed independent of names — but the plan's own dataflow diagram feeds `device_names` into `build_fleet_map` as if it's authoritative. Fix: decouple cleanly — matching uses only `coordinator.devices` keys (always present post-first-refresh); `cloud_name` is explicitly "may be None / eventually-consistent", and the sensor must not churn its match state when names arrive late.

## Local-first violations / control-path risks

**L1 — The opt-in coordinator-hooked native name-sync is a latent local-first hazard and should be cut from Stage 1 (MAJOR).** §3 "MODIFIED coordinator.py" proposes "Optionally hook the opt-in native name-sync here … so sync runs automatically as names resolve." This puts a *write to another integration's device registry rows* on the cloud coordinator's hot path, fired from `_refresh_device_names` (a best-effort background task). Concretely: every time the cloud poll resolves a name, you'd call `dr.async_update_device(name=...)` on **native shelly** devices. Problems: (a) it couples a native device's registry state to cloud availability/timing — exactly the kind of cloud-in-the-loop coupling the operator forbids, even if "only the name"; (b) `_refresh_device_names` runs under the single-flight flag and swallows errors, so failures are invisible; (c) it races with the native integration's own name management on reload. The service-driven, dry-run-default path is fine. Fix: name-sync to native devices happens **only** via the explicit, manual `fleet_map` service (or a deliberate, throttled options-gated pass that is NOT on the poll callback). Remove the coordinator auto-sync entirely for Stage 1.

**L2 — `has_local_control` includes `climate` but the resilience logic is inverted into a false-safe (MAJOR).** §3 defines the device-level flag as `has_cloud_control and not has_local_control`. But your own integration *also* exposes switch/light/cover for a device that is **simultaneously** present locally (the operator's normal case: same physical Shelly on both integrations). For those, `has_cloud_control=True` AND `has_local_control=True` → **not flagged**. Good. The danger is the opposite: the plan's "OR the operator's automations route control through the cloud entity even though a local twin exists" is the *real* local-first risk (a user wired an automation to `switch.shelly_cloud_diy_...` instead of the native twin), and that's buried as an "OR" with no concrete detection for the "twin exists but automation uses cloud" case beyond the generic automation scan. The genuinely dangerous, common misconfiguration — *cloud control entity used in an automation when a perfectly good local twin exists* — deserves to be its own first-class flag with its own message ("you have a local twin; switch the automation to it"), not folded into a generic "cloud in control path." Fix: split into two resilience categories: (1) `cloud_only_control` (no local twin — may be unavoidable, e.g. shared device), (2) `cloud_control_used_despite_local_twin` (actionable, the operator can fix). Only (2) should drive a high-visibility repair issue.

**L3 — "matched count stable across reloads" can be violated by your own integration creating duplicate-looking devices (MINOR→MAJOR depending on UX).** Your devices use `identifiers={(DOMAIN, device_id)}` with **no MAC connection** (entities/base.py:79). The native device for the same physical Shelly uses `CONNECTION_NETWORK_MAC` and a different identifier domain. So in HA you get **two separate device cards** for one physical Shelly. Stage 1 doesn't merge them (correctly out of scope), but the resilience report and sensor must make crystal clear these are the same hardware, or the operator will "fix" by deleting one and break either history or control. The plan never addresses the user-facing confusion of the two-device-per-Shelly reality that Stage 1 surfaces for the first time. Fix: the report must explicitly pair them ("Treppe OG — local: device X, cloud overlay: device Y") and warn never to delete the local one.

## Technical correctness / HA-API misuse

**T1 — `referenced_entities` access pattern is hand-wavy and partly wrong (MAJOR).** §3 admits uncertainty in the source ("is not right here — instead…", "the trace?"). The honest read: there is no stable, documented public API for "give me all entity_ids referenced by every automation/script." `BaseAutomationEntity` is not a public, import-stable surface; reaching into `hass.data["automation"]`/`hass.data["script"]` component objects and iterating their entities is **exactly** the kind of internal-poking that gets flagged in HACS/Core review and breaks on refactor. `referenced_entities` exists but the way you enumerate the entities holding it is the fragile part. Fix: either (a) drop the automation/script scan from Stage 1 entirely (the device-level cloud-only-control flag is the high-value 80%), or (b) use the supported `homeassistant.helpers.entity_registry` + the *labels/area* indirection only, or (c) accept it as best-effort, wrap in `try/except`, and clearly mark the result "may be incomplete." Do not ship it as an authoritative SC3 ("Adding a cloud-only switch to an automation makes it appear in the list within one coordinator tick after reload") — that's a false "it just works" claim.

**T2 — Direct mutation of `entity_registry.deleted_entities` / `dev_reg` semantics aren't in Stage 1, good — but the sensor's refresh model has a debounce/write concern (MINOR).** The Fleet-Map sensor recomputes on `async_track_time_interval` (5 min) AND on `SIGNAL_FLEET_REFRESH` fired from the coordinator's name-resolution (plan §3 coordinator change). Name resolution can fire repeatedly as devices come online over several polls. Without debounce, you get sensor state thrash and attribute churn (and if name-sync-on-resolve from L1 were kept, repeated registry writes → debounced `.storage` saves with churn). Fix: debounce `SIGNAL_FLEET_REFRESH` handling (e.g. `async_call_later` coalescing), and never write registries from the signal path.

**T3 — Sub-device → parent-MAC folding by "parsing the identifier prefix" is fragile and partially redundant (MINOR).** §3 `_index_native_macs_from_connections` parses native sub-device identifiers `(shelly, "{mac}-{key}")` to recover the parent MAC. But the native main device already carries `CONNECTION_NETWORK_MAC`, so the parent MAC is available directly — you only need sub-device parsing if a device somehow has sub-devices but you missed the main device, which shouldn't happen. Parsing `"{mac}-{key}"` also assumes the MAC contains no `-` and the split is unambiguous — true for Shelly's `aabbcc-switch:0` shape but undocumented. Fix: index off `CONNECTION_NETWORK_MAC` on the main device only; treat sub-device parsing as a fallback wrapped in try/except, not a primary path. (The plan's own §5 says you target only the main device for name-sync anyway, so the sub-device index buys little.)

**T4 — MAC normalization is inconsistent across the plan (MINOR).** §3 `_index_native_shelly` keys by `mac.upper().replace(":","")`. §5 "MAC format drift" uses `.upper().replace(":","").replace("-","")`. The match function uses `cloud_id.upper()`. Cloud `device_id` is lowercase hex with no separators (verified: `5432044e9768`). `CONNECTION_NETWORK_MAC` in HA is canonicalized to lowercase colon-separated (`54:32:04:4e:97:68`) by `dr.format_mac`. So the correct single normalization is `dr.format_mac(x).replace(":","").upper()` on both sides — use `dr.format_mac`, don't roll your own, and use ONE helper everywhere. The two different inline expressions are a latent bug.

## HACS / Core review blockers

**R1 — Writing another integration's device `name` is the headline review risk and the plan under-rates it (MAJOR).** The plan calls it "unusual" and mitigates with default-off + docs. A reviewer's likely position: an overlay integration should **never** mutate registry rows owned by a different config entry. Even `name` (not `name_by_user`) is owned by the native integration and may be reasserted on its reload, causing a flap war between the two integrations (native sets model default → you set cloud alias → native reload resets → …). The plan's SC2 assumes native won't fight back; that's unverified. Fix: for native devices, do **not** write their `name`. Instead, if you must surface the cloud alias, set it as a *suggestion* the user applies, or write only your OWN devices' names (which you already do in `_refresh_device_names`). Pulling cloud alias onto native devices should be, at most, a clearly-labeled, manual, reversible, explicitly-opt-in action — and even then I'd push to cut it from Stage 1 to keep the integration shippable.

**R2 — Scope creep: a "fleet" service-device + summary sensor + repair issues + diagnostics is a lot of new surface for a read-only overlay (MINOR→MAJOR).** Four user-facing surfaces (service, sensor, repair issue, diagnostics) plus a synthetic "Fleet Map" device. For Stage 1, this risks over-engineering and more review surface. The synthetic device `identifiers={(DOMAIN, f"{entry.entry_id}_fleet")}` is reasonable to avoid polluting a real device, but combined with repair issues that overlap the sensor state, you have three places saying the same thing. Fix: ship the **diagnostics + one manual service with a notification** first (lowest review risk, zero standing entities). Add the summary sensor only if the operator actually wants a dashboard number. Defer repair issues until the resilience logic is proven (they're persistent and annoying if false-positive-prone, which T1 guarantees they will be).

## Missing edge cases / false claims

**E1 — SC1 "count … stable across reloads" is not guaranteed during the first-refresh window (MINOR).** If the `fleet_map` service or sensor computes before `coordinator.last_update_success`, `coordinator.devices` is empty → zero matches. The plan guards the *service* (§5: `if not coordinator.last_update_success: raise`) but the *sensor* on its time-interval has no such guard and would publish `0`/empty then jump to real counts — violating "stable." Fix: sensor returns `unknown`/`unavailable` until first successful refresh; never publish a transient zero.

**E2 — Two cloud accounts / two config entries (MINOR).** The whole plan assumes one config entry. `_index_native_shelly` walks ALL native shelly devices globally, but the sensor/service is per-entry. With two cloud entries you'd double-count or cross-attribute. Fix: scope the fleet map to the entry's own `coordinator.devices` and document multi-entry behavior.

**E3 — `name_by_user` skip is asserted but not symmetric for OWN devices (MINOR).** For your own devices you keep the `f"{name} ({id})"` convention (correct, matches coordinator.py:279). But `plan_name_sync` "produce an op only when `name_by_user is None` and `name != desired`" — for your own devices the existing `_refresh_device_names` already does this every poll. So `plan_name_sync` targeting your own devices is **redundant with existing behavior** and risks fighting it. Fix: name-sync in fleet_map targets native devices only (and per R1, ideally not even those); never your own.

**E4 — The SC4 outage test is misleading (MINOR).** §6 proposes `iptables -A OUTPUT -d <server_uri>` — `server_uri` is a URL, not an IP; that command won't parse. More importantly, the test proves native control survives, which it trivially does because native never touches your code — so SC4 is true *by construction*, not because of anything Stage 1 does. Don't oversell the test. Just assert the invariant from architecture (your code never calls a control endpoint outside `send_command`, which platforms call — verified coordinator.py:303).

## VERDICT: **REVISE**

The core idea (MAC-join overlay, read-only matching, diagnostics) is sound and the matching claim is genuinely trivial and correct (`dr.format_mac(cloud_id) == dr.format_mac(native_mac)`). But the plan as written has a real local-first hazard (L1), a review-blocking write into another integration's rows (R1), a fragile/over-claimed automation scan (T1/SC3), and overstated grounding (F1/F2). Ship a smaller, safer Stage 1.

## PRIORITISED MUST-FIX (in order)

1. **L1 / R1 — Remove all writes to NATIVE shelly device rows from Stage 1 (definitely off the coordinator hot path; ideally entirely).** Native name "sync" becomes a manual, dry-run-default, reversible action or is deferred. Keep writing only your OWN devices' names (already done). This is the single biggest local-first + review risk.
2. **T1 / SC3 — Demote or cut the automation/script scan.** No stable public API; do not present it as authoritative. Lead with device-level cloud-only-control detection; mark any automation scan best-effort and wrapped in try/except, or defer it.
3. **L2 — Split resilience into `cloud_only_control` (maybe unavoidable) vs `cloud_control_despite_local_twin` (actionable).** Only the actionable one drives a repair issue.
4. **F2 — Decouple matching from `device_names`.** Match purely on `coordinator.devices` keys via `dr.format_mac`; treat `cloud_name` as eventually-consistent/optional; never churn match state when names arrive late.
5. **T4 — One MAC-normalization helper using `dr.format_mac`,** used on both sides; delete the two divergent inline expressions.
6. **R2 — Cut Stage 1 surface to diagnostics + one manual service (+notification).** Defer the standing sensor and repair issues until the resilience classifier is proven not to false-positive.
7. **E1 / E2 — Guard the sensor (if kept) against pre-first-refresh transient zeros; scope everything per-config-entry.**
8. **T2 / T3 — Debounce `SIGNAL_FLEET_REFRESH`, never write registries from it; index native off `CONNECTION_NETWORK_MAC` (main device), sub-device parsing as try/except fallback only.**

Relevant verified file paths: `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/coordinator.py` (lines 224-230 lazy/online-only name lookup, 276-296 own-device name write, 303 single control path), `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/entities/base.py:79` (own device identifier, no MAC connection), `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/api/cloud_control.py:227-277` (name source; note path is `api/cloud_control.py`, not `cloud_control.py` as the plan states), `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/services/replace_device.py` (service pattern), `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/__init__.py:162-176` (registration).


</details>



---

# Stufe 2 — Unified replace_device (cloud + native) + upstream PR path

## Finaler (gehärteter) Plan

The code confirms every fact the critic and brief rely on. I have what I need to produce the final tightened plan.

---

# STAGE 2 — Unified `replace_device` (cloud + native) + upstream PR path

## Implementation plan for `shelly_cloud_diy` (final, critic-incorporated)

### Context grounding (verified against the codebase)

- The cloud-only service lives at `services/replace_device.py`, registered in `__init__.py:_register_services` as `shelly_cloud_diy.replace_device`. Verified apply structure (lines 181–241): remove new device's entities → `dev_reg.async_remove_device(new)` → `dev_reg.async_update_device(old, new_identifiers=..., name=...)` → per-entity `ent_reg.async_update_entity(..., new_unique_id=...)` → `_purge_deleted_for_id` → reload (explicit, *unless* an `enabled_devices` options write already triggers a reload — the existing double-reload guard at lines 211–212, 239).
- Cloud devices: `identifiers={(DOMAIN, device_id)}`, **no** `connections` MAC (`entities/base.py:78–83`); entity `unique_id = f"{device_id}_…"`. Confirms the cloud swap is a pure prefix rewrite.
- Cloud name rewrite is integration-specific: the device name bakes in `(device_id)` (`base.py:113,131,134`), and `replace_device.py:193–195` rewrites `({old_id})`→`({new_id})`. **This logic is meaningless for native** and must stay inside the cloud path (critic m8).
- `_purge_deleted_for_id` matches by `shelly_id in (e.unique_id or "")` substring (line 82) and is scoped to one `config_entry_id` (line 82).
- Verified facts from the brief, confirmed consistent with the code: native `shelly` entity `unique_id = "{MAC-UPPER}-{key}"`; main device by `connections={(CONNECTION_NETWORK_MAC, mac)}`; sub-devices by `identifiers={("shelly", "{mac}-{key}")}`; cloud `device_id == mac.lower()`. aioshelly is a manifest dependency.

---

## 1. Goal & success criteria

**Goal:** One service, `shelly_cloud_diy.replace_device`, that transplants a dead Shelly's HA identity onto a replacement of the same model, handling **both**:
- **(a) cloud devices** — `{old_id}_`→`{new_id}_` prefix rewrite (shipped, v0.5.0).
- **(b) native core `shelly` devices** — MAC-token rewrite across the native config entry's `unique_id`, host, the device's `connections`, every sub-device `identifiers`, and all `shelly`-platform entity `unique_id`s, applied while **both** native entries are UNLOADED, with the **new entry removed unconditionally**, then the old entry reloaded.

Cloud is used only for **detection/identification** and as a **fallback identity source for a dead device**. Cloud is never in the resulting control path.

**Success criteria (observable, testable):**
1. Cloud+cloud → existing behaviour unchanged (regression suite stays green).
2. Native+native same model → after apply: all old `entity_id`s preserved, history continuous, automations/scenes/dashboards keep working, native device keeps area/labels/`name_by_user`; new hardware controls at native LAN speed.
3. One cloud + one native resolving to the **same MAC** → `ServiceValidationError` ("two views of the same physical device").
4. `dry_run: true` for both variants → complete accurate change list; registry/config-entry snapshot byte-identical before/after.
5. Model-mismatch guard fires for both variants unless `force: true`.
6. After native replace, the reloaded entry does **not** raise `MacAddressMismatchError` — because both `entry.unique_id` and device `connections` were rewritten to the **new** MAC before reload, **and** the new entry (which still held `unique_id == new_mac`) was removed first, so no duplicate `(domain, unique_id)` exists at any point.
7. Apply is atomic-ordered: any mid-apply failure leaves the old native entry UNLOADED, emits a persistent_notification with partial-state + recovery breadcrumb, and never reloads onto an inconsistent state.
8. **(NEW, critic M6)** When a cloud device being replaced has a native twin (matched by MAC), the cloud dry-run/result explicitly warns that **only the cloud overlay was migrated** and the controlling native device must be replaced separately.
9. **(NEW, critic M4)** Native apply **aborts at plan time** if no valid new host is discoverable — it never reloads a control entry onto a dead host.

---

## 2. User-facing behaviour

Operator runs **Developer Tools → Actions → `Shelly Cloud DIY: Replace device`**, picks **Old** and **New**. The handler auto-detects each device's type from registry identifiers/connections:
- both `(DOMAIN, …)` → cloud path,
- both native (`("shelly", …)` identifier and/or `CONNECTION_NETWORK_MAC`) → native path,
- mixed → refuse with guidance.

`dry_run: true` (recommended, surfaced in `services.yaml`) produces a `persistent_notification` listing every change. For native: config-entry `unique_id` MAC swap, host swap (old→new, taken from the new device's native entry **captured at plan time**), `connections` swap, each sub-device identifier rewrite, each `shelly`-platform entity `unique_id` rewrite, and the **mandatory removal of the new native config entry**. Applying reloads the old native entry. The operator never edits `.storage`.

**Removed from prior plan:** the `delete_new_entry` option. Removing the new entry is **not optional** in a native merge — leaving it would create a duplicate `(domain, unique_id)` and a duplicate control device (critic B2/B3). There is no coherent "keep it" mode.

Name-Sync (Stage 1) is not built here. Native path **never touches** `name`/`name_by_user`. The cloud name rewrite stays strictly inside the cloud path.

---

## 3. Technical design

### New files

**`services/replace_native.py`** — native (MAC-based) variant.

```python
async def replace_native_device(hass, *, old_dev, new_dev, dry_run, force) -> NativeReplacePlan
```

- `NATIVE_SHELLY_DOMAIN = "shelly"` imported from `const.py` (no Python import of the native integration).
- **Two independent tokens, never crossed (critic M1/M2):**
  - `connection_mac = dr.format_mac(raw)` — colon-lowercase; used **only** for `(CONNECTION_NETWORK_MAC, …)` tuples.
  - `uid_token = old_entity.unique_id.split("-", 1)[0]` — derived from a real `shelly`-platform entity row of the old device; used for entity-uid rewrites.
  - `subdev_token` — derived independently from an actual old sub-device identifier (`split("-", 1)[0]` of a `("shelly", …)` identifier), **not** assumed equal to `uid_token` (native uses different casing across the uid vs identifier namespaces).
  - A test asserts each derived token round-trips against the rows it was derived from.
- `@dataclass NativeReplacePlan`: `entry_id_old`, `entry_id_new`, `entry_unique_id_old/new`, `host_old`, `host_new` (**captured at plan time**), `device_connection_rewrite`, `subdevice_identifier_rewrites`, `entity_uid_rewrites: list[(entity_id, old_uid, new_uid)]`, `plan_text`, `breadcrumb` (the full plan serialised for the recovery notification).
- `async def _build_native_plan(...)` — pure planning, **zero cloud calls, zero writes**:
  - Find each device's `shelly`-domain config entry.
  - Derive `connection_mac_old/new`, `uid_token_old/new`, `subdev_token_old/new` from real rows; **abort cleanly** if the old device has no `shelly`-platform entity matching `<token>-<key>` (native scheme changed — critic M11 runtime guard).
  - **(critic B1)** Enumerate `er.async_entries_for_device(old_dev, include_disabled_entities=True)` filtered to `ent.platform == NATIVE_SHELLY_DOMAIN` **and** `ent.unique_id.startswith(f"{uid_token_old}-")`. Compute new uid = `f"{uid_token_new}-" + remainder`. Any entity on the device that is *not* a `shelly`-platform row (e.g. a user-attached helper/template) is left untouched.
  - Main device `connections` rewrite `(CONNECTION_NETWORK_MAC, connection_mac_old)`→`(…, connection_mac_new)`.
  - Each sub-device identifier `("shelly", f"{subdev_token_old}-{key}")`→`("shelly", f"{subdev_token_new}-{key}")`.
  - **(critic M4)** `host_new` from the new device's own native entry `data[CONF_HOST]`, **captured into the plan now**. If absent/empty → **abort** with guidance ("could not determine the new device's host; add it to the native Shelly integration first"). Do **not** "warn and proceed onto the dead host."
- `async def _apply_native_plan(...)` — strict order (critic B2/B3/M4):
  1. `await hass.config_entries.async_unload(entry_id_new)` then `async_unload(entry_id_old)` — both unloaded (native talks to hardware live and would fight rewrites / recreate rows).
  2. Remove the **new** device's entities + new sub-devices + new main device entry (frees `{uid_token_new}-…` uids and `("shelly", subdev_token_new-*)` identifiers).
  3. **Unconditionally** `await hass.config_entries.async_remove(entry_id_new)` — its hardware identity is being merged into the old entry; this must happen **before** the old entry's `unique_id` is rewritten so two entries never share `(domain, new_mac)`.
  4. Rewrite the old native config entry: `async_update_entry(old_entry, unique_id=entry_unique_id_new, data={**data, CONF_HOST: host_new})`.
  5. `dev_reg.async_update_device(old_dev.id, new_connections={(CONNECTION_NETWORK_MAC, connection_mac_new)})` (preserves `name_by_user`, area, labels; does **not** set `name`).
  6. Rewrite each old sub-device identifier via `async_update_device(subdev.id, new_identifiers=...)`.
  7. Rewrite each old entity `unique_id` via `ent_reg.async_update_entity(eid, new_unique_id=...)`.
  8. Purge ghost `deleted_entities` for the new token, **case-insensitively** (critic M3): match `uid_token_new.lower() in (e.unique_id or "").lower()` scoped to the old entry id.
  9. `await hass.config_entries.async_reload(entry_id_old)` — old entry now points at the new MAC/host; aioshelly connects to the new unit, MAC matches `unique_id`, no `MacAddressMismatchError`, rows adopted.
  - Steps 2–8 wrapped in `try/except`; on failure: **no reload**, emit a persistent_notification with the partial state, which step failed, and the `breadcrumb` (full intended plan) under a stable `notification_id` that survives restart (critic m9).

### Modified files

- **`services/replace_device.py`** → **dispatcher**. `async_handle_replace_device` resolves both devices, classifies each (`classify_device`), routes: cloud+cloud → `_replace_cloud_device` (the current body, moved verbatim incl. its name rewrite); native+native → `replace_native.replace_native_device`; mixed/unknown → `ServiceValidationError`. Shared up-front validation (same-device, both-exist) stays here; model guard is applied per-path (cloud path keeps its current guard; native path runs the same `old.model`/`new.model` check before building the plan).
  - **(critic M6)** In `_replace_cloud_device`, after computing the plan, check whether `old_id.upper()` matches the MAC of any device in the **native** `shelly` integration (scan `dev_reg` for a `(CONNECTION_NETWORK_MAC, …)` whose `format_mac` upper-equals `old_id.upper()`). If a native twin exists, append a prominent warning to both the dry-run and the done notification: "This device is also controlled natively (LAN). This service only migrated the **cloud overlay**. To replace the controlling device, run the native replace as well." This kills the false "device replaced" implication.
- **`services.yaml`** — **(critic m7)** keep the device selectors but accept the worse picker UX: remove the `integration: shelly_cloud_diy` constraint (so native devices are selectable) and **validate hard in code** with a `ServiceValidationError` that names which integration each device belongs to. Do **not** rely on a `manufacturer: Shelly` selector filter (unreliable, version-dependent, and can't distinguish cloud vs native anyway). Update `name`/`description` to "works for cloud-overlay and native-LAN Shelly devices; dry-run first." **No `delete_new_entry` field** (removed).
- **`__init__.py`** — `_register_services` schema: no new field (the `delete_new_entry` option is dropped). Same service name dispatches internally.
- **`const.py`** — add `NATIVE_SHELLY_DOMAIN = "shelly"`.
- **`translations/en.json` + `de.json` + `strings.json`** — update the widened service description strings (HACS/Core require translations for user-visible strings).
- **`services/replace_common.py`** (optional refactor) — shared `classify_device`, the native-twin MAC detector, and dry-run notification formatting.

### HA APIs used (all already imported)
`device_registry` (`async_get`, `async_get_device`, `async_update_device`, `async_remove_device`, `format_mac`, `CONNECTION_NETWORK_MAC`), `entity_registry` (`async_get`, `async_entries_for_device`, `async_update_entity`, `deleted_entities`), `hass.config_entries` (`async_get_entry`, `async_update_entry`, `async_unload`, `async_reload`, `async_remove`), `ServiceValidationError`/`HomeAssistantError`, `persistent_notification`, `CONF_HOST`.

### Data flow (native)
classify both native → `_build_native_plan` reads old/new tokens + `host_new` from real rows (no cloud) → dry-run notification *or* `_apply_native_plan`: unload both → delete new's rows → **remove new entry** → rewrite old entry `unique_id`+host → rewrite connections + sub-device identifiers + `shelly`-platform entity uids → purge ghosts (case-insensitive) → reload old entry → aioshelly connects to new hardware, MAC matches, rows adopted.

---

## 4. Local-first compliance

- Native variant: **zero cloud HTTP** in plan or apply; operates purely on HA registries + the native `shelly` entry. After reload, control runs entirely via the native LAN integration (aioshelly). Our coordinator is never in native control.
- **(critic B3 honoured)** No duplicate control device is ever left behind: the new native entry is removed unconditionally, so no second native path to the same relay and no split history can survive a restart.
- Cloud touched only in the dead-device fallback (read name/model from the in-memory `coordinator.devices` snapshot — no extra network call) and in the native-twin detection (registry-only). Both read-only metadata.
- **Cloud/internet DOWN:** native variant fully functional (MACs and host come from persisted registry/local entry; reload reconnects over LAN). Cloud variant's model guard already reads the HA registry, not live cloud (`replace_device.py:127–134`), so the swap works offline; only re-binding fresh cloud entities waits for cloud — expected (overlay, not control). Dead-device fallback degrades gracefully if the cloud snapshot is empty.

---

## 5. Edge cases & failure modes

- **Mixed (cloud+native), same MAC** → `ServiceValidationError`: "two views (cloud overlay + native LAN) of the same physical device." Detection `cloud_id.upper() == format_mac(native_conn).upper()`.
- **Mixed, different units** → out of scope (incompatible identity schemes); refuse with guidance to replace within the same integration.
- **Native `MacAddressMismatchError` guard** → avoided by rewriting `entry.unique_id` + device `connections` to the new MAC **before** reload, and removing the new entry **before** the unique_id rewrite so `(domain, new_mac)` is never duplicated. We never call native's reconfigure flow (which aborts `"another_device"`); we mutate the registry directly while unloaded.
- **Native scheme drift** → if the old device has no `shelly`-platform entity matching `<token>-<key>`, abort cleanly (critic M11) rather than corrupt rows.
- **Non-shelly entities on the native device** (user-attached helpers/templates) → never rewritten; the rewrite is filtered to `ent.platform == "shelly"` (critic B1).
- **Offline/dead old device** → expected; everything comes from the persisted registry. Cloud overlay (if reachable) supplies a name/model fallback only when the registry has none.
- **Multi-channel / sub-devices** → enumerate and rewrite every sub-device identifier (using `subdev_token`) and every per-channel entity uid (using `uid_token`). Cloud variant has no sub-devices.
- **BLU/BLE (GBLE)** → cloud-only devices (identifier `(DOMAIN, id)`) → classify as cloud → cloud path; they have no native twin so the native-twin warning never fires for them. (The prior "refuse GBLE in native" guard was dead code — removed per critic M6.)
- **`name_by_user` / area / labels** → preserved by `async_update_device(new_connections=…/new_identifiers=…)`; native path never sets `name`.
- **New device on a different config entry (native)** → the normal native topology (one entry per device). Plan handles cross-entry merge: identity merged into the **old** entry, **new entry removed**. The cloud `_resolve_entry` shared-entry assumption does **not** apply to native — the native planner must not require a shared entry.
- **Host undiscoverable for new device** → **abort at plan time** (critic M4); do not proceed onto a dead host (which would put the reloaded control entry into a permanent `setup_retry` loop, hammering a wrong IP — critic m10).
- **Hostname vs IP / DNS stall** → if `host_new` is a hostname, the reloaded entry resolves it as native normally would; we surface a note in the dry-run that the captured host is a name, not an IP.
- **Partial-apply failure** → leave old entry UNLOADED, no reload, notification with breadcrumb (criterion #7).
- **Native-twin of a replaced cloud device** → warn that only the overlay was migrated (criterion #8, critic M6).
- **Force semantics** → overrides the model-mismatch guard for both variants.

---

## 6. Testing & verification approach

- **Snapshot dry-run equality** — both variants: snapshot `dev_reg`/`ent_reg`/`config_entries` to dicts, run `dry_run=True`, re-snapshot, assert byte equality.
- **Token round-trip tests (critic M1/M2)** — assert `uid_token` and `subdev_token` derived from seeded rows reproduce those rows exactly; assert `connection_mac` (format_mac output) is never used in uid/identifier comparisons (a test that seeds upper-no-colon uids and a colon-lower connection and verifies the rewrite still matches all uids).
- **Unit tests** (`pytest-homeassistant-custom-component`, `MockConfigEntry`, seeded registries):
  - native single-channel: entry `unique_id`+host rewritten, connections rewritten, every `shelly` entity uid rewritten, `entity_id`s unchanged, `name_by_user`/area preserved, **new entry removed**.
  - native multi-channel: sub-device identifiers + per-channel uids all rewritten.
  - **platform filter (critic B1):** seed a non-shelly helper entity on the native device → assert it is untouched.
  - **no-duplicate-unique_id (critic B2):** assert the new entry is removed before the old entry's `unique_id` is rewritten (ordering assertion + post-state: exactly one entry with `unique_id == new_mac`).
  - **ghost purge case-insensitive (critic M3):** seed a `deleted_entities` ghost with mixed-case new MAC → assert gone post-apply.
  - **host abort (critic M4):** new entry with no `CONF_HOST` → assert `ServiceValidationError`, no writes.
  - classification matrix: cloud/cloud, native/native, cloud/native-same-MAC, cloud/native-diff.
  - model guard with/without `force`.
  - partial-failure: monkeypatch `ent_reg.async_update_entity` to raise mid-loop → assert no reload, notification created with breadcrumb.
- **Runtime native-scheme guard (critic M11)** — the real safety net, documented as such: at apply time, read the *installed* native rows and abort if they don't match `<token>-<key>`. Unit tests pin *our assumptions*; this guard catches a native refactor in prod.
- **Live dry-run on operator's HA** (61 devices) — native dry-run on a real pair; **verify the derived tokens print exactly as the native rows hold them** before any apply. **No native apply on prod until must-fixes 1–4 are merged and the dry-run token output is reviewed.**
- **Regression** — existing cloud-variant tests stay green after the dispatcher refactor.

---

## 7. Risks & mitigations

- **Depending on native internals** (`"{MAC}-{key}"` uid, `("shelly", "{mac}-{key}")` identifiers, `CONF_HOST`). *Mitigation:* derive tokens from real rows; never hard-code format; runtime scheme guard aborts cleanly on drift; tests pin assumptions.
- **MAC casing (critic M1/M2)** — two explicit values (`connection_mac` via `format_mac` for connections only; `uid_token`/`subdev_token` via `split("-",1)[0]` from real rows), never crossed; round-trip tests.
- **Cross-integration entry mutation (critic M5 — reweighted as a primary concern):** writing `async_update_entry`/`async_remove` on the `shelly` domain from our integration is exactly what HA Core review rejects ("an integration must not modify another integration's entries"). HACS default store is more lenient, but it is a maintenance landmine: any native schema change (`CONF_HOST` rename, gen2 multi-host where `CONF_HOST` becomes a list, BLE-bridge sub-entries) breaks us with data corruption, not a clean error. *Mitigation:*
  - The **upstream core-`shelly` repair-flow PR is the primary deliverable** (below), positioning our native service as the interim tool.
  - Ship the native variant behind a **loud experimental gate** (config/options toggle defaulting off + a one-time repair issue explaining the risk) and a **native-integration version/compat check** (the runtime scheme guard doubles as this — abort if the native scheme isn't what we validated against).
  - Native path stays strictly registry-level, preserves history, deletes nothing the user can't re-add.
- **Atomicity (critic m9)** — registry writes hit `.storage` via *debounced* `async_schedule_save`; a crash between writes and flush tears state regardless of `try/except`. *Mitigation:* unload-before-write, ordered apply, no-reload-on-failure, and a **recovery breadcrumb** persisted as a `persistent_notification` (stable `notification_id`, survives restart) so a crashed apply is diagnosable. Framed honestly as atomic-ordered, **not** transactional.
- **Duplicate control entry (critic B2/B3)** — eliminated: new entry removed unconditionally, before the unique_id rewrite.
- **Wrong/stale new host (critic M4/m10)** — eliminated by aborting at plan time when no valid `host_new`.
- **Double-reload** — native path reloads the old entry exactly once and never triggers an options-listener reload (the `enabled_devices` swap is cloud-only).

### Upstream PR path (core `shelly`) — primary long-term home
- Architecture discussion **#1088** endorsed per-integration repair flows; **@thecode** (shelly codeowner) proposed exactly this and cited the ESPHome replace-device repair PR (**core #142507**) as the model. Codeowners to tag: **@bieniu @thecode @chemelli74 @bdraco**.
- Plan: implement device replacement as a **`RepairsFlow`** inside `homeassistant/components/shelly/`, mirroring the ESPHome PR — swap entry `unique_id`, device `connections`, sub-device identifiers, and entity `unique_id`s while unloaded, then reload, reusing aioshelly's MAC validation. Open a draft referencing #1088 and #142507; request **@thecode** first given the prior endorsement. Our `replace_native.py` is the reference implementation / validation ground. Once upstream lands, our service **defers to native's flow** for native devices and keeps only the cloud variant + cross-detection.

---

## 8. Out of scope (this stage) & dependencies

**Out of scope:**
- **Cross-type swap** (cloud device ↔ native device as the replacement) — incompatible identity schemes; not attempted.
- **On-device config clone** (relay/input names, input modes, schedules, on-device scripts, webhooks, KVS) — **Stage 3** (LAN-RPC). Stage 2 transplants HA-side identity only.
- **Name-Sync / Fleet-Map / Resilience-Check** — **Stage 1**. Stage 2 preserves names, does not sync them.
- **Cloud writes** — Cloud API is read-only; never used to configure hardware.
- **`delete_new_entry` as an option** — removed; new-entry removal is mandatory in native merge, not user-selectable.

**Dependencies:**
- Builds on the shipped cloud variant — reuses its registry-rewrite pattern and the (generalised, case-insensitive) ghost-purge helper.
- Native variant requires the native core `shelly` integration installed and the new unit already added to native (supplies `host_new`).
- Stage 1's MAC-matching helper (`cloud_id.upper() == format_mac(native_conn).upper()`), if built, is reused by the dispatcher's same-physical-device detection **and** the native-twin warning (critic M6); until then this stage implements that comparison inline.
- aioshelly already a manifest requirement (`manifest.json`), available for `format_mac`/model normalisation.

**Relevant files (absolute):**
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/services/replace_device.py` (→ dispatcher + `_replace_cloud_device`, + native-twin warning)
- NEW `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/services/replace_native.py`
- NEW (optional) `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/services/replace_common.py`
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/__init__.py` (schema; no new field)
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/services.yaml` (drop integration filter; validate in code; no `delete_new_entry`)
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/const.py` (`NATIVE_SHELLY_DOMAIN`)
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/translations/{en,de}.json`, `strings.json`

---

## Changes made vs critic

**Accepted and incorporated:**
- **B1** — native rewrite filtered to `ent.platform == "shelly"` + token-prefix assertion; non-shelly entities on the device left untouched. (§3, §5, §6 test.)
- **B2 + B3** — `delete_new_entry` option **dropped**; new native entry removed **unconditionally**, sequenced **before** the old entry's `unique_id` rewrite so `(domain, new_mac)` is never duplicated and no duplicate control device survives a restart. (§2, §3 apply steps 2–3, §7.)
- **M1 + M2** — two independent, never-crossed tokens: `connection_mac` (`format_mac`, connections only) vs `uid_token`/`subdev_token` (`split("-",1)[0]` from real rows); `subdev_token` derived separately from `uid_token`; round-trip tests. (§3, §6, §7.)
- **M3** — ghost purge made **case-insensitive** on the derived token; ghost-removal test added. (§3 step 8, §6.)
- **M4 + m10** — `host_new` captured **at plan time**; **abort** if undiscoverable rather than "warn and proceed onto a dead host." (§1 crit #9, §3, §5, §6 test.)
- **M5** — upstream repair-flow PR **repositioned as the primary deliverable**; native mutation shipped behind an explicit experimental gate + native-scheme compat check. (§7.)
- **M6** — native-twin detection added to the **cloud** path; dry-run/done notifications warn that only the cloud overlay was migrated. The dead "refuse GBLE in native" guard was removed. (§1 crit #8, §3, §5.)
- **M11** — runtime native-scheme guard documented as the *real* safety net (not the unit tests). (§3, §6, §7.)
- **m7** — selectors left unfiltered with hard in-code validation naming each device's integration; no reliance on a `manufacturer` selector filter. (§3.)
- **m8** — cloud name rewrite kept strictly inside `_replace_cloud_device`; native path never touches `name`. (§2, §3.)
- **m9** — atomicity reframed as atomic-ordered (not transactional); recovery breadcrumb persisted in a restart-surviving notification. (§3, §7.)

**Rejected / qualified:**
- **None rejected outright.** One nuance recorded: the critic's own B1 analysis first proposed, then walked back, the cross-platform same-device collision (cloud and native live on *separate* HA devices, so entities never mix on one device). I adopt the critic's **re-scoped** B1 (filter to `platform == "shelly"` to protect user-attached non-shelly entities) and explicitly do **not** add same-device cross-platform deduplication, because it cannot occur given the verified identifier schemes (`(DOMAIN, id)` vs `(CONNECTION_NETWORK_MAC, mac)` / `("shelly", …)` are distinct HA devices). This is a scoping clarification, not a rejection of the fix.


<details><summary>Kritiker-Review (Rohfassung)</summary>


I have everything I need. The conflict check only looks at *live* entities (`async_get_entity_id`), not `deleted_entities` — so the ghost-purge is about a different concern (stable entity_id reuse), not the rename collision. Note also there is NO platform-scoping difference between cloud and native that would protect against cross-platform collisions here. Now I'll write the adversarial review.

---

# ADVERSARIAL REVIEW — Stage 2 Unified `replace_device` (cloud + native)

I read the actual code (`replace_device.py`, `entities/base.py`, `__init__.py`, `coordinator.py`, `services.yaml`) and verified the HA entity-registry rename semantics in source. Findings below are evidence-based and ordered by severity.

---

## BLOCKERS

### B1. `async_update_entity(new_unique_id=...)` is platform-scoped — your native rewrite will collide and abort mid-apply.
**Evidence:** `entity_registry.py:877-886` — the conflict check is `async_get_entity_id(old.domain, old.platform, new_unique_id)`. The collision space is `(domain, platform, unique_id)`. For native entities `platform == "shelly"`. Your plan deletes the *new* device's entities in apply-step 2 to "free" the `{new_mac}-…` uids, exactly like the cloud variant. Fine. **But** the deeper problem: the cloud variant works on `platform == "shelly_cloud_diy"` rows; the native variant works on `platform == "shelly"` rows. Your dispatcher refactor moves shared validation up, but the *rewrite loop is per-platform* and you never state that the native rewrite must read/operate on entities whose `.platform == "shelly"`. `er.async_entries_for_device` returns entities across **all** platforms on that device. A device that is BOTH cloud-overlaid and native-controlled (the operator's normal topology — cloud as redundant secondary path) will have entities from *both* platforms on... no — actually they're on different HA devices (cloud has its own `(DOMAIN,id)` device, native has its own). So same-device cross-platform mixing won't happen. **Re-scoped finding:** the real bug is that `_build_native_plan` must filter `er.async_entries_for_device(...)` to `ent.platform == "shelly"` before rewriting, or it will try to rewrite a stray helper/template entity a user manually attached to the native device and corrupt it. **Fix:** filter rewrites to `ent.platform == NATIVE_SHELLY_DOMAIN`; assert every rewritten uid matches `f"{old_token}-"`.
Severity: **BLOCKER** (silent corruption of non-shelly entities on the device).

### B2. You cannot reliably rewrite the native config entry's `unique_id` to the new MAC and then reload onto the *old* entry — the *new* device's entry is the one bound to the live socket, and aioshelly identity is set at runtime, not from `entry.unique_id`.
**Problem:** The plan's success criterion #6 assumes `MacAddressMismatchError` is raised by comparing *live device MAC* vs *`entry.unique_id`*. That is half-true: aioshelly/native compares the live MAC against the entry's stored identity during setup. You rewrite `old_entry.unique_id → new_mac` and `old_entry.data[CONF_HOST] → new_host`, then reload `old_entry`. But the **new** device was already added to native as its **own** entry with `unique_id == new_mac`. After you rewrite `old_entry.unique_id = new_mac`, you now have **two config entries with the same `unique_id`** for the same domain until step 8 removes the new one — and step 8 is *gated behind a flag and defaults to "leave it disabled/unloaded and warn"*. HA does not permit two entries with the same `(domain, unique_id)`; `async_update_entry(unique_id=...)` does not dedupe, but discovery/`_abort_if_unique_id_configured` and the integration's own setup will fight. **Fix:** removal of the new entry must be **mandatory and unconditional** in the native path (it is not optional — its hardware identity is being merged), sequenced *before* the old-entry unique_id rewrite. Default-leaving-it is a guaranteed duplicate-unique_id corruption.
Severity: **BLOCKER.**

### B3. Default-leave-the-new-entry behaviour produces a duplicate CONTROL device — a direct LOCAL-FIRST / duplicate-control-entity violation.
The operator's hard constraint forbids duplicate control entities. If `delete_new_entry` defaults to `False` and the new native entry is "left disabled/unloaded," then on the next HA restart, or if the user re-enables it, the new entry re-creates a *full set of native control entities* for the new MAC — duplicating the very entities you just transplanted. Now there are two native paths to the same physical relay, with split history. **Fix:** as B2 — the new native entry must be removed unconditionally in the native merge path. There is no coherent "keep it" mode.
Severity: **BLOCKER** (violates the operator's #1 constraint).

---

## MAJOR

### M1. `format_mac` normalises to colon-lowercase, but native entity uids use UPPER-no-colon — your "derive the token from real rows" and your "normalise with `format_mac`" instructions contradict each other.
**Evidence:** brief states native uid = `"5432044E9768-switch:0"` (upper, no colons); `dr.format_mac` returns `54:32:04:4e:97:68` (lower, colons). The plan says both "normalise with `format_mac`" AND "derive the exact casing/format by reading actual rows." These produce different strings. If any code path mixes a `format_mac` MAC into a uid-token comparison or rewrite, the `startswith(f"{old_token}-")` filter silently matches nothing → `rewrites == []` → either a false "nothing to do" abort or, worse, a half-applied plan. **Fix:** define two explicit values: `connection_mac = format_mac(raw)` (only for `dr.CONNECTION_NETWORK_MAC` tuples) and `uid_token = old_entity.unique_id.split("-", 1)[0]` (for uid + sub-device-identifier rewrites). Never cross them. Add a test asserting `uid_token` round-trips.
Severity: **MAJOR.**

### M2. Sub-device identifier format assumption is wrong for some native Shelly devices.
The brief says sub-devices are `("shelly", "{mac}-{key}")`. But native uses the **lowercase, no-colon** mac in *identifiers* for sub-devices in current code, while *entity uids* use **uppercase**. The plan conflates them ("`("shelly", f"{old_mac}-{key}")`" using a single `old_mac`). The casing differs between the identifier namespace and the uid namespace in the native integration, and has changed across native refactors. **Fix:** derive the sub-device token independently by reading the actual existing sub-device `identifiers` of the old device (split on first `-`), exactly as M1 prescribes for uids — never assume a shared `old_mac` string serves both.
Severity: **MAJOR** (sub-device rewrites silently no-op → orphaned sub-devices after reload).

### M3. No reverse mapping for the `deleted_entities` ghost collision — the purge helper is being asked to do something it can't.
**Evidence:** `entity_registry.py:877-886` checks only **live** entities. `deleted_entities` does NOT block `new_unique_id` rewrite (it's a separate dict). So the plan's claim that purging ghosts "frees the uids" is **false for the rename collision** — ghosts never blocked it. What ghosts actually do: when the entry reloads and the integration re-registers, `async_get_or_create` may resurrect a *deleted* entity row and assign a `_2` suffixed entity_id, breaking entity_id stability. The cloud helper `_purge_deleted_for_id` matches by `shelly_id in unique_id` substring — for native, a MAC substring match (`"5432044E9768" in uid`) is fine, **but** a lowercased/uppercased mismatch (M1) means the purge silently matches zero ghosts. **Fix:** match ghosts case-insensitively on the derived `uid_token`; add a test that seeds a deleted-entity ghost for the new MAC and asserts it's gone post-apply.
Severity: **MAJOR.**

### M4. Reload of a native entry you have unloaded does not guarantee aioshelly reconnects to the *new* host before the registry adopt — and you removed the new entry's host knowledge.
Apply order: unload both → delete new entry's rows → (B2/B3: remove new entry) → rewrite old entry host to `new_host`. But `new_host` is sourced from "the new device's own native entry" — which you are removing. If you remove the new entry **before** capturing `new_host`, it's gone. The plan's ordering puts host-rewrite at step 3 and new-entry-removal at step 8, so it's *recoverable*, but you must explicitly snapshot `new_host` into the plan dataclass during `_build_native_plan` (read-only phase), not during apply. Also: native gen2 may use a configured host that's a hostname, not the discovered IP; reloading may stall on DNS. **Fix:** capture `new_host` at plan time into `NativeReplacePlan.host_new`; if absent, abort with guidance rather than "leave unchanged and warn" (leaving the *old dead* host means the reloaded entry connects to nothing → permanent setup-retry loop on a control entry).
Severity: **MAJOR.**

### M5. Writing into another integration's config entry is a near-certain HACS/Core review rejection, and you're shipping it as a custom service anyway.
The plan acknowledges this but underweights it. Calling `hass.config_entries.async_update_entry(old_native_entry, unique_id=..., data=...)` and `async_remove(new_native_entry)` on the **`shelly`** domain from **your** integration is reaching across integration boundaries to mutate state you don't own. This is exactly what HA reviewers reject ("an integration must not modify another integration's entries"). For HACS *default store* it may pass (less strict), but it is a reputational/maintenance landmine: any native refactor (entry schema, `CONF_HOST` key rename, multi-host gen2 with `CONF_HOST` becoming a list, BLE-bridge sub-entries) breaks you with data corruption, not a clean error. **Fix:** make the upstream repair-flow PR the *primary* deliverable and ship the native variant behind a loud "experimental / may break on shelly updates" gate with a hard version-pin check on the native integration, or don't ship native mutation at all until upstream lands. Do not present this as a co-equal supported path.
Severity: **MAJOR** (sustainability + review).

### M6. GBLE / BLU refusal logic is underspecified and can misroute.
The plan says "Native variant must skip/refuse a device whose only identity is GBLE." But classification is by registry identifiers, and a GBLE device in *your* integration has identifier `(DOMAIN, id)` → classifies as cloud → cloud path. So how would a GBLE ever reach the native path? It can't — meaning the "refuse GBLE in native" guard is dead code, and the real risk is the inverse: a user picks a BLU device (cloud, no LAN twin) as `old` and a real native device as `new` → mixed → refused. Good. But what about cloud `old` (a normal Gen2) + cloud `new` where one is actually controlled natively and only *visible* via cloud? The cloud rewrite proceeds and transplants **cloud overlay entities** — harmless to control (cloud entities aren't the control path), but it creates the illusion the user "replaced" the device when their *native* control entities are untouched and still point at the dead MAC. **Fix:** in the cloud path, detect (via `cloud_id.upper() == any native device MAC`) that a native twin exists and warn in the dry-run: "This device is also controlled natively; this service only moved the cloud overlay — to replace the controlling device, run the native replace too."
Severity: **MAJOR** (false "it just works" — the operator's actual control entities are not migrated).

---

## MINOR

### m7. `services.yaml` widening loses the integration filter → users will select non-Shelly devices and hit a runtime error instead of a filtered picker.
Removing `integration: shelly_cloud_diy` and "filter by `manufacturer: Shelly` if the selector supports it" — the device selector's `manufacturer` filter is unreliable/version-dependent and won't distinguish your cloud devices from native ones anyway. **Fix:** keep two filtered selectors is impossible in one field; instead use `selector.device` with `integration:` accepting a list is not supported either. Pragmatic fix: leave unfiltered, validate hard in code with a clear `ServiceValidationError` naming which integration each device belongs to. Accept the worse picker UX.
Severity: MINOR.

### m8. `name` rewrite logic copied from cloud is meaningless for native.
Cloud variant rewrites `(old_id)` → `(new_id)` inside the device name because *your* integration bakes the id into the name (`base.py:113`). Native does **not** put the MAC in the device name. The native path must **not** touch `name` at all (the plan says so in §2, good) — but make sure the shared dispatcher doesn't accidentally run the cloud name-rewrite. Keep that logic strictly inside `_replace_cloud_device`.
Severity: MINOR.

### m9. Atomicity claim is overstated; `async_schedule_save` is debounced.
Registry writes hit `.storage` via debounced `async_schedule_save` (confirmed in source). If HA crashes between rewrites and the debounce flush, you get a torn state regardless of your try/except. The "atomic-ish" framing is honest but the partial-failure notification can't recover a crash mid-loop. **Fix:** before any native apply, persist a recovery breadcrumb (the full plan as a `persistent_notification` with `notification_id` that survives restart, or a `.storage` sidecar) so a crashed apply is at least diagnosable. Acknowledge this isn't transactional.
Severity: MINOR.

### m10. No reload-loop guard if the new host is wrong → repeated setup retries on a control entry.
If `host_new` is stale/wrong, the reloaded native entry enters `setup_retry`, and aioshelly will hammer the wrong IP. Not catastrophic but it's a control-path degradation you introduced. Covered by M4's fix (abort if no good host).
Severity: MINOR.

### m11. Test plan uses `pytest-homeassistant-custom-component` against native `shelly` internals you don't control — tests will be brittle against the very thing you're trying to pin.
The "classification matrix" and "native multi-channel" tests must *fabricate* native rows by hand (seed `er`/`dr` with `("shelly", ...)` identifiers and `"{MAC}-{key}"` uids). That's fine, but it tests *your assumptions about native*, not native's actual current scheme — so a native refactor passes your tests while corrupting prod. **Fix:** add one integration-level smoke check that reads the *installed* native integration's actual uid scheme at runtime (the "derive from real rows" guard) and aborts if it doesn't match the `<token>-<key>` shape — and document that this guard, not the unit tests, is the real safety net.
Severity: MINOR.

---

## VERDICT: **RETHINK** (native mutation path) / REVISE (cloud-side guards)

The cloud variant is sound and shipped. The native variant as specified will, in its **default** configuration, create a duplicate native control entry (B2/B3) — a direct violation of the operator's hard local-first constraint — and rests on MAC-casing assumptions that silently no-op (M1/M2). Cross-integration entry mutation (M5) is the wrong long-term home for this. The plan's own §7 nods at all of this but the §3 apply sequence and defaults don't honour the nods.

## PRIORITISED MUST-FIX

1. **B2+B3:** Native merge must **unconditionally remove the new config entry** (no flag, no "leave disabled" default). Sequence: snapshot `new_host` at plan time → unload both → remove new entry + its rows → rewrite old entry unique_id+host → rewrite device/sub-device/entity tokens → reload old entry. Drop the `delete_new_entry` option entirely.
2. **M1+M2:** Derive **two independent tokens** from real rows — `connection_mac = format_mac(...)` for `CONNECTION_NETWORK_MAC` tuples only; `uid_token`/`subdev_token` via `split("-",1)[0]` of actual existing rows. Never cross-use `format_mac` output in uid/identifier space. Test round-trip.
3. **B1:** Filter the native rewrite to `ent.platform == "shelly"`; assert each rewritten uid starts with the derived token; abort cleanly if the scheme doesn't match (M11 guard).
4. **M4:** Make a valid `host_new` mandatory; abort (don't "warn and proceed") if undiscoverable — a reloaded control entry pointed at a dead host is a control-path regression.
5. **M5:** Reposition the **upstream core-`shelly` repair-flow PR as the primary path**; ship native mutation only behind an explicit experimental gate + native-version compatibility check, or defer it until upstream lands.
6. **M6:** In the **cloud** dry-run, detect a native twin by MAC and warn that only the cloud overlay was migrated — kill the false "device replaced" implication for natively-controlled units.
7. **M3:** Make ghost-purge case-insensitive on the derived token; test ghost removal.

Until 1–4 are in, do **not** run the native apply on the operator's prod HA — dry-run only, and verify the derived tokens print exactly as the native rows hold them.


</details>



---

# Stufe 3 — On-device config clone (LAN-RPC) for resilience

## Finaler (gehärteter) Plan

All critic claims are verified against the source. `_resolve_entry` does depend on the live config entry, and `_shelly_id` works purely on the device registry. I have everything needed to write the final plan.

# STAGE 3 — On-device config clone (LAN-RPC) for resilience — FINAL PLAN

Implementation plan for `shelly-cloud-diy-ha` (domain `shelly_cloud_diy`), revised to incorporate the adversarial review. Every load-bearing claim below was re-verified against source this session (aioshelly is `MODEL_NAMES`-only at `entities/descriptions.py:28`; `CONF_KNOWN_DEVICES` at `const.py:57` is a dead constant never populated; `entities/base.py` `DeviceInfo` has no `connections`/`configuration_url`; `replace_device.py:129` compares *registry* models with a `force` override; `_shelly_id`/`_resolve_entry` at `replace_device.py:49,57`).

---

## 0. Why this stage exists (framing)

Stage 2 (`replace_device`) transplants the *Home Assistant* identity (entity IDs, history, automations) from a dead Shelly onto a new one. It does **not** move anything that lives *on the device*: relay/input names, input modes, schedules, on-device scripts, webhooks, KVS. That on-device logic is precisely what gives the operator true resilience — a schedule or script running on the Shelly itself keeps working when **both** the internet **and** Home Assistant are down. After a hardware swap the new unit is factory-blank in all those respects. Stage 3 closes that gap by cloning on-device configuration old → new **over the LAN, via local RPC**, never the cloud — the Cloud Control API is read-only for config (verified: `api/cloud_control.py` exposes only status getters + `*/control` momentary commands; no `*.SetConfig`).

This stage is structurally incapable of putting the cloud in the control path, and is deliberately scoped narrow (see §5/§8): the heavyweight, device-management-flavoured categories (scripts, KVS) ship **off by default**.

---

## 1. Goal & success criteria

**Goal:** After a same-model/same-profile hardware swap, reproduce the dead device's *on-device* configuration on the replacement unit via local RPC (Gen2/Gen3 JSON-RPC over `POST /rpc`) or local HTTP `/settings` (Gen1), so autonomous on-device behaviour (component names/modes, schedules, scripts, webhooks, KVS) survives the swap and keeps running with no internet and no HA — **and never targets the wrong physical device**.

**Success criteria (observable, testable):**

1. **S1 — Dry-run plan.** `shelly_cloud_diy.clone_config` with `dry_run: true` posts a `persistent_notification` listing, per category, exactly what *would* be written (per-component key diffs, N schedules with their timespecs, K scripts incl. enable state and byte size, W webhooks **with their URLs**, V KVS keys), plus the explicit **NON-transferable** block, with **zero** RPC writes (assertable: target `Shelly.GetConfig` hash unchanged after a dry run).
2. **S2 — Real clone, Gen2+.** After a real run between two physical same-model/same-profile Gen2/Gen3 units, target `Schedule.List`, `Script.List`+`Script.GetCode`, `Webhook.List`, `KVS.GetMany`, and per-component `*.GetConfig` (names + input modes) match the source modulo documented non-transferables. The fixture **includes a script larger than one PutCode chunk** (see M3 fix in §3.3).
3. **S3 — Resilience proof.** A schedule cloned onto the target fires at its configured time with HA stopped and the WAN link physically pulled.
4. **S4 — Local-only.** With `auth_key` deliberately invalidated (entry in reauth, coordinator possibly **absent**), `clone_config` still completes given valid LAN hosts — because resolution is **registry-only** and never touches `coordinator.devices` on the write path (§3.5, M1 fix).
5. **S5 — No control-path regression.** A clone never touches the HA entity/device registries, never calls `coordinator.send_command`, never triggers `_refresh_device_names`, and never changes `enabled_devices` or any native-`shelly`-owned entity. Registry byte-identical before/after.
6. **S6 — Safe re-run (honest idempotency).** Re-running the same clone produces **no duplicates** (scripts matched by name → clean replace; schedules/webhooks → skip if an identical (timespec/spec) tuple already exists; KVS upsert by key) and **never auto-deletes** operator-added on-device logic. This is "no duplicates / non-destructive," explicitly **not** "byte-identical re-derivation."
7. **S7 — Right-device gate (NEW, blocker-level).** Before any write, `clone_config` fetches `Shelly.GetDeviceInfo` from `target_host`, derives its MAC, and **hard-fails with `ServiceValidationError`** unless `mac.lower() == target_device_id.lower()`. No `force` override exists for this gate. Same check on `source_host` when live.

---

## 2. User-facing behaviour

The operator runs **one new service**: `shelly_cloud_diy.clone_config`. Intended workflow: **Stage 2 then Stage 3** — `replace_device` to move HA identity, then `clone_config` to move on-device logic. Stage 3 also works standalone.

`target_host` is the **primary, required identifier**. The `target` device selector exists only to (a) name the result notification and (b) supply the `device_id` (= MAC) that the S7 gate asserts against the host. The operator is told plainly in the field description: *you must type the unit's current LAN IP; the selected device only fixes which MAC is allowed to answer there.*

Fields (mirrors the `replace_device` descriptor/registration style):

- `target` *(device selector, `integration: shelly_cloud_diy`, required)* — the new device; supplies the MAC for the S7 identity gate.
- `target_host` *(text, required)* — LAN IP/hostname of the new unit. Primary identifier.
- `source` *(device selector, optional)* — the old/dead device, for the model/profile guard and snapshot lookup.
- `source_host` *(text, optional)* — LAN IP/hostname of the old unit; if omitted, the snapshot path is used (§3.4).
- `categories` *(select, `multiple: true`, default `["component_config", "schedules", "webhooks"]`)* — full option set `component_config | schedules | scripts | webhooks | kvs`. **`scripts` and `kvs` are OFF by default** (M5/M6): they are heavyweight device-management features and the likeliest secret carriers.
- `replace_existing` *(boolean, default `false`)* — when false (default), schedules/webhooks are added non-destructively (skip identical, never delete). When true, the target's schedules/webhooks in the cloned categories are wiped and recreated; this is **loudly logged** and called out in the result notification.
- `dry_run` *(boolean, default `false`)*.
- `password` *(text, optional)* — local RPC/HTTP digest password if units are auth-protected. Used transiently; **never persisted, never logged** (§7).

Operator experience:

- **Dry run:** `persistent_notification` "Shelly Cloud DIY — clone config (dry run)" with a fenced plan: per-component key diffs, schedule timespecs, script names+sizes+enable, **webhook URLs verbatim** (so the operator spots a stale `http://old-ha:8123/...` before it is copied — m6), KVS keys, and a bold **"Will NOT copy: MAC, device id, all passwords, cover calibration, WiFi/AP/cloud credentials, Bluetooth pairing."** No writes.
- **Real run:** "Shelly Cloud DIY — config cloned" summarising **per-category, per-component** success/skip-with-reason ("switch:0 names applied; cover calibration NOT copied — recalibrate"; "1 script skipped — target firmware lacks `Script` support"; "2 schedules skipped — already present").
- **Failure:** `ServiceValidationError` for caller errors (MAC mismatch at host → S7, model/profile mismatch, missing/unreachable `target_host`, BLU device); `HomeAssistantError` for mid-run failures with a precise "what succeeded / what failed / safe to re-run" message.

No entities, sensors, or dashboard surface — purely an operator-invoked tool, consistent with `replace_device` and the overlay-only posture.

---

## 3. Technical design

### 3.1 New & modified files

**NEW** `services/clone_config.py` — handler `async def async_handle_clone_config(hass, call)`, modelled on `replace_device.py:async_handle_replace_device` (same `partial`-bound registration, same `ServiceValidationError`/`HomeAssistantError` discipline, same notification pattern). Orchestration + dataclasses below.

**NEW** `rpc/local_rpc.py` — the LAN client. **Primary path is a thin direct JSON-RPC 2.0 client** over `POST http://{host}/rpc` with HTTP **digest** auth for protected devices, reusing the `aiohttp` + `validate_*` patterns in `utils/http.py`. *(Revised per B1: aioshelly's `RpcDevice` is dropped from the design entirely — it is a stateful, WebSocket-first wrapper with a `create()/initialize()` lifecycle the original plan invented an API for, and it has never been instantiated in this codebase. Firmware JSON-RPC method names are a harder, more stable contract than the Python wrapper, exactly as the original §7 argued. aioshelly stays exactly where it is: `MODEL_NAMES` only.)* Gen1 backend: thin async HTTP client for `GET/POST http://{host}/settings...` (Gen1 has no RPC).

**NEW** `rpc/__init__.py` — package marker + re-exports.

**NEW** `rpc/clone_plan.py` — pure, side-effect-free planner (diff source-config → target-write-ops), unit-testable without a device.

**NEW** `services/_common.py` — factor `_shelly_id` / `_resolve_entry` out of `replace_device.py` so both services share one implementation (small refactor; updates `replace_device.py` imports).

**NEW** `utils/local_host.py` — `validate_local_host(raw) -> str`: inverse of `validate_gateway_url`. Rejects loopback/unspecified **and the HA host's own LAN IP** (this is *new code* — `validate_gateway_url` blocks only loopback/unspecified today, verified `utils/http.py`; the SSRF guard must add the HA-host check itself — m2). RFC1918 preference with a discoverable override flag, since some operators run Shellies on routed/non-1918 VLANs (m2).

**MODIFIED** `__init__.py` — in `_register_services`, add a block guarded by `if not hass.services.has_service(DOMAIN, "clone_config")`, registering `partial(async_handle_clone_config, hass)`. The `categories` field is validated as **`vol.All(cv.ensure_list, [vol.In(CLONE_CATEGORIES)])`** (a list validator, **not** `cv.string` — the existing schema uses `cv.string` for the `replace_device` device fields; mismatching the multi-select here is the #1 hassfest failure — m1). Import `async_handle_clone_config` next to the existing `replace_device` import.

**MODIFIED** `services.yaml` — add a `clone_config:` descriptor: `device` selectors for `target`/`source`, a `select` selector with `multiple: true` + `options:` for `categories`, booleans for `replace_existing`/`dry_run`, text for hosts/`password`.

**MODIFIED** `strings.json` + `translations/{en,de}.json` — `services.clone_config.*` strings (every user-visible string — CLAUDE.md hygiene rule).

**MODIFIED** `manifest.json` — no new requirement (no new dependency: the direct `/rpc` client uses `aiohttp`). Version bumped to `0.6.0` at release time only, per CLAUDE.md release flow.

### 3.2 Data structures

```python
CLONE_CATEGORIES = ("component_config", "schedules", "scripts", "webhooks", "kvs")
DEFAULT_CATEGORIES = ("component_config", "schedules", "webhooks")  # scripts/kvs OFF by default

@dataclass(frozen=True)
class CloneSource:
    gen: str                              # "G1" | "G2" | "GBLE" (const.device_gen)
    mac: str                              # from Shelly.GetDeviceInfo (identity gate)
    model: str
    profile: str | None                  # e.g. "cover"/"switch" for 2PM-class devices
    component_configs: dict[str, dict]    # {"switch:0": {...}, "input:0": {...}, "sys": {...}}
    schedules: list[dict]                 # Schedule.List -> "jobs"
    scripts: list[ScriptBlob]
    webhooks: list[dict]                  # Webhook.List -> "hooks"
    kvs: dict[str, Any]
    raw_get_config: dict                  # full Shelly.GetConfig (audit/fallback)

@dataclass(frozen=True)
class ScriptBlob:
    name: str
    enable: bool
    code: str

@dataclass(frozen=True)
class WriteOp:
    category: str                         # component_config|schedule|script|webhook|kvs
    method: str                           # e.g. "Switch.SetConfig"
    params: dict
    label: str                            # human line for the dry-run plan
```

### 3.3 RPC method map (concrete data flow)

**Identity gate first (S7, before any read of source config or any write):**
1. `Shelly.GetDeviceInfo` on `target_host` → `mac`, `model`, `app`/profile, `gen`. **Hard-fail** unless `mac.lower() == target_device_id.lower()`. No override.
2. If `source_host` is live: same call; record `source` mac/model/profile (used only for the model/profile guard, never asserted equal to target).

**Read (source) — Gen2/Gen3:**
- `Shelly.GetConfig` → full config; slice every component with a name/mode (`switch:N`, `input:N`, `cover:N`, `light:N`, `sys`; `wifi` **roaming flags only, never creds**).
- `Schedule.List` → `jobs`.
- `Script.List` → ids/names/enable; per id `Script.GetCode` → code.
- `Webhook.List` → `hooks`; `Webhook.ListSupported` on the **target** to validate event names before creating.
- `KVS.GetMany` (or `KVS.List` then `KVS.Get`).

**Write (target) — Gen2/Gen3 — all writes schema-safe by construction (M4 fix):**
For each component, the planner reads the **target's** current `*.GetConfig`, then writes back **only the intersection** of (keys present in the target's config) ∩ (keys we intend to transfer) ∩ (not on the blacklist), copying the *source's values*. This guarantees we never send a key the target firmware rejects (Gen2 `*.SetConfig` is schema-strict; an unknown/derived key fails the whole component write) and structurally excludes calibration/mac/derived fields without needing a perfect static whitelist. Each `*.SetConfig` response is checked; per-component success/fail goes into the result notification.
- Components: `Switch.SetConfig` / `Input.SetConfig` / `Cover.SetConfig` / `Light.SetConfig` / `Sys.SetConfig` (intersection-filtered as above).
- **Schedules (non-destructive default — M2 fix):** `Schedule.List` the target; **skip** creating any job whose `(timespec, calls)` tuple already exists; otherwise `Schedule.Create`. **Never auto-`Schedule.Delete`.** Only when `replace_existing: true` does the service first delete the target's schedules in scope and recreate — loudly logged. The plan is honest that schedule `id`s are server-assigned and not stable across devices, so dedup is best-effort structural matching, not a stable-key merge.
- **Scripts (correct ordering — M3 fix):** for each source script — `Script.Stop` if running → if a script of that `name` exists on target, `Script.Delete` it (PutCode *appends*; reusing an id would double/corrupt the body) → `Script.Create {name}` → loop `Script.PutCode {id, code: chunk, append: <bool>}` with `append:false` on the first chunk and `append:true` thereafter, **chunk ≤ 1024 bytes** (conservative; firmware PutCode body limit varies ~1–4 KB) → `Script.SetConfig {enable}` → `Script.Start` only if the source was enabled.
- **Webhooks (non-destructive default — M2 fix):** validate each hook's events against the target's `Webhook.ListSupported`; **skip** hooks whose spec already exists; otherwise `Webhook.Create`. Never auto-delete unless `replace_existing: true`. Webhook URLs are surfaced verbatim in the dry-run (m6).
- **KVS:** `KVS.Set` per key (natural upsert).

**Gen1:** read `GET /settings`, `/settings/relay/N`, `/settings/actions`; write the transferable subset via `POST /settings/relay/N?name=...` etc. Gen1 has **no** scripts/KVS/`Schedule.*`. **Gen1 clones names + relay settings + actions(webhooks) only; schedules are explicitly NOT transferred** and are shown as a capability gap in the dry-run. The plan does not claim Gen1 schedule parity (m3 fix — Gen1 `schedule_rules` format is not mappable to Gen2 jobs).

### 3.4 Dead-source handling (snapshot, scope-reduced — M6 fix)

A dead source has no `source_host`. Layered fallbacks:

1. **Cloud name seed (best-effort, decoupled):** *if* a coordinator is present, component **names** can be seeded from cloud status. This is strictly optional and guarded: `coordinator = hass.data[DOMAIN].get(entry.entry_id)` → if `None` (e.g. entry in reauth, S4), skip silently. Everything except names is marked "unavailable from cloud — source unreachable." The cloud returns no schedules/scripts/webhooks/KVS.

2. **Local snapshot cache (opt-in, metadata-first):** a sub-action `action: snapshot` (run while the old device is alive) persists a `CloneSource` to `.storage` via `Store`. Per M6:
   - **By default the snapshot stores metadata only** — component configs (intersection-relevant keys), schedule specs, script **names + enable state + sizes**, webhook specs, KVS **keys**. It does **not** store script bodies or KVS *values*, because those routinely contain secrets (API tokens, MQTT creds, webhook URLs with keys) that no `_strip_secrets()` can reliably detect by key name.
   - Storing bodies/values requires an explicit `include_secrets: true` with a loud warning in the notification and log; even then `_strip_secrets` strips known credential keys (`wifi.*.pass`, etc.) first.
   - `Store.async_save` is awaited and the operator is told to **wait for the result notification before powering off** the old unit (Store writes are debounced; the awaited save flushes).
   - A sub-action `action: forget` deletes a snapshot. No unbounded accumulation.
   - **Reconsidered but kept (see "Changes made vs critic"):** the snapshot survives as metadata-first opt-in rather than being dropped, because it is the *only* path to recover even schedule/script structure from a truly dead unit, which is the stage's whole resilience premise.

### 3.5 HA APIs used

- Device resolution: **registry-only** (M1 fix). Use `_shelly_id(device)` from `services/_common.py` to map selected HA devices → Shelly `device_id` (= MAC). The clone write path **never reads `coordinator.devices`** and does not require a live coordinator — so S4/S5 actually hold. `_resolve_entry` is still used to locate the owning `ConfigEntry` for the optional name-seed lookup only, and that whole branch is `None`-guarded.
- Session: `aiohttp_client.async_get_clientsession(hass)` (as in `__init__.py:60`, `historical.py`).
- Notifications: `persistent_notification.async_create`.
- Storage (snapshot): `homeassistant.helpers.storage.Store`.
- Exceptions: `ServiceValidationError` (caller error) / `HomeAssistantError` (runtime), per `replace_device.py` and CLAUDE.md.

---

## 4. Local-first compliance (the hard constraint)

Structurally incapable of putting the cloud in the control path:

1. **No cloud writes exist.** `api/cloud_control.py` has only `get_*` + `*_control` (verified). Stage 3 adds none. All writes go to `http://{target_host}/rpc` (Gen2) or `/settings` (Gen1) — point-to-point on the LAN.
2. **No control entities touched.** `clone_config` never calls `dev_reg.async_update_device`, never rewrites entity `unique_id`s, never touches `enabled_devices`, never calls `coordinator.send_command`, and never triggers `_refresh_device_names` (the registry-only resolution keeps the coordinator out of the path entirely — m4/M1). Native-`shelly` and cloud-overlay entities are untouched → S5 byte-identical.
3. **Cloud/internet DOWN behaviour.** Clone runs entirely over LAN. With WAN down, LAN RPC succeeds. With `auth_key` expired and the coordinator possibly absent, resolution is registry-only so the clone still completes (S4). The cloned on-device schedules/scripts run on the Shelly itself — that is the resilience the stage delivers.
4. **One-shot operator action, not a runtime path.** Nothing in the polling loop/coordinator/entity availability depends on it. If it fails, every existing control path (native local + cloud overlay) is unaffected.

---

## 5. Edge cases & failure modes

- **Wrong physical device at the host (BLOCKER — S7).** The model guard inherited from `replace_device.py:129` compares the two *selected registry* devices, **not** the device answering at `target_host`. DHCP churn can move an IP to an innocent Shelly. Therefore the **non-overridable** S7 MAC gate runs before any write: `Shelly.GetDeviceInfo.mac` at `target_host` must equal the selected `target` device_id. Same for `source_host` when live.
- **Model / profile mismatch (non-overridable — m5 fix).** Verify with live `Shelly.GetDeviceInfo` `model` **and** `profile`/`app` on both hosts. A `cover`-profile 2PM must not receive `switch:*` config. Unlike `replace_device`'s `force`-overridable model check (which is safe there because it is a pure prefix rewrite), here a profile mismatch causes physically wrong/unsafe config — so **this guard has no `force` override.** (If an escape hatch is ever needed it would be scoped per-category, not a blanket `force`.)
- **Source unreachable / dead.** No `source_host` → snapshot fallback (§3.4); if no snapshot, the plan shows only the cloud name-seed and flags the rest unrecoverable, then aborts categories that need device-only data (or proceeds names-only).
- **Target unreachable.** `target_host` required; connect failure → `ServiceValidationError("Target {host} not reachable on the LAN")`. Never silent no-op.
- **Auth-protected device.** HTTP digest (Gen2 `/rpc` and Gen1 `/settings`). Wrong/missing password → clear error. `password` used transiently, never persisted/logged (§7).
- **Multi-channel / sub-devices.** Iterate every `switch:N`/`input:N`/`cover:N`/`light:N` in source config; intersection-write each. Channel count match enforced by model/profile guard + per-component existence check on the target before write.
- **BLU / BLE (`gen == "GBLE"`).** Gateway-bridged BLU sensors have no local RPC. Detect via `const.device_gen` and **hard-reject**: "BLU/BLE devices have no on-device config to clone; nothing to do." Correct, not a limitation.
- **Firmware capability gaps.** Older Gen2 may lack `KVS.*`/`Script.*`/`Webhook.*`. Probe via `Shelly.GetComponents`/method errors and **skip with a reason** rather than failing the whole clone.
- **Idempotency / re-run (S6, honest).** Scripts matched by `name` → stop/delete/recreate (clean body). Schedules/webhooks → skip identical `(timespec, calls)` / spec tuples, **never auto-delete**; `replace_existing: true` for explicit wipe-and-recreate (loudly logged). KVS upsert by key. This is "no duplicates / non-destructive," not byte-identical re-derivation.
- **SetConfig schema strictness (M4).** Writes are target-`GetConfig`-intersected, so we only send keys the target already accepts; every response is checked and reported per component.
- **Stale webhook URLs (m6).** Cloned webhooks may carry absolute URLs pointing at the old HA/cloud. Not a control-path violation, but a silent breakage — surface every webhook URL verbatim in the dry-run so the operator can catch it.
- **Partial apply.** Writes are sequential, categories independent; on mid-run failure report exactly which `WriteOp`s succeeded; remedy is "re-run" (safe per S6).
- **`replace_device` MacAddressMismatchError (Stage 2, not 3).** N/A to Stage 3's firmware writes. Stage 3 runs after the target is a healthy HA device and tolerates running independently of Stage 2.

---

## 6. Testing & verification approach

- **Unit (no device):** test `clone_plan.py` against captured, sanitised `Shelly.GetConfig`/`Schedule.List`/`Script.List` fixtures. Assert WriteOps include transferables and **exclude** every non-transferable (MAC, calibration, passwords, WiFi). Assert the **intersection filter** drops keys not present in a given target-config fixture (M4). Assert idempotency yields zero ops against an already-matching target; assert non-destructive schedule/webhook skip and no auto-delete (M2). Assert the script fixture **larger than one 1024-byte chunk** plans `append:false` then `append:true` (M3). Assert BLU rejection, Gen1/Gen2 branching, and Gen1 schedule-gap reporting (m3).
- **Identity gate (S7):** unit-test the MAC comparison; integration-test that a host whose `GetDeviceInfo.mac` ≠ selected target device_id raises `ServiceValidationError` and performs **zero** writes.
- **Dry-run integration (S1):** run against two real units; assert notification text and pre/post `Shelly.GetConfig` hash unchanged.
- **Live clone (S2):** between two same-model/same-profile Gen2 units on Dirk's LAN; diff target vs source; re-run → no duplicates (S6).
- **Resilience (S3):** clone a schedule, stop HA, pull WAN, confirm it fires.
- **Local-only (S4):** invalidate `auth_key` (coordinator absent), run clone, confirm success via registry-only resolution.
- **Control-path regression (S5):** snapshot HA entity/device registries before/after; assert byte-identical (only achievable because the coordinator/name-seed path is decoupled — M1/m4).
- **Lint/translation:** `hassfest`-style check that `services.yaml` ↔ `strings.json`/`translations` are in sync, and that the `categories` `select multiple` selector matches the `vol.All(cv.ensure_list, [vol.In(...)])` schema (m1).

---

## 7. Risks & mitigations

- **No RPC client ever exercised in this codebase (B1).** The original plan asserted aioshelly's `RpcDevice` was "available" — it is not; only `MODEL_NAMES` is imported, no network I/O has ever run through aioshelly, and the invented `RpcDevice(session, ConnectionOptions)` → `call_rpc` shape is not its real lifecycle. *Mitigation:* primary path is a thin direct JSON-RPC 2.0 `POST /rpc` client (digest auth) modelled on `utils/http.py`; aioshelly stays `MODEL_NAMES`-only. Verify against a real device before claiming S1–S7.
- **Credential handling.** `password` never stored/logged; secret-scrub discipline (CLAUDE.md HARD RULE) before any push. **Never** clone WiFi/cloud creds. Snapshot is metadata-only by default; bodies/values require explicit `include_secrets: true` + `_strip_secrets()` of known credential keys (M6/§7).
- **SSRF / wrong host.** `validate_local_host` rejects loopback/unspecified **and the HA host's own LAN IP** (new code — `validate_gateway_url` does not do this today, m2); RFC1918 preference with discoverable override. The S7 MAC gate is the real backstop against writing to the wrong device.
- **Cover calibration / physically-specific config.** Hard-excluded by the blacklist and naturally by the intersection filter; operator told to recalibrate.
- **Overwriting a live device.** S7 MAC gate (non-overridable) + non-overridable model/profile guard + `dry_run` recommended in docs + per-component result notification + idempotent re-runs.
- **HACS-default / future Core review (M5).** A `cloud_polling`-classed overlay that writes firmware config — including scripts/KVS — over the LAN is unusual and overlaps the core `shelly` integration's device-management domain. *Mitigation:* it is a manual one-shot operator service (documented as such); `scripts`/`kvs` ship **off by default**; the heaviest device-management work (especially upstreaming) belongs in the core-shelly repair-flow path the project already plans (Stage 2's PR). English logs, proper exception types, full translations; no diagnostics/repairs platform.
- **Partial clone.** Per-category independence + idempotent re-run + precise "what succeeded/what failed" notification.

---

## 8. Out of scope & dependencies

**Out of scope:**
- Any cloud write (Cloud API is read-only — architectural, permanent).
- Cloning MAC, device id, passwords, cover calibration, WiFi/AP/cloud credentials, Bluetooth pairings.
- BLU/BLE on-device cloning (no local RPC; hard-rejected).
- Gen1 schedule transfer (no `Schedule.*` on Gen1; names/relay/actions only — m3).
- Moving HA identity / entity IDs / history / automations — that is **Stage 2** (`replace_device`).
- The upstream core-`shelly` repair-flow PR (Stage 2 deliverable).
- New entities/sensors/dashboard/diagnostics/repairs platforms.
- aioshelly `RpcDevice` usage of any kind (B1).

**Dependencies:**
- On Stage 2 (workflow only): documented happy path `replace_device` then `clone_config`; Stage 3 also runs standalone.
- On the live LAN: both units reachable (or a pre-failure snapshot for the source). Native core `shelly` is **not** a code dependency.
- No new library: direct `/rpc` client uses `aiohttp` (already in use); aioshelly unchanged.
- Small refactor: `_shelly_id`/`_resolve_entry` → `services/_common.py` shared by both services.

---

### Files touched (absolute)
- NEW `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/services/clone_config.py`
- NEW `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/rpc/local_rpc.py`
- NEW `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/rpc/__init__.py`
- NEW `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/rpc/clone_plan.py`
- NEW `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/services/_common.py`
- NEW `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/utils/local_host.py`
- MOD `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/__init__.py` (`_register_services`, import, list-validator schema for `categories`)
- MOD `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/services/replace_device.py` (import `_shelly_id`/`_resolve_entry` from `_common.py`)
- MOD `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/services.yaml`
- MOD `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/strings.json`
- MOD `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/translations/en.json` + `de.json`
- MOD (release-time only) `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/manifest.json` (version → 0.6.0)

---

## Changes made vs critic

**Accepted and incorporated (all blockers + majors + most minors):**
- **B1** — Dropped aioshelly `RpcDevice` entirely; primary path is a direct JSON-RPC `POST /rpc` client with digest auth. aioshelly stays `MODEL_NAMES`-only. The fabricated lifecycle/fallback-trigger claims removed.
- **B2 + B3 + S7 (new)** — Added a non-overridable MAC identity gate: `Shelly.GetDeviceInfo.mac` at `target_host` must equal the selected target device_id before any write. `target_host` is now the primary required identifier; the device selector exists only to fix the allowed MAC. Removed all implications that device_id "locates" a host (no host mapping exists; `CONF_KNOWN_DEVICES` confirmed a dead constant).
- **M1** — Clone path decoupled from the coordinator: registry-only resolution via `_shelly_id`; coordinator/name-seed access is `None`-guarded. S4/S5 now actually hold.
- **M2** — Schedule/webhook idempotency reframed as non-destructive-by-default (skip identical, never auto-delete); explicit `replace_existing` flag for wipe-and-recreate; S6 reworded to "no duplicates," not byte-identical.
- **M3** — Script clone ordering fixed: stop → delete-by-name → create → chunked PutCode (`append:false`/`true`, ≤1024 B) → enable → conditional start. S2 fixture must exceed one chunk.
- **M4** — `*.SetConfig` made schema-safe by intersecting against the target's live `*.GetConfig` keys; every write response validated and reported per component.
- **M5** — Scope shrunk: `scripts`/`kvs` ship **off by default**; documented as a manual one-shot tool; heavyweight/upstream device-management deferred to the core-shelly repair path.
- **M6** — Snapshot reduced to metadata-only by default (no script bodies/KVS values), `include_secrets` opt-in with `_strip_secrets`, awaited `Store.async_save` with a "wait before power-off" instruction, and a `forget` action for lifecycle.
- **m1** — `categories` validated as `vol.All(cv.ensure_list, [vol.In(...)])` with a `select multiple` selector, not `cv.string`.
- **m2** — `validate_local_host` explicitly adds the HA-host rejection (new code; not present in `validate_gateway_url`); RFC1918 override discoverable.
- **m3** — Gen1 schedule parity dropped; Gen1 = names/relay/actions only, shown as a capability gap.
- **m4** — `_refresh_device_names`/registry mutation kept off the clone path (consequence of M1), making S5 byte-identical achievable.
- **m5** — Model/profile guard made **non-overridable** for clone (unlike `replace_device`'s `force`).
- **m6** — Webhook URLs surfaced verbatim in the dry-run.

**Rejected, with justification:**
- **M6's stronger suggestion to "drop the snapshot feature for v1."** Rejected. The snapshot is the *only* mechanism that can recover even the structure of schedules/scripts from a truly dead unit, and dead-source recovery is the core resilience premise of the stage — dropping it would gut the use case. The critic's *legitimate* concern (secret leakage into `.storage`) is fully addressed by making the snapshot metadata-only by default and gating bodies/values behind an explicit, warned `include_secrets` opt-in plus a `forget` lifecycle action. That neutralises the risk without removing the capability, so the milder fix is taken and the "drop it" recommendation declined.

No other critic points were rejected. The local-first architecture the critic confirmed (no cloud writes; `cloud_control.py` is status + `*/control` only) is preserved and reinforced.


<details><summary>Kritiker-Review (Rohfassung)</summary>


Confirmed: the integration stores NO LAN host/IP for any device anywhere — `CONF_KNOWN_DEVICES` is defined in const.py but grep shows it's not actually populated in coordinator/config_flow, and the device registry entry has no `connections` (no MAC) and no `configuration_url`. This is a major gap the plan glosses over. I have enough to write the review.

---

# ADVERSARIAL REVIEW — STAGE 3 "On-device config clone (LAN-RPC)"

I read the actual source. The plan is unusually well-grounded for the *cloud-side* claims (the read-only Cloud API claim, the `replace_device` patterns, `device_gen`/`GBLE`, the exception discipline all check out). But it makes one false "it just works" claim at its foundation, has a real local-first hole, and is over-scoped for what it delivers. Findings below, severity-tagged, evidence-based.

---

## BLOCKERS

### B1. "aioshelly is available" is FALSE in the sense the plan relies on — the RPC client API surface was never verified
The plan's opening sentence: *"`aioshelly>=13.0.0` is already a declared requirement — the native local RPC client library is available."* This is the load-bearing premise and it is misleading.

Evidence: aioshelly is imported in **exactly one place** — `entities/descriptions.py:28`: `from aioshelly.const import MODEL_NAMES`. That is a static dict constant. The integration has **never** instantiated `RpcDevice`, **never** opened a `ConnectionOptions`, **never** done a single byte of local network I/O via aioshelly. `grep -rln 'import aioshelly'` returns only that one descriptions file. aioshelly isn't even importable in this dev environment (`ModuleNotFoundError`).

So the plan asserts a concrete API (`RpcDevice(...)`, `ConnectionOptions(ip_address=host, password=...)`, `.call_rpc(method, params)`) it never ran. Two concrete risks the plan papers over:
- `RpcDevice` in aioshelly 13.x is **not** a simple request/response client. It is a stateful, WebSocket-first device wrapper (`RpcDevice.create(...)`, `.initialize()`, an `update_*` lifecycle, a connected/notification model). `call_rpc` exists but the constructor/lifecycle in the plan (`RpcDevice(session, ConnectionOptions(...))` then call `call_rpc`) is **not** the real initialization contract. The plan invented an API shape.
- The "thin direct `POST /rpc`" fallback is described as a *fallback* "if a method isn't surfaced" — but `call_rpc` is a passthrough, so the fallback would only trigger on library *breakage*, not missing methods. The plan has the trigger condition wrong.

**Fix:** Drop aioshelly's `RpcDevice` from the design entirely. Gen2/Gen3 Shelly RPC is plain JSON-RPC 2.0 over `POST http://{host}/rpc` (with HTTP digest auth for protected devices). Write the thin direct client as the **primary** path, not a fallback, reusing the `aiohttp` + `validate_*` patterns already in `utils/http.py`. This removes a fabricated dependency on a stateful WebSocket wrapper you've never exercised, and is genuinely more stable (firmware JSON-RPC method names are a harder contract than the Python wrapper, as the plan itself argues in §7). If you keep aioshelly at all, it stays where it is: `MODEL_NAMES` only.

### B2. There is no device_id → LAN host mapping anywhere; "we'd locate the unit by device_id" is false
§3.5 claims: *"The Shelly `device_id` == MAC-lowercase, which is also how we'd locate the unit if we needed an mDNS/host hint."* And §3.4 leans on `coordinator.devices[source_id]["status"]` for fallback.

Evidence: The integration stores **no host/IP for any device**. `entities/base.py:78` builds `DeviceInfo(identifiers={(DOMAIN, device_id)}, ...)` with **no `connections`** (no MAC) and **no `configuration_url`**. `CONF_KNOWN_DEVICES` is *defined* in `const.py:57` ("Map of device_id → hostname") but my grep shows it is **never populated** by the coordinator or config flow — it's a dead constant. The cloud status dict does not contain a LAN IP. So a device_id gives you a MAC, and a MAC is **not** a routable address. mDNS resolution of `shelly-XXXX.local` is not implemented and is explicitly punted to "out of scope" in §8 — which means the *only* way to reach a unit is the operator hand-typing `target_host`/`source_host` every time.

This is fine as a *deliberate* design (operator types the IP), but the plan repeatedly implies device_id helps locate the host. It doesn't. And it means the device-selector UX (§2) is half-useless: the operator picks a device from a dropdown, then must *also* know and type its current DHCP-assigned IP. That's a foot-gun (DHCP churn → you type the IP of a *different* Shelly → you overwrite a healthy device's config; see B3).

**Fix:** Either (a) commit to mDNS/`zeroconf` resolution of the device by its MAC-derived hostname (`shelly{model}-{MAC}.local`) so the selected device *does* determine the host — pulling it out of "out of scope"; or (b) drop the device selector for the target entirely and make `target_host` the sole, required, primary identifier, with a **mandatory `Shelly.GetDeviceInfo` MAC check** that the host's MAC matches the selected target device's device_id before any write. Option (b) is the safe minimum and must be a BLOCKER-level guard, not the optional check buried in §5.

### B3. Writing to a host typed by hand, keyed to a cloud device_id, with no MAC-confirmation gate = silent wrong-device overwrite
Combine B2 with the write path. The operator types `target_host: <device-ip>`. DHCP moved that lease to a *different* Shelly since they last looked. The plan's model guard (§5) compares `old_dev.model != new_dev.model` from the **HA registry** (copied from `replace_device.py:129`) — that compares the two *selected* devices, **not** the device actually answering at `target_host`. The live `Shelly.GetDeviceInfo.model` check is described but **its MAC is never asserted against the selected target's device_id**. Result: you can `Switch.SetConfig` / `Schedule.Create` / `KVS.Set` onto an innocent bystander device on the LAN. That is a destructive, hard-to-diagnose failure, and it directly harms resilience (the opposite of the stage's goal).

**Fix:** Before *any* write, fetch `Shelly.GetDeviceInfo` from `target_host`, derive its MAC, and **hard-fail with `ServiceValidationError` if `mac.lower() != target_device_id.lower()`**. No `force` override for this one (model can mismatch harmlessly; identity cannot). Do the same for `source_host` when live. This is the single most important safety gate and the plan demotes it to a §5 footnote.

---

## MAJOR

### M1. Local-first claim S4 ("works with auth_key invalidated") is contradicted by the device-selector design
§4.3 and S4 claim the clone runs even when the cloud poll is failing/in reauth. But the *entire* operator entry path is two **device selectors** (`integration: shelly_cloud_diy`). Those devices only exist in the HA registry because the coordinator materialized them from cloud polling. More to the point: `_resolve_entry` (reused per §3.5) requires both devices share a live `shelly_cloud_diy` config entry, and the dead-source fallback reads `coordinator.devices[...]`. When `auth_key` is invalid, `async_config_entry_first_refresh()` raises `ConfigEntryAuthFailed` (`__init__.py:56`, coordinator `:166-168`) and **the entry fails to set up** — `hass.data[DOMAIN][entry.entry_id]` may not exist, so `coordinator.devices` is unreachable. The registry *devices* survive (they're persisted), but the coordinator object behind them may not. So S4 as written ("entry in reauth, clone still completes") is not guaranteed by this design; it depends on whether setup half-completed.

**Fix:** Decouple the clone path from the coordinator entirely. Resolve `source`/`target` from the **device registry only** (you already have `_shelly_id` for that), take hosts from the service fields, and never touch `coordinator.devices` on the live path. Make the cloud "name seed" fallback (§3.4 item 1) strictly best-effort and guarded with `coordinator = hass.data[DOMAIN].get(entry.entry_id)` → if `None`, skip silently. Then S4 actually holds. As written it's a hopeful claim.

### M2. `Schedule.Create` idempotency by "structural identity (timespec + calls)" is unsound; you'll get duplicates or destructive deletes
§3.3/§5/S6 claim idempotency via matching schedules on `timespec + calls` and "skip/replace matches." Shelly `Schedule.List` jobs have a server-assigned `id` that is **not** stable across devices, and the `calls` array references component instances; two legitimately-distinct schedules can share a timespec, and a re-run after the operator hand-edited one job on the target will either (a) create a near-duplicate or (b) — if you chose `Schedule.Delete` on "match" — **delete the operator's edited schedule**. Either way "running twice yields the same config" (S6) is not achievable for schedules without a stable key, and the "Delete matching" branch is a local-first hazard (it can remove on-device logic the operator added post-clone).

**Fix:** Don't pretend full idempotency. Default to **non-destructive**: on re-run, `Schedule.List` the target and **skip creating a job whose (timespec, calls) tuple already exists**; never `Schedule.Delete` automatically. Add an explicit `replace_existing: false` field if the operator truly wants a wipe-and-recreate, and make that mode loudly logged. Same logic for webhooks (no stable cross-device id either). Scripts-by-name is fine. KVS-by-key is fine. Be honest in S6 that idempotency is "no duplicates," not "byte-identical."

### M3. `Script.PutCode` chunking + `Script.Create` ordering is under-specified and will silently truncate
The plan says "Script.Create then PutCode (chunked; PutCode appends)." Correct that PutCode appends — which means **if a `Script.Create` returns an existing/reused id, or you re-run, PutCode appends to existing code and you get doubled/corrupted scripts**. The plan's idempotency story ("match scripts by name") doesn't address that PutCode has no "overwrite" — you must `Script.Stop` + delete-and-recreate (or PutCode with a clear-first) to get a clean body. There's also no chunk-size handling spec (PutCode has a per-call body limit ~1–4 KB depending on firmware); "chunked" is hand-waved.

**Fix:** Specify: for each source script, on the target `Script.Stop` (if running) → if a script of that name exists, `Script.Delete` it → `Script.Create {name}` → loop `Script.PutCode {id, code: chunk, append: <bool>}` with `append:false` on first chunk and `append:true` thereafter, chunk size ≤ a conservative constant (e.g. 1024 bytes) → `Script.SetConfig {enable}` → `Script.Start` only if source was enabled. Test against a script larger than one chunk (S2 fixture must include this).

### M4. Component config "transferable subset" filtering is a whitelist you can't safely hardcode across models/firmware
§3.3 says write "filtered to transferable keys only (names, `in_mode`/`type`, auto-on/off timers; never calibration, never WiFi, never mac)." `*.SetConfig` on Gen2 is **schema-strict**: sending a key the target firmware doesn't recognize, or a key in the wrong section, returns an RPC error and the whole component write fails. The set of valid keys differs by model, profile (switch vs cover), and firmware version. A static "transferable keys" whitelist will rot. Worse, the plan's "never WiFi" is good, but it omits that `Switch.SetConfig` etc. will *reject* a payload that still contains read-only/derived fields you forgot to strip (e.g. `id`, computed `voltage_limit` ranges). The dry-run "N component configs" count gives false confidence because the actual SetConfig may fail at apply time on a key you didn't filter.

**Fix:** Invert the approach: read the **target's** current `*.GetConfig` for each component, and write back **only the keys that already exist in the target's config AND that you intend to transfer** (intersection), copying the source's *values*. This makes the write schema-safe by construction (you only set keys the target already accepts) and naturally excludes calibration/mac if you blacklist them from the intersection. Validate each `*.SetConfig` response and report per-component success/fail in the result notification.

### M5. Over-scope / HACS-Core review blocker: a `cloud_polling` overlay integration writing firmware config to arbitrary LAN hosts
§7 acknowledges "local RPC from a cloud_polling-classed integration is unusual." It under-rates the review risk. The manifest declares `"iot_class": "cloud_polling"`. An integration whose advertised class is cloud-polling that ships a service to **mutate device firmware over the LAN** — including creating on-device scripts and KVS — is exactly the kind of scope creep HACS reviewers and (later) Core reviewers push back on. It also overlaps the **core `shelly` integration's** domain (those same Gen2 devices are managed locally by core shelly). Two integrations writing `*.SetConfig` to the same physical device is a recipe for config-fight and user confusion.

**Fix:** Reframe and shrink. (a) Make it unambiguously a one-shot operator tool (it already is) and say so in the manifest/docs; (b) consider gating it behind an explicit opt-in option so it's not a surprise; (c) seriously reconsider whether **scripts/KVS/webhooks cloning belongs in *this* integration at all** vs. being the upstream core-shelly repair-flow work the project already plans (Stage 2's PR path). Cloning *names + input modes* is a defensible thin overlay; cloning *on-device scripts* is a heavyweight device-management feature that a cloud-overlay integration has no natural business owning. At minimum, split the categories so the unusual ones (scripts/KVS) are off by default.

### M6. The snapshot `Store` to `.storage/shelly_cloud_diy_clone_{id}.json` is a new persistence surface with secret-leak risk and no lifecycle
§3.4 item 2 adds a `Store`-backed snapshot containing a full `CloneSource` (component configs, scripts, webhooks, KVS). §7 says "strip secrets before save." Problems: (1) on-device **scripts and KVS routinely contain secrets** (API tokens, MQTT creds, webhook URLs with keys) that `_strip_secrets(config)` cannot reliably detect — you can strip `wifi.sta.pass` by key name, but you cannot know that `KVS["mqtt_token"]` is a secret. So the snapshot will leak credentials into `.storage` in plaintext, violating the spirit of the project's HARD secret rule. (2) No retention/cleanup: snapshots accumulate per device_id forever. (3) `Store` writes are debounced; a snapshot taken right before the operator pulls the plug may not be flushed — undermining the very "snapshot before failure" use case.

**Fix:** If you keep the snapshot: write it with `Store(..., private=True)` semantics is not enough — flush synchronously (`await store.async_save(...)`; Store.async_save *does* write immediately, but document that the operator must wait for the result notification before powering off). Do **not** snapshot script bodies or KVS values by default (only metadata + names) unless the operator passes an explicit `include_secrets: true` with a loud warning. Add a `clone_config` `action: forget` to delete a snapshot. And honestly — given M5 — consider dropping the snapshot feature for v1; it's the most over-engineered part.

---

## MINOR

### m1. `services.yaml`/strings/translations sync is asserted but the repo has a pattern you must match
The existing `replace_device:` block in `services.yaml` uses `device:` selectors and the `__init__.py` schema validates with `cv.string` (selectors return entity/device-id strings). Your new `categories` multi-select must use a `select:` selector with `multiple: true` and `options:`, and the `vol.Schema` must validate it as `vol.All(cv.ensure_list, [vol.In([...])])` — not `cv.string`. The plan says "multi-select" but the schema sketch in §3.1 doesn't show the voluptuous shape; mismatches here are the #1 hassfest failure. Pin it down.

### m2. `validate_local_host` "reject loopback / HA host" is good but "restrict to RFC1918 with override" will block legitimate setups
Some operators run Shellies on a routed VLAN that isn't RFC1918 (or use a `.local` name). An RFC1918-only default with override is fine, but make sure the override is discoverable, and base the SSRF guard on the existing `validate_gateway_url` (which only blocks loopback/unspecified — note it does **not** block link-local or the HA host's own LAN IP; your "reject HA host" claim needs new code, it's not in `http.py` today).

### m3. Gen1 schedule mapping is hand-waved and probably wrong
§3.3: "schedules map to Gen1 `schedule`/`schedule_rules` fields where present." Gen1 schedule semantics (sunrise/sunset offsets, `schedule_rules` string format `HHMM-DOW-on`) are nothing like Gen2 `Schedule.Create` jobs. There is no clean mapping. Claiming S2 ("Schedule.List matches") for Gen1 is impossible — Gen1 has no `Schedule.List`. 

**Fix:** State plainly: Gen1 clones **names + relay settings + actions(webhooks) only**; schedules are explicitly **not** transferred for Gen1, shown as a capability gap in the dry-run. Don't imply parity.

### m4. "No control entities touched" (S5) is true for the happy path but the test is over-claimed
S5/§6 wants "byte-identical registries before/after." That's a fine *intent*, but the cloud "name seed" fallback (§3.4) reads `coordinator.device_names` which can trigger a background `_refresh_device_names` → `device_registry.async_update_device(name=...)` (coordinator ~:266). If the clone call happens to wake that path, the device registry *will* change (a name), failing the byte-identical assertion. Keep the clone path strictly read-only w.r.t. the coordinator (see M1 fix) so S5 is actually true.

### m5. `force` semantics copied from replace_device are wrong for clone
`replace_device`'s `force` overrides a model-name mismatch and that's relatively safe (prefix rewrite). For clone, `force`-overriding a model mismatch means pushing `switch:*` config onto a `cover:*` profile device — the plan itself says in §5 this "must not" happen. So `force` here is more dangerous than in replace_device. Don't reuse the same flag meaning. Keep the profile/model guard **non-overridable**, and if you must have an escape hatch, scope it per-category.

### m6. `Webhook.ListSupported` validation is good but webhook URLs may point at the *old* HA/cloud and silently break
Cloned webhooks often contain absolute URLs (e.g. `http://old-ha-ip:8123/api/webhook/...`). Copying them verbatim to the new device can recreate a *control/notify path that points at a stale endpoint* — not a local-first violation per se, but a silent breakage. At least surface the webhook URLs in the dry-run plan so the operator sees what's being copied.

---

## False / overstated claims to correct (quick hits)
- "the native local RPC client library is available" → only `MODEL_NAMES` is used; no RPC client has ever run (B1).
- "device_id … is how we'd locate the unit" → no host mapping exists; `CONF_KNOWN_DEVICES` is a dead constant (B2).
- S4 "works with auth_key invalidated" → not guaranteed; coordinator may be absent (M1).
- S6 "running twice yields the same target config" → unachievable for schedules/webhooks without stable keys (M2); and PutCode-append breaks it for scripts (M3).
- "filtered to transferable keys" via static whitelist → will fail schema-strict SetConfig (M4).
- Gen1 schedule parity → impossible (m3).

---

## VERDICT: **REVISE**

The strategic shape is sound and the local-first *architecture* (no cloud writes — verified, `cloud_control.py` has only status + `*/control`) is genuinely defensible. The stage is not a control-path violation by construction, which is the operator's hard line, and that holds. But the plan ships on three unverified/false foundations (the aioshelly RPC client it never ran, the nonexistent host mapping, and a missing MAC-confirmation write gate) plus an idempotency story that doesn't survive contact with Shelly's schedule/script semantics. It is also over-scoped for a cloud-overlay integration. Do not implement as written.

## PRIORITISED MUST-FIX (in order)
1. **B3 + B2** — Mandatory, non-overridable MAC check: fetch `Shelly.GetDeviceInfo` from `target_host`, hard-fail unless its MAC == selected target device_id. Make `target_host` the primary identifier; either implement mDNS host resolution or accept hand-typed IPs gated by the MAC check. (Wrong-device overwrite is the worst outcome and the plan demotes the guard.)
2. **B1** — Replace the fabricated `RpcDevice` usage with a thin, primary `POST /rpc` JSON-RPC client + HTTP digest auth, modeled on `utils/http.py`. Keep aioshelly at `MODEL_NAMES` only. Verify against a real device before claiming any of S1–S6.
3. **M4** — Make `*.SetConfig` schema-safe by intersecting against the target's live `*.GetConfig` keys; validate every write response; report per-component.
4. **M3 + M2** — Correct script clone (stop→delete→create→chunked PutCode `append:false/true`→enable) and make schedule/webhook clone **non-destructive by default** (skip duplicates, never auto-delete); stop claiming byte-identical idempotency.
5. **M1** — Decouple the clone path from the coordinator (registry-only resolution) so S4/S5 actually hold.
6. **M5 + M6** — Shrink scope: split categories, default the heavyweight ones (scripts/KVS) off, and either drop the `.storage` snapshot for v1 or make it metadata-only with explicit opt-in for bodies/secrets and a `forget` action. This is also the HACS/Core review-blocker mitigation.
7. **m1/m3/m5** — Fix the voluptuous schema for `categories`, stop implying Gen1 schedule parity, and make the model/profile guard non-overridable.

Relevant files (absolute):
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/services/replace_device.py` (model guard at :129 compares *registry* models, not the live host — see B3)
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/entities/descriptions.py:28` (the *only* aioshelly use — B1)
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/const.py:57` (`CONF_KNOWN_DEVICES` defined but never populated — B2)
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/entities/base.py:78` (DeviceInfo has no `connections`/MAC and no `configuration_url` — B2)
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/api/cloud_control.py` (read-only claim verified: only `get_*` + `*_control` — local-first architecture holds)
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/coordinator.py:266` (background `async_update_device(name=...)` can mutate registry — m4/M1)
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/__init__.py:162-176` (`_register_services` schema uses `cv.string`; `categories` needs a list validator — m1)
- `/home/dwolbeck/claude-code/PUBLIC/shelly-cloud-diy-ha/custom_components/shelly_cloud_diy/utils/http.py:17` (`validate_gateway_url` blocks loopback only — does NOT block the HA host; the §3.1 "reject HA host" claim is new code — m2)


</details>
