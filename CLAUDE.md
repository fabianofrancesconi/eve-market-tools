# EVE Market Tools — developer notes

## Releases

**One version bump per push, NOT per commit.** A version corresponds to a *release*
(one Docker image, one `latest`), not to an individual commit. The UI reads
`__version__` directly — if it is not updated, the version badge in the browser will
not change — so every push that ships behaviour changes must carry exactly one bump.

### What `x.y.z` means

- **`z` (patch)** — the default. Bump `z` for essentially everything: bug fixes,
  refinements, small user-visible tweaks, internal changes, most feature work. Unless a
  release clears the bar for a `y` bump below, it increments `z`.
- **`y` (minor)** — bump `y` (and reset `z` to 0) only for a *big* release: a new
  feature or capability, a large or sweeping change, a major user-visible change, or a
  significant rewrite. This is the exception, not the norm.
- **`x` (major)** — left alone unless there's a genuinely huge, deliberate milestone;
  don't reach for it without the user's say-so.

When in doubt, bump `z`. A batch that collates several small fixes is a `z` bump even
if there are many commits; it's the *size/nature of the change*, not the commit count,
that decides `y`.

### While working (before a push is requested):

Commit freely. A workstream can be many commits — split them however makes the history
readable (one per logical fix is fine). **Do NOT bump `__version__` on each commit and
do NOT put `vX.Y.Z:` in every commit message.** Those intermediate commits are just
work-in-progress; use plain descriptive messages (e.g. `Fix notes autosave data loss`).
Do run `pytest tests/` as you go — all tests must pass.

### When the user asks to push ("push", "ship it"):

Collate everything since the last released tag into a *single* release:

1. Pick the next version `x.y.z` for the whole batch (one bump for all the commits).
2. **Update `__version__ = "x.y.z"`** in `lp-web.py` (near the top) — what the UI shows.
3. Run `pytest tests/` — all must pass.
4. Commit the bump with message `vx.y.z: <summary of the batch>` (a short summary line;
   the body can bullet the notable changes). This is the only versioned commit.
5. Tag that commit and push the branch + that one tag together:

```
git tag v1.2.3 <sha>
git push origin master v1.2.3
gh run list --workflow=docker.yml --limit 1   # confirm it completed
```

There is normally only **one** new tag per push now, so the multi-tag ordering hazard
below rarely applies — but the rule still holds if you ever have more than one.

⚠️ **Never push more than one new `vX.Y.Z` tag in a single `git push` command**, and
never use `git push origin master --tags`. The Docker workflow tags every build
`latest` in addition to its version tag; pushing several tags at once makes GitHub
kick off multiple workflow runs concurrently with no guaranteed ordering between them,
and whichever run's `docker push` finishes last wins the mutable `latest` tag — so
`latest` can end up pointing at an *older* release even though every run reports
success. (Confirmed: pushing v1.49.0 and v1.50.0 together left `latest` == v1.49.0's
image until v1.50.0's run was manually re-triggered alone.) Separately, pushing more
than three new tags in one push makes GitHub trigger **no** tag workflows at all for
that push. If for some reason you do have multiple tags to ship, push them ONE AT A
TIME, waiting for each Docker run to finish before the next. To re-trigger a tag whose
workflow didn't run, delete it on the remote and re-push it:
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
