# Roadmap — Shelly Cloud DIY für Home Assistant

> 🇬🇧 **English:** The primary language of this project is English. See [`ROADMAP.md`](ROADMAP.md) for the English version.

## Projektziel

`shelly-cloud-diy-ha` ist eine Home-Assistant-Custom-Integration, die Home
Assistant über die **Cloud Control API** von Shelly anbindet — also über
den offiziellen Self-Service-Pfad, den Shelly ausdrücklich für DIY- und
Privat-User vorgesehen hat. Das Projekt existiert, weil die einzige
bisher verfügbare Community-Integration in diesem Themenfeld
([engesin/shelly-integrator-ha](https://github.com/engesin/shelly-integrator-ha))
die **Integrator API** nutzt, zu der Shelly wörtlich dokumentiert:
*"Licenses for personal use are not provided."* — dafür ist ein
kommerzieller Integrator-Freigabeprozess nötig, durch den die meisten
Privatanwender nie durchkommen.

Dieses Projekt ist ein Hard-Fork von oben genanntem Upstream, den ich nur
wegen Git-History-Nachvollziehbarkeit behalte. Weitere Upstream-Merges
sind nicht vorgesehen.

## Scope-Ziel

- **Erreicht:** Installierbar via **HACS** — die Integration liegt im
  HACS-Default-Store, eine Custom-Repository-URL braucht es nicht mehr.
- **Derzeit kein Ziel:** Aufnahme in **Home Assistant Core**. Ich halte
  den Code stilistisch Core-kompatibel (keine Personennamen im Quellcode,
  englische Logmeldungen, ordentliche Exception-Typen, Übersetzungen) —
  aber der volle Core-Qualitätsstandard wird nicht nach Plan ausgebaut.
  Einzelne Punkte daraus sind trotzdem eingezogen, weil sie für sich
  genommen nützlich waren — allen voran der sanitisierte
  Diagnostics-Export, der es überhaupt erst möglich macht, das Gerät eines
  Users aus einem Issue-Report heraus aus der Ferne zu debuggen.

## Meilensteine

Status: ✅ fertig · 🔄 in Arbeit · ⏳ geplant · 💡 angestrebt

> **Wo das Projekt steht (2026-09-05, `v0.11.0`):** Die Meilensteine 0, 1
> und 3 sind erledigt — die Integration ist released, liegt im
> HACS-Default-Store und ist längst über den ursprünglichen M1-Scope
> hinausgewachsen (Gen1-/Gen2-/Gen3-Geräte, BLU-Familie, Energiemessung,
> Virtual Components, Offline-Melder, Relais-Defekt-Melder, Repairs-Plattform
> und Gerätegesundheits-Checks). Meilenstein 2 hat sich geteilt: seine
> **Steuerungs**-Hälfte — Virtual Components auf eigenen Geräten über OAuth
> schalten — ist gebaut und wartet auf das nächste Release, seine ursprüngliche
> **Push**-Hälfte ist gemessen, enger als angenommen und nicht gebaut. Details
> im jeweiligen Abschnitt.

### Meilenstein 0 — Grundlage  ✅

- `engesin/shelly-integrator-ha` geforkt als `notDIRK/shelly-integrator-ha`.
- Security-Härtung: randomisierte Per-Install-Webhook-ID, SSRF-Schutz für
  Local-Gateway-URL, Webhook-Handler-Logging über `logger.exception`.
- Korrektheit: Deep-Merge bei partiellen StatusOnChange-Updates, toter
  30-s-Polling-Timer deaktiviert, WebSocket-Reconnect mit Jitter.
- Konsolidierte Codebase-Map unter `docs/CODEBASE_MAP.md` (Pre-Pivot-Stand).
- Zweisprachiger "Getting an API Token"-Abschnitt in der alten README
  (dokumentierte das Integrator-API-Beschaffungsproblem — nach dem Pivot
  weitgehend obsolet).
- Pivot-Recherche: verifiziert, dass die Shelly Cloud Control API
  geteilte Geräte sieht (mit einer echten ECOWITT WS90 getestet, die aus
  einem Fremd-Account geteilt ist); verifiziert, dass die
  Cloud-Control-API-WebSocket den `auth_key` ablehnt (`Token-Broken`,
  Close 4401) und OAuth braucht; bestätigt, dass HTTP-Polling mit
  `auth_key` den vollständigen Status aller Account-sichtbaren Geräte
  zurückgibt.
- Repo umbenannt zu `shelly-cloud-diy-ha`, Python-Domain zu
  `shelly_cloud_diy`, CLOUD-DIY-Branding in `images/icon.png`.
- Drei historische Release-Tags (`v0.1.0-notDIRK` … `v0.2.2-notDIRK`)
  bleiben auf ihren Integrator-API-Commits als Audit-Trail.

### Meilenstein 1 — Cloud Control API mit `auth_key` + HTTP-Polling  ✅

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
  `auth_key`-Form-Parameter).
- `config_flow.py` neu schreiben — User-Step fragt `auth_key` + `Server-URI`
  ab; kein Consent-Step mehr; Options-Flow entsprechend vereinfacht.
- `coordinator.py` auf Polling von `/device/all_status` umschreiben
  mit konfigurierbarem Intervall (3–60 s, Default 5 s), respektiert das
  dokumentierte 1-req/s-Rate-Limit (konsolidierter Single-Poll schlägt
  Per-Device-Fan-Out).
- Entfernen: Consent-Webhook-Flow (`services/webhook.py`,
  `core/consent.py`, Webhook-ID-Migrations-Logik in `__init__.py`),
  `api/websocket.py` (zurück in M2-Scope).
- Wiederverwenden: Device-State-Merge-Logik, Per-Platform-Entity-Klassen
  (sensor, switch, light, cover, button, binary_sensor),
  Entity-Descriptions, Historical-CSV-Service (Local-Gateway-Pfad bleibt
  unverändert).
- Hinzufügen: Entity-Mapping für BLE/Gateway-überbrückte Sensoren, die
  in `/device/all_status` mit `gen: "GBLE"` auftauchen
  (Shelly-BLU-Familie, Shelly BLU H&T, SBWS-90CM-Wetterstation etc. —
  eine Mapping-Tabelle, gekeyed auf `_dev_info.code`).
- Aktualisieren: Translations und `strings.json` für die neuen
  Config-Felder (`auth_key`, `server_uri` statt `integrator_token`);
  deutsche Übersetzung ergänzen (`translations/de.json`).
- Manifest: Bump auf `0.3.0`, `iot_class` auf `cloud_polling` umstellen
  (weil der Push-Mechanismus entfällt), ungenutzte
  `dependencies: ["webhook"]` entfernen.
- Release: `v0.3.0` getaggt ohne `-notDIRK`-Suffix — Ziel ist langfristig
  der HACS-Default-Store.

Nicht-Ziele in M1:
- Echtzeit / Sub-5-Sekunden-State-Update-Latenz (→ M2).
- OAuth-Authentifizierung (→ M2).
- Cloud-seitige historische Energiedaten (der bestehende
  Local-Gateway-Pfad bleibt; Cloud-Historie ist separater Spät-Scope,
  sofern machbar).

M1-Folge-Releases (alles innerhalb des `auth_key`-HTTP-Polling-Modells,
ohne OAuth):
- **v0.3.2** ✅ — Gen2/Gen3-Modellerkennung (`code` + `cloud.connected`
  aus dem Top-Level von `/device/all_status` lesen, nicht nur aus
  `_dev_info`).
- **v0.3.3** ✅ — User-gesetzte Gerätenamen per Cloud Control API v2:
  `POST /v2/devices/api/get` mit
  `{auth_key, ids, select:["settings"], pick:{settings:["sys"]}}` liefert
  `settings.sys.device.name` (Gen2) / `settings.name` (Gen1). Lazy,
  batched, nur für Online-Geräte, teilt sich das 1-req/s-Budget mit dem
  Haupt-Poll. Das ist der *geräte-lokale* Name (via Shelly-App / LAN-RPC
  gesetzt); in der Praxis fast immer identisch mit dem Shelly-Cloud-Label,
  aber nicht garantiert.
- **v0.4.0** ✅ — Opt-In-Anlage von Entities pro Gerät (siehe unten).

Alles nach v0.4.0 ist Geräte- und Plattform-Abdeckung, die im
ursprünglichen M1-Plan gar nicht vorgesehen war — getrieben vor allem
durch Issue-Meldungen von Usern: Gen2-Doppel-Rollladen, H&T-Gen3-Batterie-
Sensoren, RGBW2-Farbsteuerung, Plus-Uni-Impulszähler, Pro-3EM-
Energiemessung, BLU Door/Window und read-only Virtual Components. Die
Details pro Version stehen in den
[Releases](https://github.com/notDIRK/shelly-cloud-diy-ha/releases).

Opt-In-Entity-Anlage (v0.4.0):
- Das Default-Anlegen aller Entities aller gefundenen Geräte ist
  unfreundlich für User, die bereits die HA-Core-Shelly-Integration
  über LAN laufen haben — sie bekommen Duplikate. v0.4.0 ergänzt den
  Config-Flow um einen Device-Picker (mit "alle gleich anlegen"-Option
  für Greenfield-User) und einen Options-Flow-Schalter zum späteren
  Aktivieren/Deaktivieren. Der Coordinator pollt weiter die ganze Flotte
  (ein Request), aber Entities werden nur für aktivierte Geräte erzeugt.

Ausdrücklich dokumentierte Einschränkungen, die User kennen müssen:
- **1 Request pro Sekunde** Rate-Limit pro Shelly-Account (Shelly-Offizial-Doku).
- **Polling-Latenz** von 5 s (Default) bedeutet: Sensor-Werte hinken der
  Realität um bis zu ~5 Sekunden hinterher; Schaltbefehle gehen sofort
  raus, die Latenz betrifft nur die State-*Beobachtung*.
- **HTTP-Endpunkte sind laut Shelly absichtlich nur grob dokumentiert**
  (Shelly behält sich Parameterformat-Änderungen vor) — die Integration
  pinnt auf die aktuelle v1-Endpunkt-Form und reagiert auf Änderungen
  reaktiv.

### Meilenstein 2 — OAuth: Cloud-Steuerung für eigene Geräte, danach Push  🔄 (Steuerung gebaut, Push nicht)

**Das Ziel, neu formuliert.** Dieser Meilenstein startete als „Realtime-Push".
Ihn zu messen hat die Überschrift geändert. Push erwies sich als eng — dieselbe
OAuth-Sitzung erschließt dafür etwas, das die dokumentierte API überhaupt nicht
kann: auf ein Gerät zu **schreiben**. Der Meilenstein hat deshalb jetzt zwei
Hälften, und die wertvolle ist nicht mehr die, nach der er benannt ist.

#### 2.1 Cloud-Steuerung für eigene Geräte — ausgeliefert in v0.12.0  ✅

Standardmäßig aus. Was sie leistet und was sie kostet:

- Manches, was ein Shelly kann, hat in der dokumentierten Cloud-Control-API
  **keine Route**. Die Zonen eines Bewässerungscomputers oder der Boolean, den
  ein Skript bereitstellt, sind *Virtual Components*: lesbar, nicht schreibbar.
  Jede dokumentierte Schreib-Route antwortet „diese Route gibt es nicht" —
  gemessen, mit einem funktionierenden `set/switch`-Aufruf auf demselben Gerät
  als Gegenprobe.
- Schreiben *geht* über dasselbe Cloud-WebSocket-Relay, das die Shelly-App
  benutzt — ein generisches JRPC-Relay. `Boolean.Set` auf einer Virtual
  Component hat darüber an echter Hardware funktioniert.
- Das Relay routet **nur zu Geräten, die dem Konto gehören**. Ein geteiltes
  Gerät wird mit `WRONG_ID` abgelehnt, und eine absichtlich kaputte ID bekommt
  dieselbe Ablehnung — es ist also eine Routing-Grenze, kein Formatfehler.
  Eigentum ist im Poll-Payload nirgends erkennbar, deshalb wird jedes Gerät mit
  einer solchen Komponente einmal pro Sitzung (nie pro Poll) per
  `Shelly.GetDeviceInfo` gefragt, und das Urteil steht in der Diagnose — „warum
  hat mein Gerät keinen Schalter" soll aus einem Fehlerbericht beantwortbar
  sein.
- Der Kanal ist **undokumentiert**, und Shellys Support hat am 2026-07-27
  klargestellt, dass undokumentierte Endpunkte nicht Teil der unterstützten API
  sind. Deshalb ist die Steuerung **Opt-in und standardmäßig aus**.
  Einschalten fragt nach der Kontoanmeldung, die das Relay braucht; das
  Passwort wird einmal benutzt und nie gespeichert, gespeichert wird nur das
  entstehende Token — und die Option wieder auszuschalten löscht es.
- Der neue Schalter entsteht **neben** dem bestehenden schreibgeschützten
  Sensor derselben Komponente, nie an seiner Stelle — ihn zu ersetzen würde
  bestehende Automatisierungen stumm brechen.
- Fehler sind laut. Ein abgelehnter Befehl wirft, statt Erfolg zu melden; der
  Zustand danach kommt aus dem nächsten Poll statt aus einer optimistischen
  Annahme; und wenn der Steuerkanal selbst weg ist, wird der Schalter
  `unavailable`, statt bedienbar auszusehen.
- **Der Poll bleibt unangetastet.** Mit ausgeschalteter Option ändert sich an
  der Integration nichts: keine Anmeldung, keine zweite Verbindung, keine
  Sonde, keine neue Entität.

Vor dem Release an echter Hardware bestätigt, an einer laufenden
Home-Assistant-Installation gegen ein echtes Konto: der Schalter entsteht neben
dem schreibgeschützten Sensor, der Befehl erreicht das Gerät in rund zwei
Sekunden — gegengeprüft von einer zweiten, unabhängigen Integration, die
dasselbe Gerät über das lokale Netz beobachtet — und der Zustand der Entität
folgt aus dem nächsten Poll statt aus einer Annahme. Die Diagnose meldete das
Gerät als eigenes, keines unklassifiziert. Eine Einschränkung deutlich gesagt:
dieser Lauf meldete sich mit einem vorab erzeugten Token an, das
Anmeldeformular selbst ist also durch Tests gedeckt, nicht durch diesen Lauf.

[Issue #20](https://github.com/notDIRK/shelly-cloud-diy-ha/issues/20) bleibt
trotzdem offen, bis sein Melder es an dem Bewässerungscomputer bestätigt, der
die Arbeit ausgelöst hat — ein Gen3-Relais ist kein FK-06X.

#### 2.2 Realtime-Push — gemessen und bewusst nicht die Überschrift

Der OAuth-WebSocket streamt `Shelly:StatusOnChange` tatsächlich im
Sub-Sekunden-Bereich, ganz ohne Subscribe-Frame — aber nur für Geräte, die dem
Konto **gehören**. Für ein dem Konto nur **geteiltes** Gerät antworten
Status-Requests mit `WRONG_ID`, Subscribe-Versuche mit `BAD_REQUEST`, und
passives Mithören liefert überhaupt keine Frames. Batterie- und BLU-Geräte
schlafen und pushen nie, unabhängig davon, wem sie gehören. Die Geräte-ID im
Push-Frame ist dezimal, während das HTTP-Inventar hexadezimal ist;
`dezimal == int(hex_mac, 16)` bildet beides aufeinander ab (BLE-gebrückte
`XB…`-IDs bleiben auf beiden Seiten Zeichenketten).

Die Folge, die die ursprüngliche Überschrift erledigt hat: der Poll ist **eine
kontoweite Anfrage**, nicht eine pro Gerät. Solange auch nur ein geteiltes oder
schlafendes Gerät existiert, fällt keine Anfrage weg — Push kann das
Poll-Intervall *lockern*, nie den Poll ersetzen. Damit ist Push eine
Latenzverbesserung vor einem unveränderten Poll: bauenswert, aber keine
Überschrift. Gebaut ist er nicht.

#### 2.3 Anmelden statt Schlüssel einfügen — gemessen, nicht gebaut

Ein Nebenprodukt der Steuerungsarbeit, festgehalten, bevor es verloren geht: der
OAuth-Access-Token, der für das Relay erzeugt wird, funktioniert auch auf der
**dokumentierten HTTP-API** — als Bearer-Header:

| Anfrage | Antwort |
|---|---|
| `POST /device/all_status`, `Authorization: Bearer <access_token>` | **200**, der komplette Kontoschnappschuss |
| derselbe Token als `auth_key=`-Body-Parameter | 401 `invalid_token` |

Gemessen am 2026-09-05 an einem echten Konto. Eine Installation ließe sich also
im Prinzip mit **einer Anmeldung und ganz ohne `auth_key`** einrichten statt mit
einem Schlüssel *und* — für die Steuerung — einer Anmeldung.

Zwei Gründe, warum es trotzdem nicht gebaut wird, in dieser Reihenfolge: es
brächte einen **zweiten Authentifizierungsweg in den Poll**, also in den einen
Teil dieser Integration, der noch nie kaputt war; und der Gewinn ist Bequemlichkeit
beim Einrichten, keine neue Fähigkeit. Das einzige echte Argument dafür ist eine
Fehlerklasse, die dadurch verschwindet: ein gespeicherter `auth_key` wird
serverseitig ungültig, sobald das Kontopasswort geändert wird, und niemand sagt
es dem Nutzer — die Integration antwortet ab da einfach mit 401. Ein Token, der
sich selbst erneuert, hat dieses Problem nicht.

Nicht-Ziele in M2:
- Per-Device-Webhook-Subscriptions (das Relay liefert bereits alles).
- Ein MQTT-Weg. Home Assistant bringt bereits eine MQTT-Integration mit; eine
  zweite in dieser hier wäre doppelter Code mit der schlechteren Geschichte.

### Meilenstein 3 — HACS-Default-Store-Aufnahme  ✅

**Ziel:** Eintrag in der [HACS-Default-Integration-Liste](https://github.com/hacs/default),
damit User die Integration nicht mehr über Custom-Repository-URL
hinzufügen müssen.

**Erledigt** — die Integration liegt im HACS-Default-Store und lässt sich
ohne Custom-Repository installieren. Ein Wermutstropfen bleibt: Das Icon in
der HACS-Übersichtsliste ist aus diesem Repo heraus nicht reparierbar (der
Weg über `home-assistant/brands` gilt seit HA 2026.3 nicht mehr für
Custom-Integrations). Stattdessen liefert die Integration einen lokalen
`brand/`-Ordner mit, den HA für die Geräte- und Entity-Seiten ausspielt.

Voraussetzungen (alle erfüllt):
- Logo-Submission an [home-assistant/brands](https://github.com/home-assistant/brands)
  als `core_integrations/shelly_cloud_diy/{icon.png,logo.png}` —
  bereinigte Varianten ohne `notDIRK`-Wordmark und Fork-Symbol werden zu
  diesem Zeitpunkt generiert.
- Erstes stabiles Release-Tag (ohne `-dev`).
- README finalisiert, besteht das HACS-Style-Review.
- Issue-Tracker mit mindestens ein paar geschlossenen / triagierten
  Issues (um aktive Wartung zu zeigen).
- Optional: simpler GitHub-Actions-CI, der Lint und vorhandene Tests bei
  Push / PR laufen lässt.

### Meilenstein 4 — Quality-Scale-Ausbau  🔄 (teilweise erledigt)

Pfad zu HA-Core-Quality-Scale `silver` / `gold`:
- ✅ `async_get_config_entry_diagnostics` für sanitisierten Export —
  ausgeliefert (`diagnostics.py`), und genau das macht Remote-Triage von
  Issues überhaupt erst möglich.
- ✅ Reparatur-Hinweise für aktionable Zustände — mit v0.8.0 ausgeliefert
  und seither gewachsen: anhaltendes Rate-Limit, aus dem Konto
  verschwundene Geräte, fehlgeschlagener Verlaufsimport, verschweißter
  Relaiskontakt (v0.9.0) und die Gesundheits-Schwellwerte (v0.11.0). Fünf
  Karten, alle rein informativ und alle pro Config-Entry aggregiert.
  ⚠ Das Modul heißt `repair_issues.py`, nicht `repairs.py`: auf HA 2025.1.4
  weist der Plattform-Loader eine `repairs.py` ohne
  `async_create_fix_flow` zurück — und keiner dieser Zustände lässt sich
  ohnehin aus Home Assistant heraus beheben.
- ⏳ Testabdeckung ≥ 70 %.
- 🔄 CI: Lint, Type-Check (mypy), Test-Matrix gegen unterstützte
  HA-Versionen — in GitHub Actions laufen heute hassfest + HACS-Validation;
  die lokalen Testläufe decken das älteste und das neueste unterstützte
  HA-Release ab.

(Kein fester Zeitplan — abhängig davon, ob Core-Submission wirklich
Ziel wird.)

## Abgrenzung zu bestehenden Projekten

| Projekt | Auth | Realtime | Shared Devices | Gepflegt | Notizen |
|---|---|---|---|---|---|
| **`notDIRK/shelly-cloud-diy-ha`** (dieses Repo) | `auth_key` (ausgeliefert) / OAuth (M2, gebaut) | HTTP-Poll 5 s heute; WebSocket-Push für eigene Geräte geplant, mit Poll-Fallback | ✅ | 🔄 aktiv | Volle Gen1- + Gen2- + Gen3- + BLE-Gateway-Abdeckung |
| [`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha) | Integrator-API-Token (von Shelly reglementiert) | WebSocket-Push | ❌ (Consent-Flow ist pro Besitzer) | ✅ aktiv | Privatanwender bekommen den Token typischerweise nicht |
| [`home-assistant/core` Shelly-Integration](https://www.home-assistant.io/integrations/shelly/) | Lokal per LAN (mDNS / direkte IP) | LAN-Push | ❌ (entfernte / geteilte Geräte übers LAN nicht erreichbar) | ✅ vom HA-Core-Team gepflegt | Mainstream; braucht LAN-Erreichbarkeit |
| [`StyraHem/ShellyForHASS`](https://github.com/StyraHem/ShellyForHASS) | Lokal per LAN | LAN-Push | ❌ | ❌ *"ShellyForHass will no longer receive further development updates"* laut eigener README | In HA Core aufgegangen |
| [`vincenzosuraci/hassio_shelly_cloud`](https://github.com/vincenzosuraci/hassio_shelly_cloud) | Username/Passwort (reverse-engineered Browser-Calls) | HTTP-Polling | ? | ❌ letzter Commit 2019 | Nur Switches; README warnt, dass HTTP-Parsing fragil ist |
| [HA-YAML-Blueprint](https://community.home-assistant.io/t/controlling-shelly-cloud-devices-in-home-assistant/928462) | `auth_key` (wie dieses Projekt) | ❌ nur Commands | ? | ✅ Community-maintained | *"The device state is not updated from the cloud"* — State ist nicht lesbar |
| [`corenting/poc_shelly_cloud_control_api_ws`](https://github.com/corenting/poc_shelly_cloud_control_api_ws) | OAuth | WebSocket-Push | ? | Explizit als POC markiert, keine Integration | Referenz-Implementierung für den M2-OAuth-Flow hier |

Kurzfassung: Aktuell existiert **keine andere gepflegte HA-Integration,
die Cloud-Control-API-Zugriff MIT State-Read UND Shared-Device-Support
UND Gen1/Gen2/Gen3/BLE-Abdeckung kombiniert**. Diese Lücke ist real und der
Grund, warum es dieses Projekt überhaupt gibt.

## Rate-Limits, Latenz, ehrliche Erwartungen

**Shellys dokumentiertes Rate-Limit:** 1 API-Request pro Sekunde pro
Account (Quelle: [Shelly Cloud Control API Docs, Getting Started](https://shelly-api-docs.shelly.cloud/cloud-control-api/)).

**Traffic-Profil in Meilenstein 1:**
- Ein einzelner `POST /device/all_status` liefert den kompletten State-
  Snapshot aller Geräte, die dein Account sieht (eigene + geteilte +
  BLE-überbrückte). Bei 58-Geräte-Accounts ca. 60 KB pro Request.
- Default-Poll-Intervall: 5 s → durchschnittlich ca. 12 KB/s
  Outbound-HTTPS. Konfigurierbar bis runter auf 3 s (24 KB/s bei 58
  Geräten) für snappieren State oder hoch bis 60 s für
  Low-Traffic-/Battery-Setups.
- User-initiierte Befehle (Schalter an/aus, Dimmen, Rollladen) werden
  sofort per separatem HTTP-POST abgesetzt, nicht erst beim nächsten
  Poll. Commands und Polls teilen sich das 1-req/s-Budget, das
  Default-5-s-Intervall lässt also ca. 4 req/s Command-Headroom.
- Beobachtete State-Change-Latenz: **p50 ≈ 2,5 s, p99 ≈ 5 s** bei
  Default-Poll. Für Wetterstation / Energie-Metering irrelevant; für
  Licht-Schalter-Feedback fühlt sich das gemütlich an.

**Traffic-Profil in Meilenstein 2 (zweimal revidiert — und die zweite
Revision ist die wichtige):**
- Outbound-Poll-Traffic: **Push reduziert ihn überhaupt nicht**, es sei denn,
  du lockerst das Intervall selbst. Der Poll ist *eine kontoweite Anfrage*, die
  alle Geräte auf einmal abdeckt; schon ein einziges geteiltes oder schlafendes
  Gerät hält diese eine Anfrage nötig, es fällt also nichts weg. Was Push
  einbringt, ist, dass ein *längeres* Poll-Intervall vertretbar wird — die
  Ersparnis macht der Nutzer, nicht der Code. Eine frühere Fassung dieses
  Dokuments versprach "0 Bytes Steady State", eine spätere "reduziert, nicht
  eliminiert"; beide sind älter als diese Rechnung und beide waren falsch.
- Latenz: **< 100 ms** für eigene Geräte am Netzteil; für geteilte und
  schlafende bleibt es bei der Poll-Latenz (p50 ≈ 2,5 s).
- Kosten: eine persistente WebSocket-Verbindung pro HA-Instanz plus ein
  Token-Refresh etwa einmal täglich. Die Cloud-Steuerung zahlt diese Kosten
  bereits, wenn sie eingeschaltet ist; Push käme ohne zweite Verbindung aus.

## Security und Datenhaltung

- Der `auth_key` wird in `entry.data` gespeichert (Home-Assistant-
  Standard-Config-Entry-Storage, Klartext auf Disk unter
  `.storage/core.config_entries`). Der Key gibt weitreichende Kontrolle
  über deine Geräte — behandle ihn wie ein Passwort.
- Er wird in der Shelly-App unter **Benutzereinstellungen →
  Authorization cloud key** angezeigt. Ein Passwort-Wechsel bei Shelly
  invalidiert ihn serverseitig — das ist die vorgesehene
  Rotations-Methode.
- Meilenstein 1 speichert weder Mail noch Passwort.
- Die Cloud-Steuerung (Meilenstein 2) sendet `sha1(passwort)` einmalig bei der
  Anmeldung an `api.shelly.cloud/oauth/login`. In `entry.data` liegt danach der
  entstandene Datensatz — Access-Token, Refresh-Token, Ablaufzeitpunkt — und
  sonst nichts. Das Passwort selbst wird nie gespeichert, und die
  Cloud-Steuerung wieder auszuschalten löscht den Datensatz.
