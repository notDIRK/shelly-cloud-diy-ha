# CLAUDE.md — instructions for any Claude session working on this repo

**Project identity:** `shelly-cloud-diy-ha` — Home Assistant integration for
Shelly Cloud, using the self-service **Cloud Control API** path (auth_key /
OAuth). Started as a notDIRK fork of `engesin/shelly-integrator-ha` (which
uses the gated Integrator API), now diverged into a separate integration
with domain `shelly_cloud_diy`. Fork lineage kept for git traceability;
no upstream merges expected.

## ⚠ HARD RULE — secret scan before every push

Before running `git push` on this repo, scan both the staged diff and the
working tree for credentials. No exceptions, not even for one-line / docs /
rename / version-bump commits.

Quick scan (run from repo root):

⚠ Use `origin/main..HEAD`, **never `origin/HEAD..HEAD`**. `origin/HEAD` is unset
in this clone, so `git diff origin/HEAD..HEAD` aborts with `bad revision`; the
`grep` behind the pipe then reads an empty stream, matches nothing and prints
`diff clean` — **without having checked anything**. Always print the diff size
first so a scan that saw zero lines is visible.

```bash
git diff origin/main..HEAD -- '**' | wc -l   # 0 = nothing was scanned, investigate

git diff origin/main..HEAD -- '**' | grep -E -i \
  'auth_key|integrator_token|access_token|bearer[[:space:]]+[A-Za-z0-9]|ghp_|gho_|sk-[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]+\.eyJ' \
  && echo "SECRET SUSPECTED — ABORT" || echo "diff clean"

git grep -E -i 'auth_key[[:space:]]*=|integrator_token[[:space:]]*=' \
  -- ':!README.md' ':!CLAUDE.md' \
  && echo "SECRET SUSPECTED — ABORT" || echo "tree clean"
```

If ANY match appears that is not a pattern name in a validator regex or a
documentation mention of the concept, **stop, tell the user, do not push**.

The operator's live Shelly Cloud `auth_key` lives at
`~/.config/shelly-integrator-ha/auth_key` (outside any repo, chmod 600 —
directory name kept stable across the rename to avoid breaking local scripts).
Never copy its contents into any file inside this repo, any commit message,
any `bash -c "echo ..."`, any log line. Read it into a local shell variable
when needed for a `curl` call and discard.

If a secret is ever pushed by accident: (1) tell the operator immediately,
(2) rotate the credential upstream (changing the Shelly password
regenerates the auth_key server-side), (3) only then force-remove from
`origin` with `git push --force-with-lease` and **explicit** operator
approval. Never force-push as a first reflex.

## Local HA access (private, gitignored)

Der Betreiber betreibt eine laufende Home-Assistant-Instanz, gegen die
Integrations-Code getestet und debugged werden kann. Zugang dokumentiert in:

```
./.ha_handoff.md
```

Diese Datei ist **gitignored** (`.ha_handoff*` in `.gitignore`) und enthaelt
reale IPs, Hostnamen und Pfade zu Credentials. **Niemals in Commits, Logs,
Screenshots, Issues oder Chats einfuegen.** Bei versehentlichem Leak:
Long-Lived-Token in HA rotieren (Profil → Tokens).

Wenn `.ha_handoff.md` fehlt: der Betreiber nutzt dieses Repo gerade
offline / auf einem anderen Host — einfach ohne Live-HA arbeiten.

## Repo topology

- `origin` → `github.com/notDIRK/shelly-cloud-diy-ha` (push target)
- `upstream` → `github.com/engesin/shelly-integrator-ha` (read-only; kept for fork-lineage traceability only, no merges expected since we pivoted API)
- Python domain: `shelly_cloud_diy` (matches repo basename for entity-ID consistency)
- Release tags: `vX.Y.Z` (SemVer, no personal suffix — targeting HACS default store, the `-notDIRK` suffix would look unprofessional). Historical `vX.Y.Z-notDIRK` tags (≤ v0.2.2) stay on their commits for audit trail.
- Conventional Commits style (`feat:`, `fix(security):`, `docs:`, etc.).
- `.planning/` is gitignored — GSD scratch, not for distribution.
- Pivot roadmap: `docs/ROADMAP.md`. Pre-pivot codebase snapshot: `docs/CODEBASE_MAP.md`.

## Architectural direction

The integration has pivoted from the **Shelly Integrator API** (gated by
Shelly, "Licenses for personal use are not provided") to the
**Shelly Cloud Control API** (self-service `auth_key` / OAuth). The
WebSocket endpoint format is identical between both APIs, so the coordinator
and WebSocket handler are largely reusable; the rewrite centres on the auth
layer, config flow, and adding support for BLE/gateway-bridged sensors
(shared devices, Shelly BLU family, etc.) that the Integrator API consent
model cannot reach.

See `docs/ROADMAP.md` for the milestone plan. Milestone 1 (HTTP polling
with `auth_key`) is the first HACS-release target; Milestone 2 (OAuth +
WebSocket realtime) is follow-up.

## HA-Core / HACS-Default ambitions

Short term: **HACS default-store submission** (not HA Core). That means a
logo PR to `home-assistant/brands` when we cut the first stable release,
and a cleanup pass on anything that would block Core submission later:
- No `notDIRK` references inside Python source or user-visible strings
- English log messages
- Proper exception types (`HomeAssistantError`, `ConfigEntryAuthFailed`, `UpdateFailed`)
- Translations for every user-visible string

Don't over-engineer for Core (heavy test coverage, diagnostics/repairs
platforms, quality_scale=gold) — that's future scope.

## Release flow

```bash
# 1. Bump manifest.json version
git commit -am "chore(release): bump manifest to X.Y.Z"

# 2. Pre-push secret scan (see HARD RULE above)

# 3. Push and tag
git push origin main
git tag -a vX.Y.Z -m "Release X.Y.Z"
git push origin vX.Y.Z

# 4. GitHub release
gh release create vX.Y.Z --repo notDIRK/shelly-cloud-diy-ha \
  --title "vX.Y.Z" --notes "…"
```
