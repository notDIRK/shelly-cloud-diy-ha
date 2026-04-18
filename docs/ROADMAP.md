# Roadmap — Shelly Cloud DIY for Home Assistant

## English

### Project intent

`shelly-cloud-diy-ha` is a Home Assistant custom integration that connects
Home Assistant to the Shelly Cloud using the **Cloud Control API**, the
self-service API path that Shelly explicitly documents as being available
to DIY / private users. The project exists because the only pre-existing
community integration in the same space ([engesin/shelly-integrator-ha](https://github.com/engesin/shelly-integrator-ha))
uses the **Integrator API**, which Shelly documents as *"Licenses for
personal use are not provided."* — it requires a commercial-integrator
approval flow that most private users never get through.

This project is a hard fork of that upstream, retained for git-history
traceability only. No upstream merges are expected.

### Scope target

- **Short term:** installable via **HACS** (currently as a custom repository,
  subsequently in the HACS default store).
- **Not a short-term goal:** submission to **Home Assistant Core**. Code is
  kept Core-compatible in style (no personal references in source, English
  log messages, proper exception types, translations), but we are not
  building out the full Core quality-scale requirements (heavy test
  coverage, diagnostics/repairs platforms, quality_scale=gold) in the
  initial releases.

### Milestones

Status key: ✅ done · 🔄 in progress · ⏳ planned · 💡 aspirational

#### Milestone 0 — Foundation  ✅

- Forked `engesin/shelly-integrator-ha` as `notDIRK/shelly-integrator-ha`
- Security hardening: randomised per-install webhook id, local-gateway-URL
  SSRF guard, webhook-handler logging uses `logger.exception`
- Correctness: deep-merge partial StatusOnChange updates, disabled dead 30 s
  polling timer, jittered WebSocket reconnect backoff
- Consolidated codebase map at `docs/CODEBASE_MAP.md`
- Bilingual "Getting an API Token" section (documented the Integrator-API
  acquisition problem — now largely obsolete post-pivot)
- Pivot research: verified the Shelly Cloud Control API sees shared
  devices (tested against a real ECOWITT WS90 shared from another
  account); verified the Cloud Control API WebSocket rejects `auth_key`
  (`Token-Broken` close 4401) and requires OAuth; confirmed HTTP polling
  via `auth_key` returns full device status for all account-visible
  devices
- Repo rename to `shelly-cloud-diy-ha`, Python domain to `shelly_cloud_diy`,
  CLOUD DIY branding applied to `images/icon.png`
- Three historical release tags (`v0.1.0-notDIRK` … `v0.2.2-notDIRK`) kept
  on their Integrator-API commits for audit trail

#### Milestone 1 — Cloud Control API with auth_key + HTTP polling  🔄 (next)

**Goal:** First usable HACS release for private users. No more
Integrator-API token, no Shelly support email, no consent webhook. User
pastes their `auth_key` + server URI from the Shelly App and everything
works.

Changes:
- Replace auth layer: delete `api/auth.py` (JWT / integrator-token
  exchange), add `api/cloud_control.py` (HTTP client wrapping
  `POST /device/all_status`, `POST /device/status`, `POST /device/relay/control`,
  `POST /device/light/control`, `POST /device/relay/roller/control`, all
  authenticated via the `auth_key` form parameter)
- Rewrite `config_flow.py` — user step asks for `auth_key` + `server URI`;
  no more consent step; options flow simplified accordingly
- Rewrite `coordinator.py` to poll `/device/all_status` at a configurable
  interval (3–60 s, default 5 s), respecting the documented 1 req/s rate
  limit (single consolidated poll beats per-device fan-out)
- Remove: consent webhook flow (`services/webhook.py`, `core/consent.py`,
  webhook-id migration logic in `__init__.py`), `api/websocket.py` (moved
  to M2 scope)
- Keep reusable: device-state merge logic, per-platform entity classes
  (sensor, switch, light, cover, button, binary_sensor), entity
  descriptions, historical CSV service (local-gateway path is unchanged)
- Add: entity mapping for BLE / gateway-bridged sensors seen in
  `/device/all_status` with `gen: "GBLE"` (Shelly BLU family, Shelly BLU
  H&T, SBWS-90CM weather station, etc. — one mapping table keyed by
  `_dev_info.code`)
- Update: translations and `strings.json` for the new config fields
  (`auth_key`, `server_uri` replacing `integrator_token`); German
  translation added
- Manifest: bump to `0.3.0`, update `iot_class` to `cloud_polling`
  (because push is no longer the mechanism), drop unused `dependencies: ["webhook"]`
- Release: v0.3.0 tagged without the `-notDIRK` suffix going forward —
  targeting HACS-default-store submission eventually

Non-goals in M1:
- Real-time / sub-5-second update latency (that is M2)
- OAuth authentication (that is M2)
- Cloud-sourced historical energy data (existing local-gateway path is
  preserved; cloud historical is a separate later scope if feasible)

Explicitly documented limitations users must know:
- **1 request per second** rate limit per Shelly account (Shelly official
  doc)
- **Polling latency** at default 5 s means sensor values lag reality by up
  to ~5 seconds; switch actions fire immediately, latency only applies to
  state *observation*
- **HTTP endpoints are documented by Shelly as intentionally
  underdocumented** (they reserve the right to change parameter formats)
  — we pin to the v1 endpoint shape and will track changes reactively

#### Milestone 2 — OAuth + WebSocket realtime  ⏳

**Goal:** Push-based realtime updates for users who are willing to
authenticate with email + password instead of (or in addition to) the
auth_key.

Changes:
- Add OAuth code flow to `config_flow.py`: `POST
  https://api.shelly.cloud/oauth/login` with `email` + `sha1(password)` +
  `client_id=shelly-diy` → receive `code` → `POST
  https://<server>/oauth/auth` with `code` → receive `access_token`
- Bring back `api/websocket.py` (architecturally reused from the
  pre-pivot Integrator-API era — the WSS endpoint format is identical)
  and use OAuth `access_token` as the `t=` URL parameter
- Swap coordinator's polling loop for WebSocket event subscription
  (`Shelly:StatusOnChange`, `Shelly:Online`, `Shelly:CommandResponse`)
- Access-token lifecycle: track expiry, refresh proactively, fall back to
  re-OAuth if refresh fails
- Options flow: let the user switch between Simple (auth_key / polling)
  and Full (OAuth / realtime) modes without reinstalling

Non-goals in M2:
- Per-device webhook subscriptions (the WebSocket delivers everything)

#### Milestone 3 — HACS default-store submission  💡

**Goal:** Entry in the [HACS default integration list](https://github.com/hacs/default),
so that users no longer need to add this as a custom repository URL.

Prerequisites:
- Logo submission to [home-assistant/brands](https://github.com/home-assistant/brands)
  as `core_integrations/shelly_cloud_diy/{icon.png,logo.png}` — clean
  variants without the `notDIRK` wordmark and fork symbol will be
  generated at this point
- First stable (non-`-dev`) release tag
- README finalised and passing the HACS style review
- Issue tracker with at least a few closed / triaged issues (to show
  active maintenance)
- Optional: simple GitHub Actions CI that runs lint and any existing
  tests on push / PR

#### Milestone 4 — Quality-scale improvements  💡

Path to HA Core quality-scale `silver` / `gold`:
- `async_get_config_entry_diagnostics` for sanitized export
- `repairs` platform for actionable issue flags (rate-limit exhaustion,
  token expiry, etc.)
- Test coverage target ≥ 70 %
- CI: lint, type-check (mypy), test matrix against supported HA versions

(Not committed to a timeline — gated on whether a Core submission
materialises as a goal.)

### Differentiation vs existing projects

| Project | Auth method | Realtime | Shared devices | Maintained | Notes |
|---|---|---|---|---|---|
| **`notDIRK/shelly-cloud-diy-ha`** (this repo) | `auth_key` (M1) / OAuth (M2) | HTTP poll 5 s (M1) / WebSocket push (M2) | ✅ | 🔄 active | Full Gen1 + Gen2 + BLE-gateway coverage |
| [`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha) | Integrator API token (gated by Shelly) | WebSocket push | ❌ (consent-flow is per-owner) | ✅ active | Private users typically cannot obtain the token |
| [`home-assistant/core` Shelly integration](https://www.home-assistant.io/integrations/shelly/) | Local LAN (mDNS / direct IP) | LAN push | ❌ (remote / shared devices not reachable over LAN) | ✅ maintained by HA Core | Mainstream; requires LAN reachability |
| [`StyraHem/ShellyForHASS`](https://github.com/StyraHem/ShellyForHASS) | Local LAN | LAN push | ❌ | ❌ **"ShellyForHass will no longer receive further development updates"** (per README) | Folded into HA Core |
| [`vincenzosuraci/hassio_shelly_cloud`](https://github.com/vincenzosuraci/hassio_shelly_cloud) | Username/password (reverse-engineered browser calls) | HTTP polling | ? | ❌ last push 2019 | Switches only; README warns HTTP parsing is fragile |
| [HA YAML Blueprint](https://community.home-assistant.io/t/controlling-shelly-cloud-devices-in-home-assistant/928462) | `auth_key` (same as us) | ❌ command-only | ? | ✅ community-maintained | *"The device state is not updated from the cloud"* — cannot read state back |
| [`corenting/poc_shelly_cloud_control_api_ws`](https://github.com/corenting/poc_shelly_cloud_control_api_ws) | OAuth | WebSocket push | ? | Explicitly labelled POC, not an integration | Reference implementation for our M2 OAuth flow |

The short version: there is currently **no other maintained HA
integration that combines Cloud-Control-API-based access with state
reading AND shared-device support AND Gen1/Gen2/BLE coverage**. The gap
is real, which is why this project exists.

### Rate limits, latency, and honest expectations

**Shelly's documented rate limit:** 1 API request per second per account
(source: [Shelly Cloud Control API docs, Getting Started](https://shelly-api-docs.shelly.cloud/cloud-control-api/)).

**Milestone 1 traffic profile:**
- A single `POST /device/all_status` returns the complete state snapshot
  of every device your account can see (owned + shared + BLE-bridged).
  58-device accounts return ≈ 60 KB per request.
- Default poll interval: 5 s → average traffic ≈ 12 KB/s outbound HTTPS.
  Configurable down to 3 s (24 KB/s at 58 devices) for snappier state or
  up to 60 s for low-traffic / battery-sensitive setups.
- User-initiated commands (switch on/off, dim, roller) are dispatched
  immediately via separate HTTP POSTs; they do not wait for the next
  poll cycle. Commands and polls share the 1 req/s budget, so the
  default 5 s interval leaves ~4 req/s of command headroom.
- Observed state-change latency: **p50 ≈ 2.5 s, p99 ≈ 5 s** at default
  poll interval. For weather station / energy metering use cases this is
  a non-issue; for light-switch feedback it can feel gentle.

**Milestone 2 traffic profile (future):**
- Outbound poll traffic: **0 bytes** steady state; events push from
  Shelly Cloud as they happen.
- Latency: **< 100 ms** for state propagation from device → Shelly Cloud
  → HA.
- Cost: single persistent WebSocket connection per HA instance; one
  OAuth re-auth roughly every 24 hours.

### Security and data handling

- The `auth_key` is stored in `entry.data` (Home Assistant standard
  config-entry storage, plaintext at rest in `.storage/core.config_entries`).
  The key grants broad device control — treat it like a password.
- It is displayed by the Shelly App under **User settings → Authorization
  cloud key**. Changing your Shelly password invalidates it
  server-side, which is the intended rotation mechanism.
- Milestone 1 does not store email or password.
- Milestone 2 (OAuth) sends `sha1(password)` to
  `api.shelly.cloud/oauth/login` during the initial login; the resulting
  `access_token` is stored in `entry.data`. We do not store the password
  itself.

---

## Deutsch

### Projektziel

`shelly-cloud-diy-ha` ist eine Home-Assistant-Custom-Integration, die Home
Assistant über die **Cloud Control API** von Shelly anbindet — also über
den offiziellen Self-Service-Pfad, den Shelly ausdrücklich für DIY- und
Privat-User vorgesehen hat. Das Projekt existiert, weil die einzige
bisher verfügbare Community-Integration in diesem Themenfeld
([engesin/shelly-integrator-ha](https://github.com/engesin/shelly-integrator-ha))
die **Integrator API** nutzt, zu der Shelly wörtlich dokumentiert:
*"Licenses for personal use are not provided."* — dafür ist ein
kommerzieller Integrator-Freigabe­prozess nötig, durch den die meisten
Privatanwender nie durchkommen.

Dieses Projekt ist ein Hard-Fork von oben genanntem Upstream, den wir nur
wegen Git-History-Nachvollziehbarkeit behalten. Weitere Upstream-Merges
sind nicht vorgesehen.

### Scope-Ziel

- **Kurzfristig:** Installierbar via **HACS** (zunächst als Custom
  Repository, später im HACS-Default-Store).
- **Kein Kurzfrist-Ziel:** Aufnahme in **Home Assistant Core**. Wir halten
  den Code stilistisch Core-kompatibel (keine Personennamen im Quellcode,
  englische Logmeldungen, ordentliche Exception-Typen, Übersetzungen) —
  aber wir bauen den vollen Core-Qualitätsstandard (umfangreiche
  Tests, Diagnostics/Repairs-Platforms, quality_scale=gold) in den
  ersten Releases bewusst NICHT aus.

### Meilensteine

Status: ✅ fertig · 🔄 in Arbeit · ⏳ geplant · 💡 angestrebt

#### Meilenstein 0 — Grundlage  ✅

- `engesin/shelly-integrator-ha` geforkt als `notDIRK/shelly-integrator-ha`
- Security-Härtung: randomisierte Per-Install-Webhook-ID, SSRF-Schutz für
  Local-Gateway-URL, Webhook-Handler-Logging über `logger.exception`
- Korrektheit: Deep-Merge bei partiellen StatusOnChange-Updates, toter
  30-s-Polling-Timer deaktiviert, WebSocket-Reconnect mit Jitter
- Konsolidierte Codebase-Map unter `docs/CODEBASE_MAP.md`
- Zweisprachiger "Getting an API Token"-Abschnitt in der alten README
  (dokumentierte das Integrator-API-Beschaffungsproblem — nach dem Pivot
  weitgehend obsolet)
- Pivot-Recherche: verifiziert, dass die Shelly Cloud Control API
  geteilte Geräte sieht (mit einer echten ECOWITT WS90 getestet, die aus
  einem Fremd-Account geteilt ist); verifiziert, dass die Cloud-Control-API-
  WebSocket den `auth_key` ablehnt (`Token-Broken`, Close 4401) und OAuth
  braucht; bestätigt, dass HTTP-Polling mit `auth_key` den vollständigen
  Status aller Account-sichtbaren Geräte zurückgibt
- Repo umbenannt zu `shelly-cloud-diy-ha`, Python-Domain zu
  `shelly_cloud_diy`, CLOUD-DIY-Branding in `images/icon.png`
- Drei historische Release-Tags (`v0.1.0-notDIRK` … `v0.2.2-notDIRK`)
  bleiben auf ihren Integrator-API-Commits als Audit-Trail

#### Meilenstein 1 — Cloud Control API mit auth_key + HTTP-Polling  🔄 (als Nächstes)

**Ziel:** Das erste nutzbare HACS-Release für Privatanwender. Kein
Integrator-API-Token mehr, keine Support-Mail an Shelly, kein
Consent-Webhook. User kopiert `auth_key` + Server-URI aus der Shelly-App
rein und alles läuft.

Änderungen:
- Auth-Schicht ersetzen: `api/auth.py` (JWT/Integrator-Token-Austausch)
  löschen, `api/cloud_control.py` hinzufügen (HTTP-Client mit
  `POST /device/all_status`, `POST /device/status`,
  `POST /device/relay/control`, `POST /device/light/control`,
  `POST /device/relay/roller/control`, authentifiziert per
  `auth_key`-Form-Parameter)
- `config_flow.py` neu schreiben — User-Step fragt `auth_key` + `Server-URI`
  ab; kein Consent-Step mehr; Options-Flow entsprechend vereinfacht
- `coordinator.py` auf Polling von `/device/all_status` umschreiben
  mit konfigurierbarem Intervall (3–60 s, Default 5 s), respektiert das
  dokumentierte 1-req/s-Rate-Limit (konsolidierter Single-Poll schlägt
  Per-Device-Fan-Out)
- Entfernen: Consent-Webhook-Flow (`services/webhook.py`,
  `core/consent.py`, Webhook-ID-Migrations-Logik in `__init__.py`),
  `api/websocket.py` (zurück in M2-Scope)
- Wiederverwenden: Device-State-Merge-Logik, Per-Platform-Entity-Klassen
  (sensor, switch, light, cover, button, binary_sensor),
  Entity-Descriptions, Historical-CSV-Service (Local-Gateway-Pfad bleibt
  unverändert)
- Hinzufügen: Entity-Mapping für BLE/Gateway-überbrückte Sensoren, die
  in `/device/all_status` mit `gen: "GBLE"` auftauchen (Shelly-BLU-Familie,
  Shelly BLU H&T, SBWS-90CM-Wetterstation etc. — eine Mapping-Tabelle,
  gekeyed auf `_dev_info.code`)
- Aktualisieren: Translations und `strings.json` für die neuen
  Config-Felder (`auth_key`, `server_uri` statt `integrator_token`);
  deutsche Übersetzung ergänzen
- Manifest: Bump auf `0.3.0`, `iot_class` auf `cloud_polling` umstellen
  (weil der Push-Mechanismus entfällt), ungenutzte
  `dependencies: ["webhook"]` entfernen
- Release: v0.3.0 getaggt ohne `-notDIRK`-Suffix — Ziel ist langfristig
  der HACS-Default-Store

Nicht-Ziele in M1:
- Echtzeit / Sub-5-Sekunden-State-Update-Latenz (→ M2)
- OAuth-Authentifizierung (→ M2)
- Cloud-seitige historische Energiedaten (der bestehende
  Local-Gateway-Pfad bleibt; Cloud-Historie ist separater Spät-Scope,
  sofern machbar)

Ausdrücklich dokumentierte Einschränkungen, die User kennen müssen:
- **1 Request pro Sekunde** Rate-Limit pro Shelly-Account (Shelly-Offizial-Doku)
- **Polling-Latenz** von 5 s (Default) bedeutet: Sensor-Werte hinken der
  Realität um bis zu ~5 Sekunden hinterher; Schaltbefehle gehen sofort
  raus, die Latenz betrifft nur die State-*Beobachtung*
- **HTTP-Endpunkte sind laut Shelly absichtlich nur grob dokumentiert**
  (Shelly behält sich Parameterformat-Änderungen vor) — wir pinnen auf
  die aktuelle v1-Endpunkt-Form und reagieren auf Änderungen reaktiv

#### Meilenstein 2 — OAuth + WebSocket-Realtime  ⏳

**Ziel:** Push-basierte Realtime-Updates für User, die bereit sind, sich
mit Mail + Passwort zu authentifizieren statt (oder zusätzlich zum)
`auth_key`.

Änderungen:
- OAuth-Code-Flow im `config_flow.py` ergänzen: `POST
  https://api.shelly.cloud/oauth/login` mit `email` + `sha1(password)` +
  `client_id=shelly-diy` → `code` empfangen → `POST
  https://<server>/oauth/auth` mit `code` → `access_token` empfangen
- `api/websocket.py` zurückholen (architekturbekannt aus der Vor-Pivot-
  Integrator-API-Ära — die WSS-URL-Form ist identisch) und OAuth-
  `access_token` als `t=`-URL-Parameter nutzen
- Coordinator-Polling-Loop durch WebSocket-Event-Subscription ersetzen
  (`Shelly:StatusOnChange`, `Shelly:Online`, `Shelly:CommandResponse`)
- Access-Token-Lifecycle: Ablauf tracken, proaktiv refreshen, Fallback
  auf Re-OAuth wenn Refresh scheitert
- Options-Flow: Umschalten zwischen Simple (auth_key / Polling) und Full
  (OAuth / Realtime) ohne Neuinstallation

Nicht-Ziele in M2:
- Per-Device-Webhook-Subscriptions (WebSocket liefert bereits alles)

#### Meilenstein 3 — HACS-Default-Store-Aufnahme  💡

**Ziel:** Eintrag in der [HACS-Default-Integration-Liste](https://github.com/hacs/default),
damit User die Integration nicht mehr über Custom-Repository-URL
hinzufügen müssen.

Voraussetzungen:
- Logo-Submission an [home-assistant/brands](https://github.com/home-assistant/brands)
  als `core_integrations/shelly_cloud_diy/{icon.png,logo.png}` —
  bereinigte Varianten ohne `notDIRK`-Wordmark und Fork-Symbol werden zu
  diesem Zeitpunkt generiert
- Erstes stabiles Release-Tag (ohne `-dev`)
- README finalisiert, besteht das HACS-Style-Review
- Issue-Tracker mit mindestens ein paar geschlossenen / triagierten
  Issues (um aktive Wartung zu zeigen)
- Optional: simpler GitHub-Actions-CI, der Lint und die vorhandenen
  Tests bei Push / PR laufen lässt

#### Meilenstein 4 — Quality-Scale-Ausbau  💡

Pfad zu HA-Core-Quality-Scale `silver` / `gold`:
- `async_get_config_entry_diagnostics` für sanitisierten Export
- `repairs`-Plattform für aktionable Fehlerkennzeichnungen
  (Rate-Limit-Erschöpfung, Token-Ablauf etc.)
- Testabdeckung ≥ 70 %
- CI: Lint, Type-Check (mypy), Test-Matrix gegen unterstützte HA-Versionen

(Kein fester Zeitplan — abhängig davon, ob Core-Submission wirklich
Ziel wird.)

### Abgrenzung zu anderen Projekten

(Siehe die Tabelle oben im englischen Abschnitt — die Spaltenüberschriften
sind identisch; die Inhaltsaussagen gelten sprachübergreifend.)

Kurzfassung: Aktuell existiert **keine andere gepflegte HA-Integration,
die Cloud-Control-API-Zugriff MIT State-Read UND Shared-Device-Support
UND Gen1/Gen2/BLE-Abdeckung kombiniert**. Diese Lücke ist real und der
Grund, warum es dieses Projekt überhaupt gibt.

### Rate-Limits, Latenz, ehrliche Erwartungen

**Shellys dokumentiertes Rate-Limit:** 1 API-Request pro Sekunde pro
Account (Quelle: [Shelly Cloud Control API Docs, Getting Started](https://shelly-api-docs.shelly.cloud/cloud-control-api/)).

**Traffic-Profil in Meilenstein 1:**
- Ein einzelner `POST /device/all_status` liefert den kompletten State-
  Snapshot aller Geräte, die dein Account sieht (eigene + geteilte +
  BLE-überbrückte). Bei 58-Geräte-Accounts ca. 60 KB pro Request.
- Default-Poll-Intervall: 5 s → durchschnittlich ca. 12 KB/s Outbound-
  HTTPS. Konfigurierbar bis runter auf 3 s (24 KB/s bei 58 Geräten) für
  snappieren State oder hoch bis 60 s für Low-Traffic-/
  Battery-Setups.
- User-initiierte Befehle (Schalter an/aus, Dimmen, Rollladen) werden
  sofort per separatem HTTP-POST abgesetzt, nicht erst beim nächsten
  Poll. Commands und Polls teilen sich das 1-req/s-Budget, das
  Default-5-s-Intervall lässt also ca. 4 req/s Command-Headroom.
- Beobachtete State-Change-Latenz: **p50 ≈ 2,5 s, p99 ≈ 5 s** bei
  Default-Poll. Für Wetterstation / Energie-Metering irrelevant; für
  Licht-Schalter-Feedback fühlt sich das gemütlich an.

**Traffic-Profil in Meilenstein 2 (Zukunft):**
- Outbound-Poll-Traffic: **0 Bytes** Steady State; Events werden von
  Shelly Cloud gepusht, wie sie passieren.
- Latenz: **< 100 ms** für State-Propagation vom Gerät → Shelly Cloud → HA.
- Kosten: Eine persistente WebSocket-Connection pro HA-Instanz; ein
  OAuth-Re-Auth ungefähr alle 24 Stunden.

### Security und Datenhaltung

- Der `auth_key` wird in `entry.data` gespeichert (Home-Assistant-
  Standard-Config-Entry-Storage, Klartext auf Disk unter
  `.storage/core.config_entries`). Der Key gibt weitreichende Kontrolle
  über deine Geräte — behandle ihn wie ein Passwort.
- Er wird in der Shelly-App unter **Benutzereinstellungen →
  Authorization cloud key** angezeigt. Ein Passwort-Wechsel bei Shelly
  invalidiert ihn serverseitig — das ist die vorgesehene
  Rotations-Methode.
- Meilenstein 1 speichert weder Mail noch Passwort.
- Meilenstein 2 (OAuth) sendet `sha1(passwort)` beim initialen Login an
  `api.shelly.cloud/oauth/login`; der resultierende `access_token` wird
  in `entry.data` gespeichert. Das Passwort selbst speichern wir nicht.
