# CLAUDE.md — instructions for any Claude session working on this fork

## ⚠ HARD RULE — secret scan before every push

Before running `git push` on this repo, scan both the staged diff and the
working tree for credentials. No exceptions, not even for one-line / docs /
rename / version-bump commits.

Quick scan (run from repo root):

```bash
git diff origin/HEAD..HEAD -- '**' | grep -E -i \
  'auth_key|integrator_token|access_token|bearer[[:space:]]+[A-Za-z0-9]|ghp_|gho_|sk-[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]+\.eyJ' \
  && echo "SECRET SUSPECTED — ABORT" || echo "diff clean"

git grep -E -i 'auth_key[[:space:]]*=|integrator_token[[:space:]]*=' \
  -- ':!README.md' ':!CLAUDE.md' \
  && echo "SECRET SUSPECTED — ABORT" || echo "tree clean"
```

If ANY match appears that is not a pattern name in a validator regex or a
documentation mention of the concept, **stop, tell the user, do not push**.

The operator's live Shelly Cloud `auth_key` lives at
`~/.config/shelly-integrator-ha/auth_key` (outside any repo, chmod 600).
Never copy its contents into any file inside this repo, any commit message,
any `bash -c "echo ..."`, any log line. Read it into a local shell variable
when needed for a `curl` call and discard.

If a secret is ever pushed by accident: (1) tell the operator immediately,
(2) rotate the credential upstream (changing the Shelly password
regenerates the auth_key server-side), (3) only then force-remove from
`origin` with `git push --force-with-lease` and **explicit** operator
approval. Never force-push as a first reflex.

## Repo topology

- `origin` → `github.com/notDIRK/shelly-integrator-ha` (fork, push target)
- `upstream` → `github.com/engesin/shelly-integrator-ha` (read-only)
- Release tags: `vX.Y.Z-notDIRK`; manifest version numeric part stays in sync with the tag.
- Conventional Commits style (`fix(security): …`, `docs(readme): …`, etc.).
- `.planning/` is gitignored — GSD scratch, not for the fork.
- Consolidated codebase map: `docs/CODEBASE_MAP.md`.

## Open architectural decision

Pivot from the Integrator API (gated, "no personal use" per Shelly docs) to
the **Cloud Control API** (self-service `auth_key`, OAuth for realtime) is
under evaluation. See the bilingual "Getting an API Token" section in the
README for context. The WebSocket URL is identical between both APIs, so
a pivot is primarily an auth-layer + config-flow rewrite, not a full
rewrite. Shared-device access (devices shared from another Shelly account
into the operator's account) is only achievable on the Cloud Control API
path.

## Upstream sync flow

```bash
git fetch upstream
git merge upstream/main        # resolve conflicts
# bump manifest.json version
git commit -am "chore(release): bump manifest version to X.Y.Z"
git push origin main
git tag -a vX.Y.Z-notDIRK -m "Release X.Y.Z-notDIRK"
git push origin vX.Y.Z-notDIRK
gh release create vX.Y.Z-notDIRK --repo notDIRK/shelly-integrator-ha \
  --title "vX.Y.Z-notDIRK" --notes "…"
```
