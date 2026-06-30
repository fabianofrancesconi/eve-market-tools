# EVE Market Tools — developer notes

## Releases

**Every commit that changes behaviour must bump the version.** The UI reads `__version__` directly — if it is not updated, the version badge in the browser will not change.

### Steps on every behaviour-changing commit (do not wait for the user to ask):

1. **Update `__version__ = "x.y.z"`** in `lp-web.py` line 13 — this is what the UI displays
2. Run `pytest tests/` — all tests must pass
3. Commit with message `vx.y.z: <description>`
4. `git tag vx.y.z` (after the commit, use the commit hash if tagging retroactively)

**Do NOT push automatically.** Commit and tag locally as you go, but only push when
the user explicitly asks (e.g. "push", "ship it"). Several tiny changes can pile up
across local commits/tags and go out in a single push.

When pushing, push the branch together with the release tag(s) in one command —
naming the tags explicitly:

```
git push origin master vX.Y.Z              # one release
git push origin master vX.Y.Z vX.Y.W       # up to three new tags at once
```

⚠️ **Do not use `git push origin master --tags` when more than three new tags are
pending.** GitHub silently triggers **no** tag workflows when a single push contains
more than three new tags — so all the Docker builds get skipped. (This — not "pushing
a tag alone" — is the real cause of silently-skipped CI.) Since the workflow above lets
local commits/tags pile up between pushes, `--tags` is exactly the case that bites.

If several tags have piled up, push them in **batches of ≤3** (`git push origin master
vA vB vC`, then `git push origin vD …`). A single-tag push **does** trigger CI; to
re-trigger a tag that was skipped, delete it on the remote and re-push it:
`git push origin :refs/tags/vX.Y.Z && git push origin vX.Y.Z`.

The Docker image is only built on `v*` tag pushes. Each published image gets `latest`, `v1.x.y`, and `1.x` tags.

## Tests

```
pytest tests/
```

All changes need corresponding tests in `tests/`.

## Git / push

The `origin` remote uses **SSH** (`git@github.com:fabianofrancesconi/eve-market-tools.git`),
which authenticates as the personal `fabianofrancesconi` account via the local SSH key.
Push directly, naming the release tag(s) (see the ⚠️ note above about `--tags` skipping
CI when more than three new tags are pushed at once):

```
git push origin master vX.Y.Z
```

Do **not** use an HTTPS remote — it falls back to a stale `fabiano_adobe` keychain
credential (Adobe work account) that lacks push access and fails with 403.
