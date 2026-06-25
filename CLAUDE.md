# EVE Market Tools — developer notes

## Releases

When shipping a new version, **always update `__version__`** in [`lp-web.py`](lp-web.py) (line 13) to match the version in the commit message. The HTML template substitutes `__VERSION__` at runtime via `.replace("__VERSION__", __version__)`, so the badge in the UI comes directly from this string.

Do this automatically on every commit — do not wait for the user to ask:
1. Update `__version__ = "x.y.z"` in `lp-web.py`
2. Commit with message `vx.y.z: <description>`
3. `git tag vx.y.z <commit-hash>`
4. Push commits and tag together in one command: `git push origin master --tags` (via WSL)
   — never push a tag alone (`git push origin vX.Y.Z`); that can silently skip CI

The Docker image is **only built on tag pushes** (not on every master commit). Pushing a `v*` tag is what triggers the CI build and publishes the image to GHCR with `latest`, `v1.x.y`, and `1.x` tags. Commits pushed without a tag will not produce a Docker image.

## Tests

Run before every commit:

```
pytest tests/
```

All changes need corresponding tests in `tests/`.

## Git / push

GitHub auth doesn't work from the Windows shell — always push from WSL:

```
wsl -e bash -c "cd /mnt/c/Users/fabia/OneDrive/Documents/eve-scanner && git push origin master --tags"
```
