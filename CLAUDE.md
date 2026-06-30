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

When pushing, always push branch and tags together in one command:

```
git push origin master --tags
```

**Never** push a tag alone (`git push origin vX.Y.Z`) — it can silently skip CI.

The Docker image is only built on `v*` tag pushes. Each published image gets `latest`, `v1.x.y`, and `1.x` tags.

## Tests

```
pytest tests/
```

All changes need corresponding tests in `tests/`.

## Git / push

The `origin` remote uses **SSH** (`git@github.com:fabianofrancesconi/eve-market-tools.git`),
which authenticates as the personal `fabianofrancesconi` account via the local SSH key.
Push directly:

```
git push origin master --tags
```

Do **not** use an HTTPS remote — it falls back to a stale `fabiano_adobe` keychain
credential (Adobe work account) that lacks push access and fails with 403.
