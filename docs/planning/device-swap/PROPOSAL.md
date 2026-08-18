# Feature-Vorschlag: "Shelly tauschen ohne Neu-Konfiguration" (Device Replacement)

> Status: **Vorschlag / noch nicht beschlossen** · erstellt 2026-06-23 · Quelle: Multi-Agent-Research (HA-Core-Registry, Native-Shelly-Integration, Shelly-API, eigene Codebase). Alle Fakten primärquellen-belegt — siehe `RESEARCH-NOTES.md` (Zusammenfassung) bzw. die zitierten Links unten.

## 1. Das Problem (Nutzersicht)

Ein eingebundener Shelly geht kaputt. Beim Ersatz durch ein neues Gerät:

- Das neue Gerät hat eine **andere MAC** → HA sieht es als **komplett neues Device**.
- Alle Automationen, Szenen, Dashboards, Skripte, Template-Sensoren, Energie-Statistiken, die auf den alten Shelly zeigten, sind **verwaist**. Das neue Gerät bekommt `..._2`-Entities.
- Bisher muss man das neue Gerät neu anlernen **und** überall in HA von Hand umbiegen. Nervig, fehleranfällig.

Ziel: **ein Knopfdruck** — alte HA-Identität (entity_ids, device_id, Namen, Bereiche, Verlauf) auf die neue Hardware übertragen. Optional zusätzlich die **Shelly-eigene Konfiguration** (Relaisnamen, Eingangsmodi, Zeitpläne, On-Device-Skripte) mitkopieren.

Anforderung des Betreibers: Soll **nicht nur** für über `shelly-cloud-diy` eingebundene Geräte funktionieren, sondern **auch** für Shellys, die über die **native HA-Shelly-Integration (lokal/LAN)** eingebunden sind.

## 2. Die zentrale Erkenntnis (warum das überhaupt geht)

HA identifiziert eine Entity intern über `(domain, platform, unique_id)`. Der **`entity_id`-String** (z.B. `switch.buro_licht`) ist davon entkoppelt und nur eine *Referenz*. **~90 %+ aller Referenzen** (Dashboards, History, LTS/Energie, Szenen, Skripte, Templates, Helfer, die meisten YAML-Automationen) zeigen auf `entity_id`. Nur **Geräte-Automationen (UI device triggers/actions)** und Geräte-Bereich/Label zeigen auf `device_id`.

**Der Trick ("Identity-Transplant"):**
1. Die `unique_id` des **bestehenden** Registry-Eintrags auf das Schema umschreiben, das die neue Hardware erzeugen *wird*.
2. Beim nächsten Reload "adoptiert" die Integration via `async_get_or_create(platform, new_unique_id)` den bestehenden Eintrag → **`entity_id`, Name, Bereich, Verlauf bleiben erhalten**.
3. Zusätzlich `identifiers`/`connections` des **bestehenden Device-Eintrags** auf die neue MAC umbiegen → **dieselbe `device_id` überlebt** → auch Geräte-Automationen und Geräte-Bereich/Label bleiben.
4. Die vom neuen Gerät automatisch erzeugten Duplikat-Entities/-Device vorher entfernen (sonst `unique_id`-Kollision → `ValueError`).
5. Config-Entry **reload** (kein Neustart nötig).

Belege: HA `entity_registry.async_update_entity(new_unique_id=...)`, `device_registry.async_update_device(new_identifiers=/new_connections=)`, Adoptions-Logik in `async_get_or_create`. Registries schreiben debounced (~10 s) — also **immer über die Python-APIs im laufenden HA mutieren**, nie die `.storage`-JSON live editieren.

