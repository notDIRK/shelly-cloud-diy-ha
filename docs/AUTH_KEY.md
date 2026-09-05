# What this integration does with your Shelly auth key

*[Deutsche Fassung](AUTH_KEY.de.md)*

This integration asks you for a Shelly cloud auth key. That key is powerful, and
you are installing code from a stranger on the internet. This page tells you
exactly where the key travels, where it is stored, where it never goes, and how
to check every one of those claims yourself in about two minutes — without
running anything I wrote.

If you only read one paragraph: the key is used in **one file**, is sent **only
to the server address you typed in during setup**, is never logged, never
appears in a diagnostics download, and is never sent to me or to any third
party. The uncomfortable parts are further down, and there are some.

---

## What the key is, and what it can do

The auth key comes from the Shelly App under *User settings → Authorization
cloud key*. It is a **full-account credential**: anything you can do in the
Shelly App, the key can do — read every device on the account and control every
device on the account. There is no read-only variant and no per-device scoping.
That is Shelly's design, not a choice this integration makes.

**Revoking it:** change your Shelly account password. The key is regenerated
server-side, which makes the old one useless immediately. There is no separate
"revoke key" button.

Because a key cannot be scoped, the sensible mental model is: *this key is your
Shelly password in a different shape.* Everything below follows from taking that
seriously.

---

## Where the key goes

Exactly one file ever touches it:
`custom_components/shelly_cloud_diy/api/cloud_control.py`.

Check for yourself:

```bash
grep -rn "_auth_key" custom_components/shelly_cloud_diy/
```

Five hits, and it is worth knowing what each one is:

| What it is | Where |
|---|---|
| stored on the client instance | `ShellyCloudControl.__init__` |
| **sent** — form field, v1 endpoints | `_post` |
| **sent** — JSON body field, v2 metadata | `get_device_configs` |
| **sent** — query parameter, v2 cover | `roller_control` |
| not the key — a substring test on an error message | `_post` error handling |

So there are **three** places that transmit it. Every transmission is written
out explicitly at the call site; the helper methods do not silently attach
credentials, which is what makes the `grep` above complete rather than
indicative.

All three send to `self._base_url`. That value is built once, in
`_normalise_base_url`, from the server address **you** typed during setup
(e.g. `shelly-42-eu.shelly.cloud`, shown in the app on the same screen as the
key). There is no second destination for the key anywhere in the code, and no
hard-coded fallback host.

To satisfy yourself that there is no other outbound traffic at all:

```bash
grep -rnE "session\.(get|post|request)|ws_connect|https?://" custom_components/shelly_cloud_diy/
```

The only non-Shelly destination that appears is a gateway URL for CSV import
that **you** configure yourself — see below.

---

## Where the key does not go

**Not into the logs.** Turning on debug logging for this integration does not
print the key. The log lines that mention `auth_key` at all are messages *about*
a rejection ("auth_key rejected — skipping"), never the value.

```bash
grep -rn "_LOGGER" custom_components/shelly_cloud_diy/ | grep -i "auth\|key\|token"
```

**Not into diagnostics downloads.** The diagnostics module never reads the
config entry's stored *data* — the credentials live there. It reports the
entry's **options** (poll interval, which devices you enabled, which detectors
are on), the device snapshot and the fleet map, with names, IPs, MACs and SSIDs
redacted and device ids reduced to a non-reversible fingerprint. This matters
because diagnostics downloads are the thing people attach to public bug reports.

```bash
# Expect comments only — no line that actually reads the entry's data.
grep -n "entry.data" custom_components/shelly_cloud_diy/diagnostics.py
```

**Not to me, and not to any third party.** There is no telemetry, no analytics,
no crash reporting, no "phone home". The integration declares exactly two
dependencies in `manifest.json`: `aiohttp`, which is part of Home Assistant's
own core stack, and `aioshelly`, the same library Home Assistant's built-in
Shelly integration uses. Nothing exotic, nothing of mine.

