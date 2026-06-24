# Feature highlights & USPs

> 🇩🇪 **Deutsch:** see [`FEATURE-HIGHLIGHTS.de.md`](FEATURE-HIGHLIGHTS.de.md).
>
> Source copy for the GitHub homepage (README) and the manual pages. Status
> tags reflect what is shipped vs. on the roadmap — keep them honest:
> ✅ shipped · 🔄 in development (next) · ⏳ planned · 💡 aspirational.

## Elevator pitch

**Shelly Cloud DIY** connects Home Assistant to your Shelly fleet through the
self‑service **Cloud Control API** — including the devices a local‑only
integration can never reach (shared devices, remote sites, the Shelly BLU
family behind a gateway). But it is built on one firm principle:

> **Local‑first, cloud‑optional.** Your switching stays on the LAN — fast and
> working even with no internet. The cloud is an *overlay* for visibility,
> shared devices, and migration tooling — **never** a dependency for turning
> your lights on.

## What makes the project unique (overarching USP)

- **Self‑service access** via the documented `auth_key` / OAuth path — no gated
  Integrator‑API token, no Shelly support email, no consent webhook.
- **Sees shared & remote devices** the local LAN integration cannot — including
  Shelly BLU sensors bridged through a gateway, and devices shared from another
  Shelly account.
- **Full generation coverage:** Gen1 + Gen2/Gen3 + BLE‑gateway devices.
- The only maintained HA integration combining Cloud‑Control‑API access **with
  state read‑back, shared‑device support, and Gen1/Gen2/BLE coverage** at once.

---

## Phase 1 — Fleet‑Map: bridge the cloud and your local Shellys  🔄 next

**Tagline:** *One view of every Shelly — local, cloud, or both — with names that
look after themselves and an honest answer to "will it still work offline?"*

**The pain it solves.** If you run Shellys locally **and** keep the cloud as a
backup path (as many resilience‑minded users do), the same physical device shows
up twice in Home Assistant, you maintain its name in two places by hand, and you
have no easy way to tell whether a device's *control* secretly depends on the
internet.

**USP.** The first HA integration to **bridge the Shelly cloud and the native
local Shelly integration** — matching every physical Shelly across both worlds
by its MAC, and adding a **resilience check** nobody else offers.

**Highlights**
- **Cloud ⇄ Local matching** — instantly see which Shellys are local‑only,
  cloud‑only, or present on both paths (matched by MAC, not by fragile names).
- **Name sync (pull):** name a device once in the Shelly app and Home Assistant
  follows automatically — for cloud *and* native devices. No more double‑naming.
  Your manual Home Assistant renames are always respected.
- **Resilience check:** Home Assistant tells you if any device's **control path
  depends on the cloud/internet** — e.g. a cloud‑only switch, a device whose
  local control entity is disabled, or an automation pointing at the cloud copy
  of a device you could control locally. *The "will my lights still work when
  the internet is down?" check.*
- **Read‑only & local‑first by design:** it never touches the control path, and
  the matching keeps working even when the internet is down.

---

## Phase 2 — One‑click device replacement  🔄 in progress (cloud shipped, native upstreamed for review)

**Tagline:** *Replace a broken Shelly with a new one — keep every automation,
dashboard, and the full history. No re‑wiring.*

**The pain it solves.** A Shelly dies. Today you onboard the replacement and then
hunt through every automation, scene, and dashboard to swap the old entities for
the new ones — because Shelly hardware is bound to its MAC address and a new unit
looks like a brand‑new device to Home Assistant. Home Assistant Core has never
shipped a generic fix for this.

**USP.** A guided **device‑replacement** that preserves your Home Assistant setup
across a hardware swap — and works for **both** cloud‑integrated **and**
locally‑integrated Shellys.

**Highlights**
- **Swap without re‑configuring HA:** keeps your `entity_id`s, the Home Assistant
  device, names, areas, **long‑term history**, and every automation, script,
  scene, and dashboard reference — including helpers stacked on top (e.g.
  *Switch‑as‑Light*).
- **Works across both integrations:** our cloud devices today; the native local
  Shelly integration via the same MAC bridge.
- **Cloud as a safety net:** pulls the dead device's identity and name from the
  cloud even when the hardware is already gone and can no longer be read locally.
- **Dry‑run preview:** see exactly what will change before anything is touched.
- **Giving back:** the native variant has been **submitted upstream to Home
  Assistant Core** ([core#174581](https://github.com/home-assistant/core/pull/174581),
  modelled on the ESPHome replacement repair flow) so every HA user benefits, not
  just ours.

---

## Phase 3 — On‑device config clone: resilience that survives outages  💡 planned

**Tagline:** *Carry the Shelly's on‑device brain to the new hardware — so your
local automations keep running even if both the internet and Home Assistant are
down.*

**The pain it solves.** The most resilient automations live **on the Shelly
itself** (on‑device schedules and scripts) — they run with no cloud and no Home
Assistant. After a hardware swap, all of that is gone unless you re‑enter it by
hand.

**USP.** Clone a Shelly's **on‑device configuration** onto its replacement, so
the device keeps behaving identically — the strongest form of offline resilience.

**Highlights**
- **Clones:** relay/input names, input modes, schedules, **on‑device scripts**,
  webhooks, and KVS — over the local network (LAN), not the cloud.
- **Maximum resilience:** on‑device logic keeps working with no cloud and no HA.
- **Honest about limits:** the MAC, passwords, and roller/cover calibration
  cannot be transferred — they are re‑entered or re‑run on the new device. We say
  so up front.

---

## The one line for the top of the README

> **Shelly Cloud DIY** — local‑first Home Assistant integration for the Shelly
> Cloud Control API. Reaches shared & remote devices, bridges your cloud and
> local Shellys into one fleet view, keeps names in sync, warns you if the cloud
> ever sneaks into a control path, and lets you replace a broken Shelly without
> re‑doing a single automation.
