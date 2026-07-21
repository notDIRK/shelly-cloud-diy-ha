# Feature-Highlights & USPs

> 🇬🇧 **English:** siehe [`FEATURE-HIGHLIGHTS.md`](FEATURE-HIGHLIGHTS.md) (Primärfassung).
>
> Quell-Text für die GitHub-Startseite (README) und die Manual-Pages. Status-Tags
> zeigen ehrlich, was ausgeliefert ist und was auf der Roadmap steht:
> ✅ ausgeliefert · 🔄 in Entwicklung (als Nächstes) · ⏳ geplant · 💡 Vision.

## Kurzpitch

**Shelly Cloud DIY** verbindet Home Assistant über die Self-Service-**Cloud-
Control-API** mit deiner Shelly-Flotte — inklusive der Geräte, die eine rein
lokale Integration nie erreicht (geteilte Geräte, entfernte Standorte, die Shelly-
BLU-Familie hinter einem Gateway). Gebaut auf einem klaren Prinzip:

> **Lokal-first, Cloud-optional.** Deine Steuerung bleibt im LAN — schnell und
> auch ohne Internet funktionsfähig. Die Cloud ist ein *Overlay* für Sichtbarkeit,
> geteilte Geräte und Migrations-Werkzeuge — **nie** eine Abhängigkeit, um das
> Licht einzuschalten.

## Was das Projekt einzigartig macht (übergreifendes USP)

- **Self-Service-Zugang** über den dokumentierten `auth_key`/OAuth-Weg — kein
  gegateter Integrator-API-Token, keine Shelly-Support-Mail, kein Consent-Webhook.
- **Sieht geteilte & entfernte Geräte**, die die lokale LAN-Integration nicht
  erreicht — inkl. Shelly-BLU-Sensoren über ein Gateway und Geräte, die aus einem
  anderen Shelly-Konto geteilt wurden.
- **Volle Generationsabdeckung:** Gen1 + Gen2/Gen3 + BLE-Gateway-Geräte.
- Die einzige gepflegte HA-Integration, die Cloud-Control-API-Zugriff **mit
  State-Rücklesen, geteilten Geräten und Gen1/Gen2/BLE-Abdeckung** vereint.

---

## Phase 1 — Fleet-Map: Cloud und lokale Shellys verbinden  🔄 als Nächstes

**Slogan:** *Eine Ansicht für jeden Shelly — lokal, Cloud oder beides — mit
Namen, die sich selbst pflegen, und einer ehrlichen Antwort auf „funktioniert das
auch offline?"*

**Der Schmerz.** Wer Shellys lokal betreibt **und** die Cloud als Redundanz nutzt
(wie viele ausfallsicherheits-bewusste Nutzer), sieht dasselbe physische Gerät in
Home Assistant doppelt, pflegt seinen Namen von Hand an zwei Stellen und hat keine
einfache Möglichkeit zu erkennen, ob die *Steuerung* eines Geräts heimlich vom
Internet abhängt.

**USP.** Die erste HA-Integration, die **die Shelly-Cloud und die native lokale
Shelly-Integration verbindet** — jeder physische Shelly wird über seine MAC in
beiden Welten zusammengeführt, plus ein **Resilienz-Check**, den sonst niemand
bietet.

**Highlights**
- **Cloud ⇄ Lokal-Matching** — sofort sehen, welche Shellys nur lokal, nur Cloud
  oder auf beiden Wegen vorhanden sind (gematcht über die MAC, nicht über fragile
  Namen).
- **Namen-Sync (Pull):** ein Gerät einmal in der Shelly-App benennen, Home
  Assistant folgt automatisch — für Cloud- *und* native Geräte. Schluss mit
  Doppelpflege. Deine manuellen HA-Umbenennungen bleiben immer erhalten.
- **Resilienz-Check:** Home Assistant zeigt dir, ob der **Steuerweg** eines Geräts
  von Cloud/Internet abhängt — z. B. ein reines Cloud-Gerät, ein Gerät mit
  deaktivierter lokaler Steuer-Entity oder eine Automation, die auf die Cloud-Kopie
  eines lokal steuerbaren Geräts zeigt. *Der „bleibt das Licht bei Internet-
  Ausfall steuerbar?"-Check.*