**Not to the CSV gateway.** The integration can fetch energy CSVs from a gateway
URL you supply yourself. That request is a plain `GET` with **no credential
attached**, and the URL is validated first — non-HTTP schemes and loopback
targets are rejected, so the integration cannot be pointed back at your own Home
Assistant (`utils/http.py`, `validate_gateway_url`).

---

## Where the key is stored — the part I would rather not have to write

Home Assistant stores config entry data in
`<config>/.storage/core.config_entries`, **in plain text**. Your Shelly key sits
in that file, unencrypted, exactly like the credentials of every other
integration you have installed.

This is how Home Assistant works for all integrations; there is no supported way
for an integration to opt out, and I am not going to pretend otherwise. The
practical consequences are yours to weigh:

- anyone who can read your Home Assistant config directory can read the key
- the same is true of any **backup** of that directory, including automatic ones
  and anything you upload to cloud storage
- if you share a backup for debugging — with anyone, including me — assume the
  key went with it, and rotate it afterwards

---

## The second credential — only if you switch cloud control on

Everything above is about the auth key, which is the only credential a default
install has. There is one optional feature that needs a second one, and it is
**off unless you turn it on**: cloud control, which switches virtual components
(an irrigation controller's zones, for instance) that the documented API has no
route for at all. See the README for what it does and why the channel it uses is
undocumented.

Switching it on asks for your **Shelly account email and password**, because the
channel accepts an account token and not the auth key. So, in the same terms as
above:

**The password is never stored, and nothing that does I/O ever sees it.** It is
turned into the digest Shelly's login expects at the flow boundary and the
plaintext is discarded there. That is a structural property, not a promise —
one function takes the plaintext, every other function takes the digest:

```bash
# Seven hits, and only two of them are code: the function itself in
# api/oauth.py, and the single call site in config_flow.py. The other five
# are comments and docstrings saying the same thing this section says.
grep -rn "sha1_password" custom_components/shelly_cloud_diy/
```

**What is stored is the token** that the sign-in returns, in the same
`core.config_entries` file as the auth key, with the same plain-text caveat.
Switching the option off again **deletes it**; so does removing the integration.
That is the part I can state, because it is mine. Whether changing your Shelly
account password also invalidates an already-issued token is Shelly's side and I
have not measured it — so do not rely on it. Switching the option off is the
reliable way to be rid of it here.

**Where it goes:** three form POSTs, all marked `# CREDENTIAL:` at the call site
in `api/oauth.py` — the login (to Shelly's fixed login host), the code exchange
and the refresh (both to your account's own host, named by the login response
itself). And one WebSocket connection, to your account's host, in
`api/cloud_ws.py`.

**Not into the logs, not into a repr.** The token classes override `__repr__`
so a token cannot be printed by an f-string or a traceback frame, inbound relay
frames are redacted before any debug log line, and no `aiohttp` error object is
ever put into a message — its text embeds the full request URL, which on this
channel carries the token.

**Not into diagnostics.** The diagnostics file reports whether cloud control is
on, whether it is connected, and per device whether Shelly will route commands
to it. No token, no email, no raw device id.

**The wart, stated as plainly as the one below:** the access token rides in the
WebSocket connect URL as a query parameter. That is how Shelly's relay is
addressed; there is no header variant. The mitigations are the same as for the
cover command below — the recipient is Shelly, who issued it, and the connection
is TLS — and so is the residual: URLs get logged more liberally than bodies, on
their servers, which I cannot measure.

If you never switch cloud control on, none of this exists in your installation:
no sign-in is asked for, no token is stored, and no connection is opened.

---

## One wart, stated plainly

One of the three transmissions — the Gen2 cover command in `roller_control` —
passes the key as a **query parameter** rather than in the request body.

Three things keep this small, and one thing keeps it on the list:

- The recipient is Shelly, who issued the key and can already do everything with
  it. This is not disclosure to a third party.
- The connection is HTTPS, so the query string is not visible in transit.
  (`https://` is added automatically if you leave the scheme off. If you
  deliberately typed an `http://` address at setup, this does not hold — and
  neither does any of the rest, so do not do that.)
- It only happens when you actually operate a cover.

What remains is that URLs tend to be logged more liberally, and retained longer,
than request bodies — on Shelly's servers, not on yours. I cannot measure that
and will not speculate about it.

**Measured on 2026-08-11:** the endpoint accepts the key in the request body
too. Sending no key returns `401 invalid_token`; sending it in the body returns
`400 no_permissions` for a non-existent device — meaning authentication
succeeded and only authorization failed. So the query parameter is **not
required by the API** and can be moved.

I have not moved it yet, for one reason: I own no cover hardware, so I can prove
that authentication works via the body but not that a real cover command
completes that way. Changing working control code on inference alone, to gain a
logging nuance on someone else's servers, is a bad trade. **If you own a cover
Shelly and are willing to test it, please say so in an issue** — that is all
this needs.

---

## Check it yourself, in two minutes

Run these against the installed code
(`<config>/custom_components/shelly_cloud_diy/`) or against a checkout. None of
them run anything I wrote.

```bash
# 1. Every place the key is used — expect 5 hits, all in api/cloud_control.py
grep -rn "_auth_key" custom_components/shelly_cloud_diy/

# 2. Every outbound request in the whole integration
grep -rnE "session\.(get|post|request)|ws_connect" custom_components/shelly_cloud_diy/

# 3. Every hard-coded URL. Expect: documentation links shown in error messages,
#    the http/https scheme handling in _normalise_base_url, the CSV gateway
#    example in a docstring — and exactly ONE hard-coded API host,
#    api.shelly.cloud/oauth/login in api/oauth.py, which is Shelly's fixed
#    login endpoint and is only ever called if you switch cloud control on.
#    Nothing the auth key touches has a hard-coded host: for the poll, the
#    server address always comes from your own configuration.
grep -rnE "https?://" custom_components/shelly_cloud_diy/

# 4. Prove the key is not in your own logs. Run this in your HA config
#    directory and replace <YOUR-KEY> with the actual value. Expect: 0
grep -cF '<YOUR-KEY>' home-assistant.log

#    Your key ends up in your shell history this way. Either clear it
#    afterwards, or avoid it entirely by typing the key at a prompt instead:
read -rsp 'key: ' K && grep -cF "$K" home-assistant.log; unset K
```

Check 4 is deliberately left for you to complete by hand, and that is the whole
reason there is no "audit my installation" tool in this repository.

The decisive reason is not that such a tool would be awkward to write. It is
that **a checker shipped by the project cannot verify the project.** If this
repository contained a script that examined this repository and printed "all
good", you would be trusting me twice over instead of once — and a check that
cannot come out badly is not a check. Any such tool would also rot: it would
test against expectations hard-coded at the time of writing, and quietly keep
reporting "all good" after the code moved underneath it. A `grep` you typed
yourself has neither problem, and it stays true no matter what I change.

There is a second, smaller reason: to search your log for your key, the tool
would have to read your key, and its report is exactly the kind of thing people
paste into public issues. That one is solvable — the tool could compare hashes
and never print anything sensitive — which is precisely why it is the *second*
reason and not the first.

If you want to verify that what HACS installed matches this repository: release
ZIPs are built by GitHub Actions from the tagged commit, so you can diff the
installed folder against the source for the version you are running.

---

## What this document does not claim

- **Nothing about Shelly's side.** What their servers log, how long they keep
  it, and who can read it is outside my knowledge and outside my control.
- **Nothing about Home Assistant's storage security** beyond stating the
  plain-text fact above.
- **Nothing about future versions**, except this: this file is part of the
  source, and changing how the key is handled without changing this file would
  be a bug worth reporting.
- **This is not a third-party audit.** It is a description you can check,
  written by the person who wrote the code. The checks matter more than the
  description; that is why they are here.

Found something that contradicts any of the above? Please
[open an issue](https://github.com/notDIRK/shelly-cloud-diy-ha/issues) — that is
a bug in the software or in this page, and either way I want to know.
