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

**Tags are created at push time, not before, and pushed ONE AT A TIME.** When asked
to push, work through the local `vX.Y.Z: ...` commits oldest-first: tag the commit
(`git tag vX.Y.Z <sha>`), push that single tag with the branch, wait for its Docker
workflow run to finish, then move on to the next one:

```
git tag v1.2.3 <sha>
git push origin master v1.2.3
gh run list --workflow=docker.yml --limit 1   # confirm it completed before tagging the next
```

⚠️ **Never push more than one new `vX.Y.Z` tag in a single `git push` command**, and
never use `git push origin master --tags`. The Docker workflow tags every build
`latest` in addition to its version tag; pushing several tags at once makes GitHub
kick off multiple workflow runs concurrently with no guaranteed ordering between them,
and whichever run's `docker push` finishes last wins the mutable `latest` tag — so
`latest` can end up pointing at an *older* release even though every run reports
success. (Confirmed: pushing v1.49.0 and v1.50.0 together left `latest` == v1.49.0's
image until v1.50.0's run was manually re-triggered alone.) Separately, pushing more
than three new tags in one push makes GitHub trigger **no** tag workflows at all for
that push — another reason to go one at a time. To re-trigger a tag whose workflow
didn't run, delete it on the remote and re-push it:
`git push origin :refs/tags/vX.Y.Z && git push origin vX.Y.Z`. If `latest` ever looks
wrong, compare digests (`docker manifest inspect ghcr.io/.../eve-market-tools:latest`
vs the newest version tag) and fix by re-running just that tag's workflow
(`gh run rerun <run-id>`) once no other docker.yml run is in flight.

The Docker image is only built on `v*` tag pushes (e.g. `v1.63.0`), but the registry
tags it produces drop the `v`: each published image gets `latest`, `sha-<commit>`, and
`x.y.z` (e.g. `1.63.0` — not `v1.63.0`). Use the `v` form for git tags and GitHub
releases, and the bare form when pulling from `ghcr.io` or comparing digests.

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
