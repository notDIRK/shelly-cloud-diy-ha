# Support

Open an [issue](https://github.com/notDIRK/shelly-cloud-diy-ha/issues). That is
the only channel, and it is the fastest one.

## What to expect

Alerts for new issues reach my phone, so you will usually hear back the same day.
Across the issues reported here so far, the median has been under four hours to a
first answer and about half a day until the issue was closed. That is what I aim
for, not a promise — this is one person's spare time.

When a fix is possible I normally ship it as a **beta release** first and ask you
to confirm it on your hardware, because your device is usually the only one that
can prove the fix. Once you confirm, it becomes a stable release.

## What helps me help you

- **Which device**, exactly — the model code (e.g. `SNSW-102P16EU`) beats a
  marketing name.
- **What you expected and what happened instead.** A screenshot of the entity or
  the dialog is often enough to identify the cause.
- **The integration version** and your Home Assistant version.
- **Diagnostics**, if I ask for them: Settings → Devices & Services → this
  integration → the device → ⋮ → *Download diagnostics*. Names, IP, MAC and SSID
  are redacted automatically and your credentials are never included.

Never paste your Shelly `auth_key`, a Home Assistant token, or anything else that
grants access. I will never ask for one, and if a log line contains one, redact
it before posting.

## How I work

I maintain this on my own and I work with AI — openly, and with every change
reviewed and tested by me before it ships. The reasoning behind that is in the
[README](README.md#how-i-work) and on [my profile](https://github.com/notDIRK).
If anything in this code looks like something I couldn't explain, ask.

## Before you report

- Is this a device you can reach on your own network? Then the **built-in Shelly
  integration** in Home Assistant is the better tool — it is local, faster, and
  works offline. This integration is a cloud overlay for what the LAN cannot
  reach.
- Check the [open and closed issues](https://github.com/notDIRK/shelly-cloud-diy-ha/issues?q=is%3Aissue)
  first; several common cases are already answered there.
