# Shelly Cloud DIY — Home-Assistant-Integration

<img src="https://raw.githubusercontent.com/notDIRK/shelly-cloud-diy-ha/main/images/icon.png" alt="Shelly Cloud DIY" width="128">

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/notDIRK/shelly-cloud-diy-ha)](https://github.com/notDIRK/shelly-cloud-diy-ha/releases)

> 🇩🇪 **Deutsch (du bist hier)** · 🇬🇧 **English** — [`README.md`](README.md) ist die englische Primärfassung; diese Seite ist ihr originalgetreues Spiegelbild.

**Shelly Cloud DIY** verbindet Home Assistant über die Self-Service-**Cloud-
Control-API** mit deiner Shelly-Flotte — und erreicht damit die Geräte, die eine
rein lokale Integration nie sieht (geteilte Geräte, entfernte Standorte, die
Shelly-BLU-Familie hinter einem Gateway). Deine Steuerung bleibt dabei
**lokal-first**: die Cloud ist ein Overlay, nie im Steuerweg.

Verfügbar im **[HACS-Default-Store](https://hacs.xyz)** — einfach nach
*Shelly Cloud DIY* suchen, keine Custom-Repository-URL nötig.

---

## Warum diese Integration

Die offizielle Home-Assistant-Shelly-Integration ist hervorragend — und **die
solltest du für deine LAN-Geräte weiter nutzen**. Sie ist Sub-Sekunden-schnell
und funktioniert auch ohne Internet. Shelly Cloud DIY **ersetzt sie nicht**.
Sie schließt die Lücken, die die LAN-Integration nicht erreicht:

### Kein Gatekeeping — in 2 Minuten startklar

**Du richtest das selbst ein, sofort, ohne irgendwen um Erlaubnis zu fragen.**
Shelly-App öffnen, deinen Cloud-`auth_key` kopieren, in Home Assistant einfügen —
in rund zwei Minuten erledigt. Kein Antragsformular, keine Support-Mail, keine
Freigabe-Warteschlange, kein Warten auf ein „Ja" vom Hersteller. Dein Zugang
gehört dir.

Das ist wichtig, weil der *andere* Cloud-Weg — die **Integrator-API**, die
`engesin/shelly-integrator-ha` nutzt — einen Token braucht, den **Shelly
reglementiert**. In Shellys eigenen Worten: *„licenses for personal use are not
provided"* — als Privat-User musst du also einen Antrag stellen und bist auf
**Shellys Goodwill angewiesen** — und kannst schlicht abgelehnt werden. Dieses
Projekt bringt dich nie in diese Lage.

| | Dieses Projekt | Integrator-API-Weg |
|---|---|---|
| **Auth** | `auth_key`, den du selbst erzeugst (M1) / OAuth (M2) | Integrator-API-Token, **von Shelly reglementiert** |
| **Zugang bekommen** | **Self-Service, sofort** — in ~2 Min kopieren & einfügen | **Fragen, dann hoffen** — Antrag, keine Privat-User-Lizenz |

- **Geteilte Geräte.** Die Cloud Control API sieht Geräte, die andere Shelly-User
  mit deinem Account geteilt haben — etwas, das eine LAN-Integration strukturell
  nicht kann. (Empirisch verifiziert anhand einer echten ECOWITT-WS90-
  Wetterstation, die aus einem fremden Account geteilt war.)
- **Entfernte / reine Cloud-Geräte.** Geräte an einem anderen Standort oder
  Geräte, die nur über die Cloud erreichbar sind, werden in Home Assistant
  sichtbar.
- **Self-Service-Zugang — kein Gatekeeper.** Du erzeugst den `auth_key` in
  Sekunden selbst in der Shelly-App (OAuth ist der Weg in Meilenstein 2; so oder
  so Self-Service). Kein reglementierter Integrator-API-Token, kein
  Antragsformular, keine Support-Mail, keine Freigabe-Warteschlange. Dein Zugang
  hängt nie am willkürlichen „Ja" eines Herstellers gegenüber einem Privat-User.

> **Lokal-first, Cloud-optional.** Für lokal gesteuerte Geräte behältst du die
> native lokale `shelly`-Integration — sie ist schneller und ausfallsicher.
> Diese Integration ergänzt nur reine Cloud- und geteilte Geräte, entfernte
> Sichtbarkeit und Migrations-Werkzeuge. Die Cloud ist ein **Overlay für
> Sichtbarkeit**, nie eine Abhängigkeit, um das Licht einzuschalten. Das Anlegen
> von Entities ist **Opt-in**, damit du keine doppelten Steuer-Entities für
> Geräte bekommst, die du bereits lokal verwaltest.

### Ein konkretes Beispiel: eine geteilte Wetterstation

Stell dir vor, ein Nachbar oder ein Familienmitglied besitzt eine ECOWITT-WS90-
Wetterstation, die mit *seinem* Shelly-Account verknüpft ist, und teilt sie in
der Shelly-App mit dir. Die lokale HA-Shelly-Integration wird sie nie sehen — die
Hardware ist nicht in deinem LAN und gehört dir nicht. Mit Shelly Cloud DIY
taucht diese geteilte Station in Home Assistant als ganz normale Sensor-Entities
auf (Temperatur, Wind, Regen, UV, …), bereit fürs Dashboard oder für
Automationen — ohne dass du je mehr vom Account des Besitzers anfasst als die
Freigabe, die er dir erteilt hat.

<img src="https://raw.githubusercontent.com/notDIRK/shelly-cloud-diy-ha/main/images/shared-weather-station-ws90.png" alt="Home-Assistant-Dashboard einer geteilten ECOWITT-WS90-Wetterstation: Temperatur-, Gefühlt-wie- und Luftfeuchte-Anzeigen, Windkompass, 24-h-Temperatur- und Druckverlauf, Stationsbatterie und -spannung, Stunden- und 5-Tage-Vorhersage, UV- und Regensensoren" width="760">

*Eine geteilte ECOWITT-WS90-Wetterstation — verknüpft mit dem Shelly-Account einer anderen Person — erscheint über die Cloud Control API als native Home-Assistant-Sensor-Entities.*

### Vergleich

| Projekt | Auth | Realtime | Shared Devices | Gepflegt |
|---|---|---|---|---|
| **`shelly-cloud-diy-ha`** *(dieses Repo)* | `auth_key` (M1) / OAuth (M2) | HTTP-Poll 5 s → WebSocket-Push (M2) | ✅ | 🔄 aktiv |
| [`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha) | Integrator-API-Token *(von Shelly reglementiert — „licenses for personal use are not provided", Privat-User müssen einen Antrag stellen und können abgelehnt werden)* | WebSocket-Push | ❌ | ✅ |
| [HA Core — offizielle Shelly-Integration](https://www.home-assistant.io/integrations/shelly/) | Lokal per LAN / mDNS | LAN-Push | ❌ *(entfernte / geteilte Geräte übers LAN nicht erreichbar)* | ✅ |
| [`StyraHem/ShellyForHASS`](https://github.com/StyraHem/ShellyForHASS) | Lokal per LAN | LAN-Push | ❌ | ❌ **eingestellt** laut eigener README |
| [`vincenzosuraci/hassio_shelly_cloud`](https://github.com/vincenzosuraci/hassio_shelly_cloud) | Username/Passwort *(reverse-engineered)* | HTTP-Polling | ? | ❌ letzter Commit 2019 |
| [HA-YAML-Blueprint (2025)](https://community.home-assistant.io/t/controlling-shelly-cloud-devices-in-home-assistant/928462) | `auth_key` | ❌ nur Commands, **kein State-Read** | ? | ✅ |

Aktuell gibt es **keine gepflegte Home-Assistant-Integration**, die
**Cloud-Control-API-Zugriff**, **State-Lesen**, **Shared-Device-Support** und
**Gen1- / Gen2- / BLE-Gateway-Abdeckung** in einem Paket vereint. Genau diese
Lücke schließt dieses Projekt.

---

## Funktionen

| | Was sie macht | Status |
|---|---|---|
| ☁️ **Cloud-Polling** | Liest den Status jedes Geräts, das dein Shelly-Account sieht — eigene, geteilte, entfernte und BLE-überbrückte (Shelly-BLU-Familie über ein BLU-Gateway). | ✅ ausgeliefert |
| 🔌 **Opt-in-Entities** | Schalter, Lampen, Rollladen, Sensoren, Binary Sensors, Buttons — nur für die Geräte angelegt, die du auswählst, damit es keine Doppler mit der LAN-Integration gibt. | ✅ ausgeliefert |
| 📡 **Ausfall-Erkennung** | Ein **Reporting**-Sensor je Gerät, der abfällt, sobald sich ein Gerät nicht mehr meldet — das Signal, das `cloud.connected` nicht liefern kann (siehe unten). | ✅ ausgeliefert |
| ⚡ **Warnung bei klebendem Kontakt** | Meldet, wenn ein Relais sich als offen meldet, während die geräteeigene Messung weiter eine Last sieht — ein verschweißter Kontakt (siehe unten). | ✅ ausgeliefert |
| 🩺 **Gesundheitsprüfung** | Meldet, wenn ein Gerät heiß läuft, sein WLAN-Signal schwach ist oder ihm Speicher ausgeht — aus Daten, die der Abruf ohnehin liefert, ohne eine einzige zusätzliche Anfrage (siehe unten). | ✅ ausgeliefert |
| 📈 **Energie-Verlaufsimport** | Importiert historische Energiedaten in die Home-Assistant-Langzeitstatistik. | ✅ ausgeliefert |
| ⚙️ **Config- + Options-Flow** | `auth_key` einfügen; Poll-Intervall und Geräte-Auswahl später anpassen. | ✅ ausgeliefert |
| 🌍 **Lokalisierte UI** | Englische und deutsche Übersetzungen für jeden sichtbaren Text. | ✅ ausgeliefert |
| 🎨 **Brand-Icon** | Bringt ihr eigenes Brand-Icon mit (HA 2026.3+). | ✅ ausgeliefert |
| 🗺️ **Fleet-Map** | Read-only-Overlay, das Cloud-Geräte über die MAC ihren lokalen Twins zuordnet. | 🧪 Beta |
| 🔁 **Geräte-Tausch** | Überträgt die HA-Identität eines defekten Shellys auf ein neues Gerät desselben Modells. | 🧪 Beta |
| 🧹 **Konto-Aufräumen** | Findet (und entfernt optional) HA-Geräte, deren Shelly-Hardware nicht mehr in deinem Konto ist — verkauft, zurückgesetzt oder gelöscht. | 🧪 Beta |

Sie läuft **parallel** zur Shelly Cloud und zur Shelly-App — sie übernimmt oder
blockiert keine anderen Clients — und **erfordert nicht**, dass Home Assistant im
öffentlichen Internet exponiert ist.

### Mitbekommen, wenn ein Gerät stirbt

Jedes Gerät bekommt einen **Reporting**-Binärsensor (Kategorie Diagnose). Er fällt
ab, sobald sich das Gerät nicht mehr bei der Shelly Cloud meldet — genau so sieht
ein Stromausfall von der Cloud aus.

Damit klar ist, was das bringt und was nicht: für ein Gerät, das Home Assistant
im LAN erreicht, merkt die native Integration den Ausfall ohnehin — vermutlich
sogar früher. Die Cloud-Sicht lohnt sich für Geräte, zu denen Home Assistant
**gar keinen lokalen Weg** hat — ein geteiltes Gerät, ein zweiter Standort, ein
BLU-Sensor hinter fremdem Gateway — wo es sonst überhaupt kein Lebenszeichen
gibt. Sie ist **kein** Mittel, den eigenen Ausfall zu überstehen: liegt dein
Netz, erreicht auch Home Assistant die Cloud nicht, und der Sensor sagt das,
indem er nicht verfügbar wird, statt zu raten.

Es gibt ihn, weil die naheliegenden Signale nicht funktionieren. Gemessen an
einem realen Konto mit 64 Geräten:

- `cloud.connected` stand **13 Minuten** nach dem physischen Trennen eines Geräts
  noch auf *verbunden* — und zwar für *jedes* Gerät des Kontos, auch für seit
  Stunden stumme. Es ist ein Transport-Flag, das die Cloud zwischenspeichert,
  kein Lebenszeichen. (Der separate `Cloud`-Sensor zeigt den Rohwert weiterhin,
  standardmäßig deaktiviert.)
- Geräte verschwinden zwar irgendwann aus der Geräteliste der Cloud, das dauerte
  aber bis zu zehn Minuten — und die Liste lässt auch gesunde Geräte zufällig aus.

Die Bewertung stützt sich deshalb auf das Einzige, was die Cloud nicht erfinden
kann: dass das Gerät einen neuen Datensatz geschickt hat — gemessen an der Uhr
von Home Assistant.

**Das Fenster gilt je Gerät und passt sich an.** Die normalen Meldeabstände
unterscheiden sich um Größenordnungen: ein Zwischenstecker mit Leistungsmessung
meldet jede Minute, ein unbenutzter Plus RGBW PM lag bei 29 Minuten, ein
BLE-Sensor bei drei Tagen — alle völlig gesund. Der eingestellte Wert
(Optionen → *Gerät als offline melden nach*, Standard 30 min) ist deshalb ein
**Basiswert**: ein von Natur aus stilleres Gerät bekommt automatisch ein weiteres
Fenster, Batterie- und BLE-Geräte ein eigenes, und solange der Rhythmus eines
Geräts unbekannt ist, gilt eine Stunde Karenz. Ein kleinerer Wert beschleunigt
die Erkennung also bei Geräten, die durchgehend melden — etwa dem Zwischenstecker
an der Gefriertruhe — ohne die stillen Geräte zu Fehlalarmen zu machen.

Bricht das Abfragen selbst weg (Cloud-Ausfall, abgelehnter `auth_key`), wird der
Sensor **nicht verfügbar**, statt die ganze Flotte für tot zu erklären.

### Mitbekommen, wenn ein Relais nicht mehr abschaltet

Ein schaltender Shelly meldet im selben Payload, was er *befohlen* hat und was
er *misst*. Sagt das Relais „offen", während die Messung weiter eine Last sieht,
ist der Kontakt verschweißt — der Aktor nimmt weiter Befehle an und meldet
weiter *aus*, die Last schaltet aber nie wirklich ab. Alles, was auf dem
Abschalten beruht (eine Automation, ein Zeitplan, ein Bewegungsmelder), hört
damit unbemerkt auf zu funktionieren, und Home Assistant sagt kein Wort.

Jeder Schaltkanal, der seinen eigenen Ausgang misst, bekommt deshalb einen
Binärsensor **Relay fault** (Diagnose, Klasse `problem`), und beim Auslösen
erscheint eine Reparatur-Meldung. Die gemessene Leistung steht als Attribut
daneben — diese Zahl ist das ganze Argument.

Gebaut wurde das an einem Gerät, das genau so ausgefallen ist: ein Shelly 1PM
Mini Gen3 meldete gleichzeitig `output: false` und 85,2 W, so lange wie
beobachtet wurde, bei sichtbar brennender Lampe, per Software nicht mehr
abschaltbar. Zwei Dinge hat dieser Lauf gelehrt, beide stecken im Detektor:

- **Der Moment direkt nach einem Schaltbefehl lügt.** Ein Ausschalten lieferte
  rund 45 Sekunden lang 0 W, obwohl die Last nachweislich weiterlief. Der
  Widerspruch muss deshalb anhalten — fünf Meldungen *und* zwei Minuten —, bevor
  überhaupt etwas gesagt wird, und ein einzelner unauffälliger Messwert nimmt
  eine stehende Warnung **nicht** zurück. Zum Löschen braucht es fünf ruhige
  Minuten.
- **Der Energiezähler ist zu grob, um zu helfen.** Er stand 80 Sekunden still
  und sprang dann auf einmal um 1,0 Wh. Er geht nicht in das Urteil ein.

Gezählt werden die Meldungen des Geräts, nicht unsere Abfragen: ein
eingefrorenes Gerät liefert seinen letzten Payload immer wieder aus, und würden
Abfragen zählen, ließe sich aus einem einzigen alten Schnappschuss ein Vorwurf
zusammenzählen. Eine bereits ausgesprochene Warnung bleibt dagegen bestehen,
wenn ein Gerät verstummt — ein verschweißter Kontakt löst sich nicht dadurch,
dass das Gerät aus der Cloud fällt.

Bewusst zurückhaltend: unter 5 W wird nichts gemeldet (Snubber, LED-Treiber und
Messrauschen legen immer etwas auf einen offenen Kontakt), Geräte mit
Klemmwandler werden nie beurteilt, weil ihre Messung in keinem definierten
Verhältnis zu irgendeinem Kontakt daneben steht, und Gen1-Rollladengeräte
bleiben ganz außen vor — dort ist „Relais aus, Strom fließt" einfach ein
fahrender Rollladen. Sitzt ein Shelly in einer Wechselschaltung, wo der andere
Schalter Strom durch die Messung schicken kann, obwohl das Relais wirklich offen
ist, lässt sich der Detektor in den Optionen abschalten.


### Mitbekommen, wenn es einem Gerät schlecht geht

Jeder Abruf liefert ohnehin schon das WLAN-Signal jedes Geräts, die Temperatur,
die seine Komponenten über sich selbst melden, wie viel Speicher und
Dateisystem-Platz übrig ist, ob noch ein Neustart aussteht und ob eine
Komponente einen eigenen Fehler meldet. Bis v0.11.0 wurde nichts davon
ausgewertet — es lag im Payload und lief ins Leere.

Jetzt wird es ausgewertet, als **eine Reparatur-Karte für das ganze Konto**
statt einer pro Gerät, und es kostet **keine einzige zusätzliche Anfrage** an
die Shelly Cloud: die Daten waren ohnehin unterwegs.

| Prüfung | Warnung | Fehler |
|---|---|---|
| WLAN-Signal | -70 dBm | -85 dBm |
| Komponententemperatur | 70 °C | 85 °C |
| Freier Speicher / Dateisystem | unter 20 % | unter 10 % |
| Neustart steht noch aus | — | ja |
| Komponente meldet eigenen Fehler | — | ja |

Die Schwellwerte sind an einem echten Konto mit 64 Geräten gesetzt worden, nicht
im luftleeren Raum gewählt: dort meldet die Prüfung 19 Befunde auf 14 der 64
Geräte — weder still noch eine Lawine. Ein Befund muss drei geräteeigene
Meldungen *und* fünf Minuten überstehen, bevor er genannt wird; ein Gerät, das
nach einem Firmware-Update kurz warm ist, löst also keine Karte aus.

Drei bewusste Grenzen, denn eine Prüfung, die zu oft „Wolf" ruft, ist schlimmer
als gar keine:

- **Gewertet wird nur die Eigenwärme einer Komponente.** Ein externer Fühler am
  Shelly Add-on nicht: ein Fühler im Heizungsvorlauf oder in der Sauna ist
  bauartbedingt heiß, und die Daten geben nichts her, um das von einem
  überhitzten Gerät zu unterscheiden. Ein *defekter* Fühler wird trotzdem
  gemeldet — über die Komponentenfehler-Prüfung.
- **Bluetooth-Geräte (BLU) werden nur nach der einen Zahl beurteilt, die sie
  haben** — dem Signal, das ihr Gateway für sie meldet. Alle anderen Felder
  fehlen ihnen, und „unbekannt" darf nie als „krank" gelesen werden.
- **Gen1-Geräte werden gar nicht beurteilt.** Es lag kein Gen1-Payload vor, an
  dem sich ein Schwellwert prüfen ließe, und eine Prüfung, die niemand
  verifizieren kann, ist schlechter als keine.

**Ausstehende Firmware-Updates zählen nur als Befund, wenn du es einschaltest.**
Auf einem typischen Konto hat die Mehrheit der Geräte eines offen; eine Karte,
die dauerhaft leuchtet, ist eine Karte, die du nicht mehr liest — auch an dem
Tag, an dem sie ein Gerät bei 85 °C meldet.

Jedes Gen2+-Gerät bekommt außerdem einen **WLAN-Signal**-Sensor (Kategorie
Diagnose), und die Geräte-Diagnose enthält einen *coverage*-Abschnitt, der
benennt, welche Teile des Payloads bei uns noch gar keine Entität erzeugen — so
taucht eine Lücke in einem Fehlerbericht auf, statt darauf zu warten, dass sie
jemandem auffällt.

Die ganze Prüfung lässt sich in den Optionen abschalten.

---

## Voraussetzungen

- Ein Shelly-Cloud-Account mit mindestens einem verknüpften Gerät.
- Home Assistant **2024.1** oder neuer.
- Ausgehende HTTPS-Erreichbarkeit von Home Assistant zu `*.shelly.cloud`
  (Standard).
- Keine eingehende Internet-Erreichbarkeit auf die HA-Instanz nötig.

---

## Installation (HACS)

Die Integration ist im **HACS-Default-Store** — eine Custom-Repository-URL ist
nicht nötig.

1. **HACS** in Home Assistant öffnen.
2. Nach **Shelly Cloud DIY** suchen.
3. **Download** klicken und die neueste Version wählen.
4. Home Assistant neu starten.
5. Weiter mit *Credentials besorgen* und *Setup* unten.

> 🧪 **Du willst die Geräte-Tausch-Beta?** Fleet-Map und Geräte-Tausch werden in
> Beta-Releases ausgeliefert (`0.5.0-beta…`). In HACS das Drei-Punkte-Menü der
> Integration öffnen → **Erneut herunterladen** → **Beta-Versionen anzeigen**
> aktivieren, dann den neuesten `…-beta`-Build wählen. Beta-Funktionen sind
> **Opt-in**; die stabile Linie läuft unverändert weiter, wenn du auf einer
> Nicht-Beta-Version bleibst.

### Umstieg vom Custom Repository

Wenn du die Integration früher als HACS-*Custom-Repository* (unter der alten URL
`notDIRK/shelly-integrator-ha`) eingebunden hattest, ist der Wechsel auf den
Default-Store-Eintrag gefahrlos — die **Domain bleibt `shelly_cloud_diy`**, also
bleiben Config-Eintrag, Entities und damit deine Dashboards erhalten. Hinweise
aus einer echten Migration:

- **HACS entfernt den überflüssigen Custom-Repository-Eintrag automatisch**,
  sobald das Repo aus dem Default-Store kommt. Wenn dein Custom-Eintrag „von
  selbst verschwindet", ist das so gewollt — kein Fehler.
- Wenn HACS beim Entfernen fragt, ob **auch die Konfiguration entfernt** werden
  soll, **lehne das ab**. Diese Option löscht den Home-Assistant-Config-Eintrag
  (Geräte, Area-Zuordnungen, Entitäts-IDs). Nur den heruntergeladenen Code
  entfernen, den Config-Eintrag behalten.
- **Reihenfolge ist wichtig:** **erst** aus dem Default-Store neu herunterladen,
  **dann** Home Assistant neu starten. Nach dem Entfernen läuft die Integration
  noch im Speicher weiter (sieht funktionsfähig aus); ein Neustart in diesem
  Zustand markiert kurz alle Entities als `unavailable`.
- Die harmlose Log-Zeile *„custom integration shelly_cloud_diy which has not been
  tested by Home Assistant"* ist für jede `custom_components/`-Integration normal
  und kein Fehler.

### Aktualisieren

Wenn HACS eine neue Version anzeigt:

1. In **HACS** **Shelly Cloud DIY** öffnen → Drei-Punkte-Menü → **Erneut
   herunterladen** und die neueste Version wählen.
2. **Home Assistant neu starten** (Einstellungen → System → Neu starten).

Der Neustart ist der leicht vergessene Schritt. Neue Entities aus einem Release —
etwa ein Sensor für ein neu unterstütztes Gerät — **erscheinen erst nach dem
Neustart**. Eine fehlende Entity direkt nach dem Update ist daher fast immer
„HACS aktualisiert, aber Home Assistant noch nicht neu gestartet" und kein Fehler.
Fehlt eine Entity auch nach dem Neustart, erzwingt ein Reload der Integration
(Einstellungen → Geräte & Dienste → Shelly Cloud DIY → ⋮ → Neu laden) ein
frisches Cloud-Polling.

---

## Credentials besorgen

Die Cloud Control API ist Self-Service. Du musst Shelly nicht kontaktieren, kein
Formular ausfüllen und nicht auf eine Freigabe warten.

1. **Shelly-App** öffnen.
2. Zu **Benutzereinstellungen → Authorization cloud key** navigieren.
3. Auf **GET KEY** tippen.
4. Du bekommst zwei Werte: einen **`auth_key`** (langer undurchsichtiger String)
   und eine **Server-URI** (z. B. `shelly-42-eu.shelly.cloud`).
5. Beide Werte trägst du im Home-Assistant-Konfigurations-Dialog während des
   Setups ein.

> 🔐 **Sicherheit** — Der `auth_key` gibt Kontrolle über jedes Gerät, das dein
> Shelly-Cloud-Account sieht (inklusive geteilter). Behandle ihn wie ein
> Passwort. Zum Rotieren: Shelly-Passwort in der App ändern — der alte Key wird
> serverseitig invalidiert und ein neuer generiert.

Du installierst Code von einem Fremden und gibst ihm einen Zugang zum gesamten
Konto. Deshalb steht in **[Was diese Integration mit deinem Auth-Key macht](docs/AUTH_KEY.de.md)**,
wohin der Schlüssel geht, wo er liegt, wohin er nie geht — und wie du jede
einzelne dieser Aussagen in etwa zwei Minuten selbst nachprüfst, mit Befehlen,
die du tippst, statt mit einem Skript von mir. Dort stehen auch die Punkte, mit
denen ich nicht zufrieden bin; eine Seite, die nur gute Nachrichten aufzählt,
wäre das Lesen nicht wert.

---

## Setup

1. Home Assistant → **Einstellungen → Geräte & Dienste → Integration hinzufügen →
   "Shelly Cloud DIY"**.
2. `auth_key` einfügen.
3. Server-URI einfügen (z. B. `shelly-42-eu.shelly.cloud`).
4. **Absenden** klicken. Geräte werden sofort geladen; wähle aus, welche zu
   Entities werden.

---

## Rate-Limits und Latenz (offene Kommunikation)

Shelly dokumentiert ein Rate-Limit von **1 API-Request pro Sekunde pro Account**
([Quelle](https://shelly-api-docs.shelly.cloud/cloud-control-api/)). Die
Integration hält sich an dieses Budget, indem sie alle State-Abfragen in einen
einzigen `POST /device/all_status`-Aufruf pro Poll-Zyklus konsolidiert — ein
Request liefert den vollständigen Status aller für den Account sichtbaren Geräte.

| | Aktuell (Meilenstein 1) | Zukunft (Meilenstein 2) |
|---|---|---|
| Transport | HTTP-Polling | WebSocket-Push |
| State-Update-Latenz (p50 / p99) | ~2,5 s / ~5 s | < 100 ms / < 500 ms |
| Outbound-Traffic (ca. 50-Geräte-Account) | ca. 12 KB/s bei 5-s-Poll | ~0 Bytes steady |
| Commands (Schalter, Dimmen, Rollladen) | sofortiger HTTP-POST, unabhängig vom Poll-Takt | über WebSocket |
| Credentials | `auth_key` + Server-URI | Shelly-Mail + Passwort (OAuth2) |

Das Default-Poll-Intervall von 5 Sekunden bleibt deutlich unter dem 1-req/s-
Budget und behält Command-Reserve. Sensor-Werte (Temperatur, Energie, Wetterdaten)
fühlen sich live an; Schalt-Feedback im UI fühlt sich gemütlich an — der
WebSocket-Push in Meilenstein 2 schließt diese Lücke. Das Poll-Intervall ist im
Options-Flow zwischen 3 s und 60 s einstellbar.

Shelly weist außerdem darauf hin, dass die HTTP-Endpunkte *absichtlich nur grob
dokumentiert sind* und Parameterformate sich ändern können. Die Integration pinnt
auf die aktuelle v1-Endpunkt-Form und reagiert auf Änderungen, sobald sie
passieren — das ist aber ein echtes Langzeit-Risiko, das du kennen solltest.

---

## Fehlersuche

Wenn ein Gerät sich falsch verhält — falscher Zustand, ein Wert der sich nicht
ändert, eine Entität die nicht erscheint — ist der schnellste Weg zur Lösung,
die **Diagnose** dieses Geräts an einen Bug-Report anzuhängen.

1. **Einstellungen → Geräte & Dienste → Shelly Cloud DIY**
2. Das betroffene Gerät öffnen → **⋮ → Diagnose herunterladen**
3. Die `.json`-Datei an ein [neues Issue](https://github.com/notDIRK/shelly-cloud-diy-ha/issues/new/choose) anhängen

Der Download ist der rohe Shelly-Cloud-Status, aus dem die Integration den
Entitäts-Zustand baut — das macht aus Raterei meist einen präzisen Fix. Deine
Privatsphäre bleibt gewahrt: Gerätenamen und Netzwerk-Identifier (Name, SSID,
IP, MAC) werden automatisch geschwärzt, und die Datei listet unter
`redacted_keys` genau auf, *was* zurückgehalten wurde. Zusätzlich enthält sie
den Coordinator-Gesundheitsstand (letzter erfolgreicher Abruf und letzter
Fehler), um die Diagnose zu beschleunigen.

Geht es um den **Reporting**-Sensor — ein Gerät gilt als offline, ist aber
nachweislich in Ordnung, oder es schlägt nie an — beantwortet derselbe Download
das direkt: der `reporting`-Block nennt, wie lange das Gerät tatsächlich still
ist, wie viel Stille ihm gerade zusteht, ob das der eingestellte Wert ist oder
ein weiteres Fenster, das sich das Gerät verdient hat, und ob sein Rhythmus
überhaupt schon gelernt wurde.

Bei Fehlern hilft es, Debug-Logging für `custom_components.shelly_cloud_diy` zu
aktivieren (Einstellungen → Geräte & Dienste → Shelly Cloud DIY → Debug-
Protokollierung aktivieren) und das Problem zu reproduzieren.

---

## Roadmap

Vollständiger Plan mit Scope, Nicht-Zielen und Einschränkungen pro Meilenstein:
[`docs/ROADMAP.md`](docs/ROADMAP.md). Feature-Tiefenblicke:
[`docs/FEATURE-HIGHLIGHTS.de.md`](docs/FEATURE-HIGHLIGHTS.de.md). Was jedes
stabile Release geändert hat: [`CHANGELOG.md`](CHANGELOG.md).

### ✅ Meilenstein 1 — Cloud-Polling *(ausgeliefert, im HACS-Default-Store)*

HTTP-Polling mit `auth_key`; Opt-in-Entities pro Gerät; Support für geteilte /
entfernte / BLE-überbrückte Geräte; Energie-Verlaufsimport; englische + deutsche
UI.

### 🧪 Geräte-Tausch-Overlay *(Beta — `0.5.0-beta`, Opt-in)*

Ein **Opt-in-Beta**-Overlay für alle, die Shellys sowohl lokal als auch über die
Cloud betreiben. Installation über HACS **Beta-Versionen anzeigen** (siehe oben).
Noch nicht fertig — gern ausprobieren, aber noch nicht darauf verlassen.

- **Fleet-Map** — ein *read-only*-Overlay, das jedes Cloud-Gerät seinem lokalen
  Home-Assistant-Twin **über die MAC** zuordnet (sowohl WLAN-Shellys als auch
  Bluetooth- / BLU-Sensoren), Namen vorschlägt und jedes Gerät markiert, dessen
  *Steuerung* heimlich von der Cloud abhängen würde. Offline-Geräte sind
  eingeschlossen. Der Steuerweg wird nie angefasst.
- **Geräte-Tausch** — überträgt die Home-Assistant-Identität eines defekten
  Shellys (Entitäts-IDs, Gerät, Name, Bereich, Langzeit-Verlauf und jeden Bezug
  aus Automationen / Skripten / Szenen / Dashboards) auf ein **neues Gerät
  desselben Modells**, sodass du Home Assistant nach einem Hardware-Tausch nicht
  neu konfigurieren musst. **Cloud-Geräte werden schon heute unterstützt.** Der
  **Upstream-Beitrag an Home Assistant Core** — ein Geräte-Tausch-Repair nach dem
  Vorbild von ESPHome für die *native* Shelly-Integration — wurde **zur Review
  eingereicht**:
  [home-assistant/core#174581](https://github.com/home-assistant/core/pull/174581).
  Nativer lokaler Support in dieser Integration ist als Nächstes geplant.
- **Konto-Aufräumen** — gleicht die von dieser Integration angelegten Geräte mit
  deinem aktuellen Shelly-Konto ab und meldet jene, deren Hardware **nicht mehr
  im Konto** ist (verkauft, auf Werkseinstellungen zurückgesetzt oder in der App
  gelöscht). *Standardmäßig nur zur Information;* das Entfernen ist ausdrücklich
  optional, wird vorher angezeigt und ehrlich als nicht automatisch umkehrbar
  ausgewiesen. Sicher konzipiert: Abwesenheit wird nur aus dem **vollständigen
  Gerätebestand** deines Kontos entschieden — nie aus einer Namensliste — und bei
  unvollständig wirkendem Bestand wird nichts getan.
- **On-Device-Config-Klon** — *geplant.* Klont die On-Device-Zeitpläne, -Skripte
  und -Eingänge eines Shellys über das LAN auf den Ersatz, für Resilienz, die
  einen Internet- und einen Home-Assistant-Ausfall übersteht.

### 🔭 Meilenstein 2 — OAuth + WebSocket-Realtime *(geplant)*

Push-basierte Updates statt Polling: Sub-Sekunden-Latenz und ~0 Steady-State-
Traffic, durch Authentifizierung per OAuth und Abo der Shelly-Cloud-Events über
einen WebSocket.

---

## Lokal-first-Hinweis

Diese Integration ist bewusst ein **Overlay**. Die Cloud dient der *Sichtbarkeit*,
*geteilten/entfernten Geräten* und *Migrations-Werkzeugen* — **nie** als
Abhängigkeit im Steuerweg eines Geräts, das du bereits lokal steuern kannst. Für
solche Geräte behältst du die native lokale `shelly`-Integration: sie ist
Sub-Sekunden-schnell und funktioniert offline. Die Aufgabe von Shelly Cloud DIY
ist, das zu erreichen, was das LAN nicht kann — und dem aus dem Weg zu gehen, was
es kann.

---

## Wie ich arbeite

Ich betreue das hier allein, in meiner Freizeit, und ich arbeite mit KI. Das
verstecke ich nicht, denn es zählt das Ergebnis — und das ist überprüfbar: Die
Entscheidungen treffe ich, jede Änderung prüfe ich vor der Veröffentlichung, und
getestet wird gegen beide Enden der unterstützten Home-Assistant-Spanne, wo immer
der Fehler es zulässt an echter Hardware. Nichts geht raus, was ich nicht selbst
kontrolliert habe. Wenn etwas in diesem Code aussieht, als könnte ich es nicht
erklären: frag mich, ich antworte.

Was du davon hast, ist Tempo — und daran liegt mir am meisten. Ich hasse Warten.
Deshalb landen Benachrichtigungen über neue Issues auf meinem Handy, und über
alle bisher gemeldeten Issues hinweg bedeutete das im Median **unter vier Stunden
bis zur ersten Antwort** und **einen halben Tag bis zum Schließen** — meist mit
einer Beta, die du sofort installieren kannst. Der Issue-Verlauf ist öffentlich,
schau also lieber dort nach, statt mir zu glauben. Der langsamste Fall dauerte
fünf Wochen; das war ganz am Anfang, und er ist der Grund für die
Benachrichtigungen.

Zwei Einschränkungen: Das ist Freizeit und mein eigenes Geld — die Werkzeuge sind
nicht umsonst, und bezahlt werde ich dafür von niemandem. Und ich bin eine
Person, schnell ist also mein Anspruch, keine Zusage.

Mehr über mich: [github.com/notDIRK](https://github.com/notDIRK)

---

## Dem Projekt helfen

Am direktesten hilfst du völlig kostenlos: ein ⭐ auf dem Repo und ein klarer
Bug-Report oder Feature-Wunsch, wenn etwas hakt.

**Home Assistant Analytics** zu aktivieren (Einstellungen → System → *Analytics*)
lohnt sich ebenfalls — es ist anonym und Opt-in und hilft dem gesamten
HA-Ökosystem. Eine ehrliche Einschränkung: Home Assistant listet *neu
hinzugekommene* Custom-Integrationen nicht mehr in seiner öffentlichen
Pro-Integration-Statistik (Regeländerung in HA 2026.3 — die
[brands](https://github.com/home-assistant/brands)-Registry nimmt keine neuen
Custom-Integrationen mehr auf, und die Analytics-Seite zählt nur Domains aus
dieser Registry). Shelly Cloud DIY erscheint dort also nicht namentlich — aber es
hilft dem Projekt, in dem es steckt, und ein ⭐ bleibt das klarste Signal, dass
sich die Pflege dieser Integration lohnt.

---

## Lizenz

MIT — siehe [`LICENSE`](LICENSE).

---

## Fork-Herkunft

Geforkt von
[`engesin/shelly-integrator-ha`](https://github.com/engesin/shelly-integrator-ha)
(Integrator-API-Implementierung). Die Fork-Beziehung ist nur für
Git-History-Nachvollziehbarkeit erhalten; das Projekt hat die API gewechselt,
weitere Upstream-Merges sind nicht zu erwarten. Die Legacy-`v0.2.x`-Tags sind die
geerbte Integrator-API-Implementierung und bleiben nur aus Nachvollziehbarkeit
bestehen.
