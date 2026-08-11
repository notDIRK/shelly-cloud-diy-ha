# Was diese Integration mit deinem Shelly-Auth-Key macht

*[English version](AUTH_KEY.md)*

Diese Integration fragt dich nach einem Shelly-Cloud-Auth-Key. Dieser Schlüssel
kann viel, und du installierst hier Code von einem Fremden aus dem Internet.
Diese Seite sagt dir genau, wohin der Schlüssel geht, wo er liegt, wohin er nie
geht — und wie du jede einzelne dieser Aussagen in etwa zwei Minuten selbst
nachprüfst, ohne irgendetwas auszuführen, das ich geschrieben habe.

Wenn du nur einen Absatz liest: der Schlüssel wird in **einer einzigen Datei**
benutzt, geht **ausschließlich an die Serveradresse, die du bei der Einrichtung
eingetragen hast**, steht in keinem Log, taucht in keiner Diagnose-Datei auf und
geht weder an mich noch an irgendeinen Dritten. Die unangenehmen Teile stehen
weiter unten, und es gibt welche.

---

## Was der Schlüssel ist und was er kann

Der Auth-Key stammt aus der Shelly-App unter *Benutzereinstellungen →
Autorisierungs-Cloud-Key*. Er ist ein **Zugang zum gesamten Konto**: alles, was
du in der App kannst, kann auch der Schlüssel — jedes Gerät des Kontos lesen und
jedes Gerät des Kontos schalten. Es gibt keine Nur-Lesen-Variante und keine
Beschränkung auf einzelne Geräte. Das ist Shellys Entwurf, keine Entscheidung
dieser Integration.

**Widerrufen:** ändere das Passwort deines Shelly-Kontos. Der Schlüssel wird
serverseitig neu erzeugt, der alte ist damit sofort wertlos. Einen eigenen
„Schlüssel widerrufen"-Knopf gibt es nicht.

Weil sich ein Schlüssel nicht einschränken lässt, ist das vernünftige
Gedankenmodell: *dieser Schlüssel ist dein Shelly-Passwort in anderer Form.*
Alles Weitere folgt daraus, das ernst zu nehmen.

---

## Wohin der Schlüssel geht

Genau eine Datei fasst ihn überhaupt an:
`custom_components/shelly_cloud_diy/api/cloud_control.py`.

Selbst nachsehen:

```bash
grep -rn "_auth_key" custom_components/shelly_cloud_diy/
```

Fünf Treffer, und es lohnt sich zu wissen, was jeder davon ist:

| Was es ist | Wo |
|---|---|
| auf der Client-Instanz abgelegt | `ShellyCloudControl.__init__` |
| **gesendet** — Formularfeld, v1-Endpunkte | `_post` |
| **gesendet** — JSON-Body-Feld, v2-Metadaten | `get_device_configs` |
| **gesendet** — Query-Parameter, v2-Cover | `roller_control` |
| nicht der Schlüssel — ein Textvergleich auf eine Fehlermeldung | Fehlerbehandlung in `_post` |

Es sind also **drei** Stellen, die ihn übertragen. Jede Übertragung steht
ausgeschrieben an der Aufrufstelle; die Hilfsmethoden hängen **nicht** still
Zugangsdaten an. Genau das macht den `grep` oben vollständig statt bloß
ungefähr.

Alle drei senden an `self._base_url`. Dieser Wert entsteht einmalig in
`_normalise_base_url` aus der Serveradresse, die **du** bei der Einrichtung
eingetragen hast (z. B. `shelly-42-eu.shelly.cloud`, in der App auf derselben
Seite wie der Schlüssel). Es gibt im Code kein zweites Ziel für den Schlüssel und
keinen fest verdrahteten Ersatzhost.

Damit du dich überzeugen kannst, dass es überhaupt keinen anderen ausgehenden
Verkehr gibt:

```bash
grep -rnE "session\.(get|post|request)|ws_connect|https?://" custom_components/shelly_cloud_diy/
```

Das einzige Nicht-Shelly-Ziel, das dabei auftaucht, ist eine Gateway-URL für den
CSV-Import, die **du** selbst konfigurierst — siehe unten.

---

## Wohin der Schlüssel nicht geht

