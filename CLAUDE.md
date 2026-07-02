# EVE Market Tools — developer notes

## Releases

**Every commit that changes behaviour must bump the version.** The UI reads `__version__` directly — if it is not updated, the version badge in the browser will not change.

### Steps on every behaviour-changing commit (do not wait for the user to ask):

1. **Update `__version__ = "x.y.z"`** in `lp-web.py` line 13 — this is what the UI displays
2. Run `pytest tests/` — all tests must pass
3. Commit with message `vx.y.z: <description>`

**Do NOT tag at commit time, and do NOT push automatically.** A commit is not yet a
release — it may still be WIP, get amended, or get reordered before it ships. Tagging
early just litters the repo with tags for commits that were never actually released.
Only push when the user explicitly asks (e.g. "push", "ship it"); several versioned
commits can pile up locally first and go out together.

**Tags are created at push time, not before.** When asked to push, for each local
`vX.Y.Z: ...` commit that's about to go out, create its tag on that commit right
then, and push **one tag at a time** — each in its own `git push` command:

```
git tag v1.2.3 <sha>
git push origin master v1.2.3

git tag v1.2.4 <sha>
git push origin v1.2.4

git tag v1.2.5 <sha>
git push origin v1.2.5
```

⚠️ **Always push exactly ONE tag per `git push` command.** GitHub's CI is unreliable
when multiple tags arrive in a single push — workflows get skipped silently. The first
push includes `master` (to advance the branch); subsequent pushes only need the tag.

Never use `--tags` and never batch multiple tags in one push command.

To re-trigger a tag whose workflow was skipped, delete it on the remote and re-push:
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
Push directly, one tag per push (see the ⚠️ note above):

```
git push origin master v1.2.3    # first push: branch + one tag
git push origin v1.2.4           # second tag alone
```

Do **not** use an HTTPS remote — it falls back to a stale `fabiano_adobe` keychain
credential (Adobe work account) that lacks push access and fails with 403.