- **Read-only & lokal-first by design:** fasst den Steuerweg nie an, und das
  Matching funktioniert auch bei Internet-Ausfall weiter.

---

## Phase 2 — Geräte-Tausch mit einem Klick  🔄 in Arbeit (Cloud ausgeliefert, native upstream zur Review)

**Slogan:** *Defekten Shelly gegen einen neuen tauschen — alle Automationen,
Dashboards und der komplette Verlauf bleiben. Kein Neu-Verdrahten.*

**Der Schmerz.** Ein Shelly geht kaputt. Heute bindet man den Ersatz ein und sucht
dann jede Automation, Szene und jedes Dashboard ab, um die alten Entities gegen
die neuen zu tauschen — denn Shelly-Hardware hängt an der MAC, und ein neues Gerät
sieht für Home Assistant aus wie ein völlig neues. Home Assistant Core hat dafür
nie eine generische Lösung geliefert.

**USP.** Ein geführter **Geräte-Tausch**, der deine Home-Assistant-Einrichtung
über einen Hardware-Wechsel hinweg erhält — und zwar für **beide** Wege:
Cloud-eingebundene **und** lokal-eingebundene Shellys.

**Highlights**
- **Tausch ohne HA-Neukonfiguration:** behält `entity_id`s, das Home-Assistant-
  Gerät, Namen, Bereiche, **Langzeit-Verlauf** und jeden Bezug aus Automationen,
  Skripten, Szenen und Dashboards — inklusive aufgesetzter Helfer (z. B.
  *Switch-as-Light*).
- **Funktioniert über beide Integrationen:** die Cloud-Geräte dieser Integration
  schon heute; die native lokale Shelly-Integration über dieselbe MAC-Brücke.
- **Cloud als Sicherheitsnetz:** holt Identität und Namen des defekten Geräts aus
  der Cloud, selbst wenn die Hardware schon weg und lokal nicht mehr lesbar ist.
- **Trockenlauf-Vorschau:** genau sehen, was sich ändert, bevor etwas angefasst
  wird.
- **Beitrag zurück:** die native Variante wurde **Upstream an Home Assistant Core
  eingereicht** ([core#174581](https://github.com/home-assistant/core/pull/174581),
  nach dem Vorbild des ESPHome-Tausch-Repair-Flows), damit alle HA-Nutzer
  profitieren, nicht nur die dieser Integration.

---

## Phase 3 — On-Device-Config-Klon: Ausfallsicherheit, die Störungen übersteht  💡 geplant

**Slogan:** *Das „Gehirn" des Shellys auf die neue Hardware mitnehmen — damit
lokale Automationen weiterlaufen, selbst wenn Internet UND Home Assistant
ausfallen.*

**Der Schmerz.** Die ausfallsichersten Automationen leben **auf dem Shelly
selbst** (On-Device-Zeitpläne und -Skripte) — sie laufen ohne Cloud und ohne Home
Assistant. Nach einem Hardware-Tausch ist all das weg, sofern man es nicht von
Hand neu einträgt.

**USP.** Die **On-Device-Konfiguration** eines Shellys auf den Ersatz klonen,
sodass das Gerät sich identisch verhält — die stärkste Form von Offline-
Resilienz.

**Highlights**
- **Klont:** Relais-/Eingangsnamen, Eingangsmodi, Zeitpläne, **On-Device-Skripte**,
  Webhooks und KVS — über das lokale Netz (LAN), nicht über die Cloud.
- **Maximale Resilienz:** On-Device-Logik läuft ohne Cloud und ohne HA weiter.
- **Ehrlich bei den Grenzen:** MAC, Passwörter und Rollladen-Kalibrierung sind
  nicht übertragbar — sie werden am neuen Gerät neu eingetragen bzw. neu
  kalibriert. Das sage ich offen.

---

## Die eine Zeile für den Kopf der README

> **Shelly Cloud DIY** — lokal-first Home-Assistant-Integration für die Shelly-
> Cloud-Control-API. Erreicht geteilte & entfernte Geräte, führt Cloud- und lokale
> Shellys zu einer Flottenansicht zusammen, hält Namen synchron, warnt, falls sich
> die Cloud je in einen Steuerweg schleicht, und tauscht einen defekten Shelly aus,
> ohne dass du eine einzige Automation neu bauen musst.
