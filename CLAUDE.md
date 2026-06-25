# EVE Market Tools — developer notes

## Releases

When shipping a new version, **always update `__version__`** in [`lp-web.py`](lp-web.py) (line 13) to match the version in the commit message. The HTML template substitutes `__VERSION__` at runtime via `.replace("__VERSION__", __version__)`, so the badge in the UI comes directly from this string.

Checklist:
1. Update `__version__ = "x.y.z"` in `lp-web.py`
2. Commit with message `vx.y.z: <description>`
3. `git tag vx.y.z`
4. Push with `--tags`

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
