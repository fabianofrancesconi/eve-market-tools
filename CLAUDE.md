# EVE Market Tools — developer notes

## Releases

**Every commit that changes behaviour must bump the version.** The UI reads `__version__` directly — if it is not updated, the version badge in the browser will not change.

### Mandatory steps on every commit (do not wait for the user to ask):

1. **Update `__version__ = "x.y.z"`** in `lp-web.py` line 13 — this is what the UI displays
2. Run `pytest tests/` — all tests must pass
3. Commit with message `vx.y.z: <description>`
4. `git tag vx.y.z` (after the commit, use the commit hash if tagging retroactively)
5. `git push origin master --tags` via WSL — always push branch and tags together in one command

**Never** push a tag alone (`git push origin vX.Y.Z`) — it can silently skip CI.

The Docker image is only built on `v*` tag pushes. Each published image gets `latest`, `v1.x.y`, and `1.x` tags.

## Tests

```
pytest tests/
```

All changes need corresponding tests in `tests/`.

## Git / push

GitHub auth doesn't work from the Windows shell — always push from WSL:

```
wsl -e bash -c "cd /mnt/c/Users/fabia/OneDrive/Documents/eve-scanner && git push origin master --tags"
```
