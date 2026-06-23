# Shelly Cloud DIY — Home-Assistant-Integration

![Shelly Cloud DIY](images/icon.png)

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/notDIRK/shelly-cloud-diy-ha)](https://github.com/notDIRK/shelly-cloud-diy-ha/releases)

> 🇬🇧 **English:** The primary language of this project is English. See [`README.md`](README.md) for the English version.

> ℹ️ **Release-Stand:** `v0.4.x` ist die aktuelle Linie, basierend auf der **Cloud Control API** (self-service `auth_key`), und ist **im [HACS-Default-Store](https://hacs.xyz) verfügbar** — einfach nach *Shelly Cloud DIY* suchen, keine Custom-Repository-URL nötig. Die `v0.2.x`-Tags sind die Legacy-**Integrator-API-Implementierung** aus dem engesin-Upstream und bleiben nur aus Nachvollziehbarkeit bestehen. Roadmap: [`docs/ROADMAP.md`](docs/ROADMAP.md) · deutsche Fassung: [`docs/ROADMAP.de.md`](docs/ROADMAP.de.md) — Milestone 2 bringt OAuth + WebSocket-Realtime.

---

## Warum es diesen Fork gibt — und welche Lücke er schließt

Shelly Cloud stellt **zwei** Cloud-APIs bereit:

1. Die **Integrator API** (Tag `ITG_OSS`), die von der verbreiteten Community-Integration [`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha) genutzt wird. Shellys eigene Docs sagen wörtlich: *"Licenses for personal use are not provided."* ([Quelle](https://shelly-api-docs.shelly.cloud/integrator-api/)). Privatanwender scheitern typischerweise am Schritt „Shelly anschreiben und auf Zulassung warten" — siehe Upstream-[Issue #1](https://github.com/engesin/shelly-integrator-ha/issues/1).
2. Die **Cloud Control API**, die **Self-Service** ist: der User generiert einen `auth_key` direkt in der Shelly-App unter *Benutzereinstellungen → Authorization cloud key*. Kein Freigabeprozess, kein Warten, verfügbar für jeden Shelly-Cloud-Account.

Die Cloud Control API **sieht Geräte, die andere Shelly-User mit dem eigenen Account geteilt haben** (empirisch verifiziert anhand einer echten ECOWITT-WS90-Wetterstation, die aus einem Fremd-Account geteilt war). Der Consent-Flow der Integrator API kann das nicht, weil die Zustimmung pro Besitzer erteilt wird — ein Gerät, das du nur *geteilt* bekommen hast, gehört dir nicht und wurde vom Besitzer nie an einen Integrator freigegeben. Die offizielle Home-Assistant-Shelly-Integration wiederum ist LAN-only und erreicht entfernte oder geteilte Geräte gar nicht.

### Vergleich mit bestehenden Projekten

| Projekt | Auth | Realtime | Shared Devices | Gepflegt |
|---|---|---|---|---|
| **`shelly-cloud-diy-ha`** *(dieses Repo)* | `auth_key` (M1) / OAuth (M2) | HTTP-Poll 5 s (M1) → WebSocket-Push (M2) | ✅ | 🔄 aktiv |
| [`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha) | Integrator-API-Token *(reglementiert — keine Privat-User-Lizenz)* | WebSocket-Push | ❌ | ✅ |
| [HA Core — offizielle Shelly-Integration](https://www.home-assistant.io/integrations/shelly/) | Lokal per LAN / mDNS | LAN-Push | ❌ *(entfernte / geteilte Geräte übers LAN nicht erreichbar)* | ✅ |
| [`StyraHem/ShellyForHASS`](https://github.com/StyraHem/ShellyForHASS) | Lokal per LAN | LAN-Push | ❌ | ❌ **eingestellt** laut eigener README |
| [`vincenzosuraci/hassio_shelly_cloud`](https://github.com/vincenzosuraci/hassio_shelly_cloud) | Username/Passwort *(reverse-engineered)* | HTTP-Polling | ? | ❌ letzter Commit 2019 |
| [HA-YAML-Blueprint (2025)](https://community.home-assistant.io/t/controlling-shelly-cloud-devices-in-home-assistant/928462) | `auth_key` | ❌ nur Commands, **kein State-Read** | ? | ✅ |
| [`corenting/poc_shelly_cloud_control_api_ws`](https://github.com/corenting/poc_shelly_cloud_control_api_ws) | OAuth | WebSocket | ? | POC, keine Integration |

Aktuell gibt es **keine gepflegte Home-Assistant-Integration**, die **Cloud-Control-API-Zugriff**, **State-Lesen**, **Shared-Device-Support** und **Gen1- / Gen2- / BLE-Gateway-Abdeckung** in einem Paket vereint. Genau diese Lücke schließt dieser Fork.

---

## Was die Integration macht

Eine Home-Assistant-Custom-Integration, die:

- sich über den Self-Service-Pfad der Cloud Control API mit der Shelly Cloud verbindet.
- den Status **jedes Geräts liest, das dein Shelly-Cloud-Account sehen kann**, inklusive geteilter Geräte und inklusive BLE-überbrückter Geräte, die über ein Shelly-BLU-Gateway gemeldet werden.
- diese Geräte als Home-Assistant-Entities verfügbar macht — Schalter, Lampen, Rollladen, Sensoren, Binary Sensors, Buttons.
- keine Exponierung von Home Assistant im öffentlichen Internet erfordert (kein Inbound-Webhook in Meilenstein 1).
- parallel zur Shelly Cloud und zur Shelly-App läuft — sie blockiert keine anderen Clients.

---

## Voraussetzungen

- Ein Shelly-Cloud-Account mit mindestens einem verknüpften Gerät.
- Home Assistant **2024.1** oder neuer.
- Ausgehende HTTPS-Erreichbarkeit von Home Assistant zu `*.shelly.cloud` (Standard).
- Keine eingehende Internet-Erreichbarkeit auf die HA-Instanz nötig (Meilenstein 1).

---

## Credentials besorgen

Die Cloud Control API ist Self-Service. Du musst Shelly nicht kontaktieren, kein Formular ausfüllen und nicht auf eine Freigabe warten.

1. **Shelly-App** öffnen.
2. Zu **Benutzereinstellungen → Authorization cloud key** navigieren.
3. Auf **GET KEY** tippen.
4. Du bekommst zwei Werte: einen **`auth_key`** (langer undurchsichtiger String) und eine **Server-URI** (z.B. `shelly-42-eu.shelly.cloud`).
5. Beide Werte trägst du im Home-Assistant-Konfigurations-Dialog während des Setups ein.

> 🔐 **Sicherheit** — Der `auth_key` gibt Kontrolle über jedes Gerät, das dein Shelly-Cloud-Account sieht (inklusive geteilter). Behandle ihn wie ein Passwort. Rotation: Shelly-Passwort in der App ändern — der alte Key wird serverseitig invalidiert und ein neuer generiert.

---

## Rate-Limits und Latenz (offene Kommunikation)

Shelly dokumentiert ein Rate-Limit von **1 API-Request pro Sekunde pro Account** ([Quelle](https://shelly-api-docs.shelly.cloud/cloud-control-api/)). Die Integration hält sich an dieses Budget, indem sie alle State-Abfragen in einen einzigen `POST /device/all_status`-Aufruf pro Poll-Zyklus konsolidiert — ein Request liefert den vollständigen Status aller für den Account sichtbaren Geräte.

| | Meilenstein 1 (aktueller Scope) | Meilenstein 2 (Zukunft) |
|---|---|---|
| Transport | HTTP-Polling | WebSocket-Push |
| Standard-Latenz State-Updates (p50 / p99) | ~2,5 s / ~5 s | < 100 ms / < 500 ms |
| Outbound-Traffic (ca. 50-Geräte-Account) | ca. 12 KB/s im Mittel bei 5-s-Poll | 0 Bytes steady |
| Commands (Schalter, Dimmen, Rollladen) | sofortiger HTTP-POST, unabhängig vom Poll-Takt | über WebSocket |
| Benötigte Credentials | `auth_key` + Server-URI | Shelly-Mail + Passwort (OAuth2 mit `client_id=shelly-diy`) |

Das Default-Poll-Intervall von 5 Sekunden ist so gewählt, dass wir deutlich unter dem 1-req/s-Budget bleiben und Command-Reserve behalten. Sensor-Werte (Temperatur, Energie, Wetterdaten) fühlen sich live an; Schalt-Feedback im UI fühlt sich gemütlich an — der WebSocket-Push in Meilenstein 2 schließt diese Lücke. Das Poll-Intervall ist im Options-Flow zwischen 3 s (24 KB/s bei 58 Geräten) und 60 s einstellbar.

Shelly weist außerdem darauf hin, dass die HTTP-Endpunkte *absichtlich nur grob dokumentiert sind* und Parameterformate sich ändern können. Die Integration pinnt auf die aktuelle v1-Endpunkt-Form und reagiert auf Änderungen, sobald sie passieren — das ist aber ein echtes Langzeit-Risiko, das du kennen solltest.

---

## Installation (HACS)

Die Integration ist im **HACS-Default-Store** — eine Custom-Repository-URL ist nicht mehr nötig.

1. **HACS** in Home Assistant öffnen.
2. Nach **Shelly Cloud DIY** suchen.
3. **Download** klicken und die neueste Version wählen.
4. Home Assistant neu starten.
5. Weiter mit *Setup* unten.

### Umstieg vom Custom Repository

Wenn du die Integration früher als HACS-*Custom-Repository* (unter der alten URL
`notDIRK/shelly-integrator-ha`) eingebunden hattest, ist der Wechsel auf den Default-Store-Eintrag
gefahrlos — die **Domain bleibt `shelly_cloud_diy`**, also bleiben Config-Eintrag, Entities und damit
deine Dashboards erhalten. Hinweise aus einer echten Migration:

- **HACS entfernt den überflüssigen Custom-Repository-Eintrag automatisch**, sobald das Repo aus dem
  Default-Store kommt. Wenn dein Custom-Eintrag „von selbst verschwindet", ist das so gewollt — kein
  Fehler.
- Wenn HACS beim Entfernen fragt, ob **auch die Konfiguration entfernt** werden soll, **lehne das ab**.
  Diese Option löscht den Home-Assistant-Config-Eintrag (Geräte, Area-Zuordnungen, Entitäts-IDs). Nur
  den heruntergeladenen Code entfernen, den Config-Eintrag behalten.
- **Reihenfolge ist wichtig:** **erst** aus dem Default-Store neu herunterladen, **dann** Home Assistant
  neu starten. Nach dem Entfernen läuft die Integration noch im Speicher weiter (sieht funktionsfähig
  aus); ein Neustart in diesem Zustand markiert kurz alle Entities als `unavailable`.
- Die harmlose Log-Zeile *„custom integration shelly_cloud_diy which has not been tested by Home
  Assistant"* ist für jede `custom_components/`-Integration normal und kein Fehler.

---

## Setup

1. Home Assistant → **Einstellungen → Geräte & Dienste → Integration hinzufügen → "Shelly Cloud DIY"**.
2. `auth_key` einfügen.
3. Server-URI einfügen (z.B. `shelly-42-eu.shelly.cloud`).
4. **Absenden** klicken. Geräte werden sofort geladen und erscheinen als Entities.

---

## Roadmap-Kurzfassung

Vollständiger Plan mit Scope, Nicht-Zielen und Einschränkungen pro Meilenstein: [`docs/ROADMAP.md`](docs/ROADMAP.md) (Englisch) · [`docs/ROADMAP.de.md`](docs/ROADMAP.de.md) (Deutsch).

- ✅ **M0 Grundlage** — Fork, Security-Härtung, Pivot-Recherche und Verifikation, Repo-Umbenennung, CLOUD-DIY-Branding
- ✅ **M1 Cloud Control API + `auth_key` + HTTP-Polling** — ausgeliefert (`v0.4.x`)
- ⏳ **M2 OAuth + WebSocket-Realtime** — Push-basierte Sub-Sekunden-Updates
- ✅ **M3 HACS-Default-Store-Aufnahme** — gemerged ([hacs/default#7101](https://github.com/hacs/default/pull/7101)); die Integration liefert ihre eigenen `brand/`-Icons mit (HA 2026.3+), da `home-assistant/brands` keine Custom-Integration-Icons mehr annimmt
- 💡 **M4 HA-Core-Quality-Scale-Politur** — Diagnostics, Repairs, Testabdeckung

---

## Lizenz

MIT — siehe [`LICENSE`](LICENSE).

---

## Fork-Herkunft

Geforkt von [`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha) (Integrator-API-Implementierung). Die Fork-Beziehung ist nur für Git-History-Nachvollziehbarkeit erhalten; das Projekt hat die API gewechselt, weitere Upstream-Merges sind nicht zu erwarten.