**Nicht ins Log.** Debug-Logging für diese Integration einzuschalten gibt den
Schlüssel nicht aus. Die Logzeilen, die `auth_key` überhaupt erwähnen, sind
Meldungen *über* eine Ablehnung („auth_key rejected — skipping"), nie der Wert
selbst.

```bash
grep -rn "_LOGGER" custom_components/shelly_cloud_diy/ | grep -i "auth\|key\|token"
```

**Nicht in die Diagnose-Datei.** Das Diagnose-Modul liest die gespeicherten
Daten des Config-Eintrags gar nicht an; es exportiert nur den Geräte-Schnappschuss
und die Fleet-Map, mit geschwärzten Namen, IPs, MACs und SSIDs. Die Datei ist
rund 130 Zeilen lang, also kurz genug zum vollständigen Lesen:
`custom_components/shelly_cloud_diy/diagnostics.py`. Das ist wichtig, weil
Diagnose-Dateien genau das sind, was Leute an öffentliche Fehlerberichte hängen.

**Nicht an mich und an keinen Dritten.** Es gibt keine Telemetrie, keine
Analytics, kein Crash-Reporting, kein „Nach-Hause-Telefonieren". Die Integration
deklariert in `manifest.json` genau zwei Abhängigkeiten: `aiohttp`, Teil von
Home Assistants eigenem Unterbau, und `aioshelly`, dieselbe Bibliothek, die auch
die eingebaute Shelly-Integration benutzt. Nichts Exotisches, nichts von mir.

**Nicht an das CSV-Gateway.** Die Integration kann Energie-CSVs von einer
Gateway-URL holen, die du selbst angibst. Diese Anfrage ist ein schlichtes `GET`
**ohne jede Zugangsinformation**, und die URL wird vorher geprüft: andere
Protokolle und Loopback-Ziele werden abgelehnt, die Integration lässt sich also
nicht auf dein eigenes Home Assistant zurückrichten (`utils/http.py`,
`validate_gateway_url`).

---

## Wo der Schlüssel liegt — der Teil, den ich lieber nicht schreiben würde

Home Assistant speichert die Daten von Config-Einträgen in
`<config>/.storage/core.config_entries`, **im Klartext**. Dein Shelly-Schlüssel
steht unverschlüsselt in dieser Datei, genau wie die Zugangsdaten jeder anderen
Integration, die du installiert hast.

So arbeitet Home Assistant für alle Integrationen; es gibt keinen unterstützten
Weg, sich davon auszunehmen, und ich werde nicht so tun als ob. Die praktischen
Folgen musst du selbst abwägen:

- wer dein Home-Assistant-Konfigurationsverzeichnis lesen kann, kann den
  Schlüssel lesen
- dasselbe gilt für jedes **Backup** dieses Verzeichnisses, auch für
  automatische und für alles, was in einen Cloud-Speicher hochgeladen wird
- wenn du ein Backup zur Fehlersuche weitergibst — an wen auch immer, mich
  eingeschlossen — geh davon aus, dass der Schlüssel mitgegangen ist, und wechsle
  ihn danach

---

## Ein Schönheitsfehler, offen gesagt

Eine der drei Übertragungen — der Gen2-Cover-Befehl in `roller_control` — hängt
den Schlüssel als **Query-Parameter** an statt ihn in den Body zu legen.

Drei Dinge halten das klein, eines hält es auf der Liste:

- Empfänger ist Shelly, die den Schlüssel ausgestellt haben und damit ohnehin
  alles können. Es ist keine Preisgabe an einen Dritten.
- Die Verbindung ist HTTPS, die Query-Zeile ist unterwegs also nicht sichtbar.
  (`https://` wird automatisch ergänzt, wenn du das Schema weglässt. Wenn du bei
  der Einrichtung bewusst eine `http://`-Adresse eingetragen hast, gilt das
  nicht — und der Rest auch nicht, also tu das nicht.)
- Es passiert nur, wenn du tatsächlich eine Beschattung fährst.

Was bleibt: URLs werden in Server-Infrastruktur typischerweise großzügiger
protokolliert und länger aufbewahrt als Request-Bodies — auf Shellys Servern,
nicht auf deinen. Das kann ich nicht messen und will darüber nicht spekulieren.

**Gemessen am 2026-08-11:** der Endpunkt akzeptiert den Schlüssel auch im Body.
Ohne Schlüssel antwortet er `401 invalid_token`; mit Schlüssel im Body antwortet
er auf ein nicht existierendes Gerät `400 no_permissions` — die Authentifizierung
war also erfolgreich und nur die Berechtigung fehlte. Der Query-Parameter ist
damit **von der API nicht verlangt** und ließe sich verlegen.

Verlegt habe ich ihn trotzdem noch nicht, aus einem Grund: ich besitze keine
Cover-Hardware. Ich kann belegen, dass die Authentifizierung über den Body
funktioniert, aber nicht, dass ein echter Fahrbefehl auf diesem Weg durchläuft.
Funktionierenden Steuercode auf einen Analogieschluss hin zu ändern, um eine
Logging-Nuance auf fremden Servern zu gewinnen, ist ein schlechtes Geschäft.
**Wenn du einen Rollladen-Shelly hast und beim Testen helfen magst, sag bitte in
einem Issue Bescheid** — mehr braucht es nicht.

---

## Selbst nachprüfen, in zwei Minuten

Führe das gegen den installierten Code aus
(`<config>/custom_components/shelly_cloud_diy/`) oder gegen einen Checkout.
Nichts davon führt Code von mir aus.

```bash
# 1. Jede Stelle, die den Schlüssel benutzt — erwartet: 5 Treffer, alle in api/cloud_control.py
grep -rn "_auth_key" custom_components/shelly_cloud_diy/

# 2. Jede ausgehende Anfrage der gesamten Integration
grep -rnE "session\.(get|post|request)|ws_connect" custom_components/shelly_cloud_diy/

# 3. Jede fest eingetragene URL. Erwartet: Doku-Links aus Fehlermeldungen, die
#    http/https-Schema-Behandlung in _normalise_base_url und das CSV-Gateway-
#    Beispiel in einem Docstring. NICHT finden solltest du einen fest
#    verdrahteten API-Host — die Serveradresse kommt immer aus deiner Konfiguration.
grep -rnE "https?://" custom_components/shelly_cloud_diy/

# 4. Beleg, dass der Schlüssel nicht in deinen Logs steht. Im HA-Konfig-
#    verzeichnis ausführen und <DEIN-KEY> durch den echten Wert ersetzen.
#    Erwartet: 0
grep -cF '<DEIN-KEY>' home-assistant.log

#    So landet dein Schlüssel in der Shell-History. Entweder danach löschen
#    oder das ganz vermeiden, indem du ihn an einer Eingabeaufforderung tippst:
read -rsp 'key: ' K && grep -cF "$K" home-assistant.log; unset K
```

Prüfung 4 ist bewusst dir überlassen, und das ist auch der ganze Grund, warum es
in diesem Repository **kein** „prüfe meine Installation"-Skript gibt.

Der entscheidende Grund ist nicht, dass so ein Werkzeug unangenehm zu schreiben
wäre. Er ist: **ein Prüfer, den das Projekt mitliefert, kann das Projekt nicht
überprüfen.** Läge in diesem Repository ein Skript, das dieses Repository
untersucht und „alles gut" ausgibt, würdest du mir zweimal vertrauen statt
einmal — und eine Prüfung, die nicht schlecht ausgehen kann, ist keine Prüfung.
Sie würde außerdem verrotten: sie testet gegen Erwartungen, die beim Schreiben
festgelegt wurden, und meldet still weiter „alles gut", nachdem sich der Code
darunter bewegt hat. Ein `grep`, den du selbst getippt hast, hat beide Probleme
nicht und bleibt wahr, egal was ich ändere.

Es gibt einen zweiten, kleineren Grund: um dein Log nach deinem Schlüssel zu
durchsuchen, müsste das Werkzeug deinen Schlüssel lesen, und sein Bericht ist
genau das, was Leute in öffentliche Issues kopieren. Der Punkt ist lösbar — ein
Werkzeug könnte über Hashes vergleichen und nie etwas Sensibles ausgeben — und
genau deshalb ist er der *zweite* Grund und nicht der erste.

Wenn du prüfen willst, ob das von HACS Installierte diesem Repository entspricht:
die Release-ZIPs werden von GitHub Actions aus dem getaggten Commit gebaut, du
kannst den installierten Ordner also gegen den Quelltext deiner Version
vergleichen.

---

## Was dieses Dokument nicht behauptet

- **Nichts über Shellys Seite.** Was deren Server protokollieren, wie lange sie
  es aufbewahren und wer es lesen kann, weiß ich nicht und kontrolliere ich
  nicht.
- **Nichts über die Sicherheit von Home Assistants Speicher**, außer der oben
  genannten Klartext-Tatsache.
- **Nichts über künftige Versionen**, außer diesem: diese Datei ist Teil des
  Quelltexts, und die Handhabung des Schlüssels zu ändern, ohne diese Datei zu
  ändern, wäre ein meldenswerter Fehler.
- **Das ist kein Audit durch Dritte.** Es ist eine Beschreibung, die du prüfen
  kannst, geschrieben von dem, der den Code geschrieben hat. Die Prüfungen zählen
  mehr als die Beschreibung — deshalb stehen sie hier.

Etwas gefunden, das dem oben Gesagten widerspricht? Bitte
[ein Issue aufmachen](https://github.com/notDIRK/shelly-cloud-diy-ha/issues) —
dann ist es ein Fehler in der Software oder auf dieser Seite, und beides will ich
wissen.