**Wichtige Randbedingung — Langzeit-Statistik:** Solange wir `entity_id` *erhalten* (das ist der Plan), bleibt auch die LTS/Energie-Historie intakt. Nur wenn sich `entity_id` ändern müsste, müsste man `statistic_id` separat umziehen (HA-Bug #167253).

## 3. Stand der Technik (es gibt nichts Fertiges)

- **Kein Core-Feature** "Replace device". Architecture-Discussion #1088 (2024) wurde von Frenck abgelehnt; Core verweist auf den **pro-Integration**-Ansatz.
- Einziges konkretes Vorbild: **ESPHome** Repair-Flow (Core PR #142507, 2025) — genau unser Muster (zwei Geräte gleicher Name, andere MAC → Entities/Automationen erhalten). Das ist die Blaupause.
- **Z-Wave `replace_failed_node`**: nur weil der Controller die alte node_id wiederverwendet — nicht verallgemeinerbar (MAC/Seriennummer-basierte Geräte haben das nicht).
- **Spook** kann `entity_id`/Name/Bereich ändern, aber **NICHT** `unique_id` umschreiben oder Entity/Device neu zuordnen. Kein Tool im Ökosystem kann das, was wir brauchen.
- **Native HA-Shelly-Integration:** hat **kein** Tausch-Feature und **blockiert** den Tausch sogar aktiv (`MacAddressMismatchError` → `ConfigEntryNotReady`; Reconfigure bricht mit `another_device` ab). Issue #125811 ("MacAddressMismatchError when replacing a Shelly device") wurde als *not planned* geschlossen. **→ echte, ungelöste Lücke.**

## 4. Machbarkeit je Einbindungsweg

### 4a. Eigene Integration `shelly_cloud_diy` — EINFACH & sicher (wir besitzen den Code)
- Device-Identifier = `(DOMAIN, device_id)`, **keine** MAC-`connections`.
- Jede `unique_id` = `f"{device_id}_..."`. Tausch = **reiner Präfix-Rewrite** `{old_id}_` → `{new_id}_` über alle Entities + Device-Identifier umschreiben + `enabled_devices`-Option umbiegen + reload.
- Service-Registrierungs-Muster existiert bereits (`download_and_convert_history`). Hook-Punkte exakt identifiziert (`__init__.py:_register_services`, `coordinator.devices`, `entities/base.py`).
- Modell-/Layout-Check über `coordinator.devices[id]["device_code"]` + Status-Key-Struktur.

### 4b. Native HA-Shelly-Integration — MACHBAR, aber riskanter (fremder Code)
- MAC ist Präfix auf **allem**: Config-Entry-`unique_id`, Device-`connections`, Sub-Device-`identifiers`, Entity-`unique_id`.
- Tausch = Präfix-Rewrite `OLD_MAC` → `NEW_MAC` über (Entry-unique_id, Entry-Host, Main-Device-MAC-connection, Sub-Device-Identifier, alle Entity-unique_ids) — **atomar, bevor** der Entry die neue Hardware kontaktiert (sonst `MacAddressMismatchError`).
- Sub-Devices (Pro 4PM: `{mac}-switch:0..3`) und BLU-Geräte (eigenes BLE-Adress-Schema!) **mit** umschreiben.
- Risiken: gleiches Modell + gleiche FW-Familie nötig (Keys müssen byte-genau matchen); **kein** Cross-Gen-Tausch; Pro-3EM LAN/WiFi-Dual-MAC; Schlaf-Geräte-Timing. Wir hängen am internen Schema einer fremden Integration → bei deren Refactor kann es brechen.

### 4c. Shelly-seitiger Config-Klon ("in Shelly selbst ablösen") — OPTIONAL, nur über LAN
- **Cloud Control API (unser auth_key-Pfad) kann das NICHT.** Sie liest nur `settings` (1 req/s) und steuert Ausgänge — **kein** Config-/Schedule-/Script-/KVS-**Write**.
- Voller Klon nur via **lokales RPC** (Gen2+: `Shelly.GetConfig` + pro-Komponente `*.SetConfig`, `Schedule.Create`, `Webhook.Create`, `KVS.Set`, `Script.GetCode`/`PutCode`) bzw. Gen1 `/settings`-Endpunkte.
- Übertragbar: Gerätename, Relais-/Eingangsnamen, Eingangsmodi, Zeitpläne, On-Device-Skripte, Webhooks, KVS, Limits.
- **Nicht** übertragbar: MAC, Device-ID, BLE-Adresse, Zertifikate/Cloud-Key, **alle Passwörter** (write-only, nie ausgelesen), **Rollladen-Kalibrierung** (muss neu kalibriert werden).

## 5. Empfohlene Architektur & Phasen

**Kernfrage (Packaging) — Entscheidung nötig, siehe unten.** Der generische Transplant-Mechanismus ist integrations-**unabhängig**; nur das alt→neu-`unique_id`-Mapping ist integrations-spezifisch. Daraus drei Optionen:

- **(A)** Service nur in `shelly_cloud_diy` (nur unsere Cloud-Geräte). Schnellster Ship, deckt aber native-Shelly NICHT ab → erfüllt die Betreiber-Anforderung nur halb.
- **(B)** Generische Engine *in* `shelly_cloud_diy`, die auch native-Shelly (und theoretisch alles) tauscht. Erfüllt die Anforderung, **bläht aber den Scope** unserer Cloud-Integration auf — riskant für HACS-/Core-Review (eine Shelly-Cloud-Integration, die fremde Integrationen chirurgisch verändert, fällt im Review auf).
- **(C)** **Separates Begleit-Tool** (eigenes kleines Custom-Repo, z.B. `ha-device-swap`, generisch). `shelly_cloud_diy` bleibt sauber. Maximale Reichweite (funktioniert für *jede* Integration), saubere Trennung — dafür ein neues Mini-Projekt zu pflegen. Das ist ehrlich gesagt ein "Mega-Feature für *alle* HA-Nutzer", nicht nur Shelly.

**Empfehlung:** Phasen-Ansatz **A → dann C** (nicht B):

- **Phase 1 (MVP, sicher, schnell):** `replace_device`-Service in `shelly_cloud_diy`. Tauscht zwei Cloud-DIY-Geräte gleichen Modells per Präfix-Rewrite. Liefert sofort den Kernnutzen für unsere Nutzer; null Risiko (eigener Code). UI: Service mit zwei Geräte-Selektoren + Trockenlauf/Vorschau, dann Bestätigung.
- **Phase 2 (Reichweite):** Generische Transplant-Engine als **eigenes Repo** (C), das *auch* native-Shelly-Geräte abdeckt (MAC-Präfix-Rewrite + Guard-Handling) und prinzipiell jede Integration mit einem bestätigten Sub-Entity-Mapping. Repair-/Wizard-Flow nach ESPHome-Vorbild (neue MAC erfassen, Mapping per `device_class`+Kanal+Name vorschlagen, Nutzer bestätigt).
- **Phase 3 (optional, "in Shelly selbst ablösen"):** LAN-RPC-Config-Klon als Zusatzschritt (nur Gen2+/Gen1 über lokales Netz). Klar kommuniziert: ohne LAN-Zugang und mit den o.g. nicht-übertragbaren Feldern (Passwörter, Kalibrierung, MAC).

## 6. Sicherheits-/UX-Leitplanken (für jede Phase)

- **Immer Vorschau + explizite Bestätigung** (welche entity_ids/Automationen betroffen sind) vor dem Schreiben.
- **Reihenfolge strikt:** neue Duplikate entfernen/Identifier freigeben → alte Identität umschreiben → reload. Kollisionen werfen `ValueError`.
- **Nur über Python-Registry-APIs im laufenden HA**, nie `.storage` live editieren.
- **Modell-/Layout-Gleichheit prüfen** (gleicher `device_code`/gleiche Kanalstruktur); Cross-Gen/Cross-Modell ablehnen.
- **Kein Secret-Risiko** in unserem Repo (HARD RULE bleibt).
- **Backup-Hinweis** vor dem Tausch (Registry-Mutation ist heikel).

## 7. Offene Entscheidungen (an den Betreiber)

1. **Packaging:** A (nur unsere Integration, schnell) vs. C (separates generisches Tool, deckt native-Shelly + alles ab) — bzw. der empfohlene Phasenweg A→C. (B nicht empfohlen.)
2. **Native-Shelly-Support** trotz Risiken (fremdes Schema, FW-Abhängigkeit) jetzt einplanen oder erst nach MVP?
3. **Shelly-seitiger Config-Klon** (Phase 3) überhaupt gewünscht, obwohl nur über LAN und mit Einschränkungen (keine Passwörter/Kalibrierung)?

## 8. Quellen (Auswahl)
- HA Dev Docs: entity_registry / device_registry index; `async_update_entity`, `async_update_device`, `async_get_or_create`.
- Architecture #1088 (replace-device abgelehnt), Core PR #142507 (ESPHome-Vorbild), #130833 (Shelly-MAC-Checks), Issue #125811 (Shelly-Tausch *not planned*), #167253 (LTS-Orphan).
- Native Shelly: `config_flow.py`, `__init__.py`, `entity.py`, `utils.py` (`get_*_device_info`, `format_ble_addr`, `MacAddressMismatchError`).
- Shelly API: Gen2 RPC (`Shelly.GetConfig`, `*.SetConfig`, `Schedule`, `Webhook`, `KVS`, `Script`, `Cover`), Cloud Control API v2 (nur read-settings/output-control), Gen1 `/settings`.
- Eigene Codebase: `entities/base.py:66-83`, unique_id-Formate je Plattform, `coordinator.py:178-219`, `__init__.py:_register_services`.
