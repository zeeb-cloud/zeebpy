# CI and dependency management

A push to `main` is a release: downstream projects install zeebpy straight
from the branch. Everything in this directory exists to make sure that what
lands on `main` passed the full gate against a known set of dependencies,
and that a dependency moving under us is a pull request, not a surprise.

## The pieces

| File | Role |
| --- | --- |
| `workflows/ci.yml` | The gate: lint + mypy ratchet, `constraints`, the suite on Python 3.10/3.12/3.14, dialect conformance on PostgreSQL and MySQL. Runs on every PR and push to `main`. |
| `../requirements-ci.txt` | Pinned, cross-platform (`--universal`) resolution of `pyproject.toml` with every extra. CI installs with `-c requirements-ci.txt`, so a run is reproducible. |
| `dependabot.yml` | Bumps the pins in `requirements-ci.txt`, the actions in the workflows, and the demo's requirements. Weekly, grouped. |
| `workflows/dependabot-auto-merge.yml` | Enables auto-merge on Dependabot PRs that are not a major bump. GitHub merges only once the ruleset's required checks pass. |
| `workflows/dependencies.yml` | Weekly canary: the suite against **unpinned latest** and against the **lowest floors** `pyproject.toml` declares, plus `pip-audit`. Also runs on a PR that touches the dependency files. |
| `rulesets/main.json` | The branch ruleset that makes the above safe: PRs only, the CI jobs are required checks, repository admins may bypass. |

## Why a pinned file for a library with `>=` floors

`pyproject.toml` keeps loose lower bounds on purpose: zeebpy is a library and
must not dictate versions to the projects that use it. But installing
"whatever is newest" in CI means an upstream release can turn `main` red
without any commit of ours, and a failing run on an unrelated PR is then a
puzzle. With `requirements-ci.txt`:

- every CI run installs the same versions, so a red run is caused by the diff;
- every upstream release arrives as a Dependabot PR whose CI run is the
  compatibility test for that release; small bumps merge on their own;
- `dependencies.yml` still tests the unpinned world every week, in both
  directions (latest and lowest), so the floors stay honest.

Developers do not need the file. `pip install -e ".[all,dev]"` stays the
local workflow; pass `-c requirements-ci.txt` to reproduce CI exactly.

## When you change `pyproject.toml`

Regenerate the pins, or the `constraints` job fails:

```bash
uv pip compile pyproject.toml --all-extras --universal --python-version 3.10 -o requirements-ci.txt
```

`uv` is in the `dev` extra. The command keeps existing pins and only adds or
re-resolves what the change requires; use `--upgrade` to refresh everything
(normally Dependabot's job). Raising a floor is a real change: the `lowest`
job in `dependencies.yml` runs on the PR and must pass on Python 3.10.

## Dependabot PR lifecycle

1. Dependabot opens a PR (Monday 06:00 Berlin). Minor and patch updates are
   grouped: one PR for runtime libraries, one for dev tools, one for actions.
   A major update is its own PR.
2. `ci.yml` and `dependencies.yml` run on it like on any PR.
3. `dependabot-auto-merge.yml` enables auto-merge unless the update is a
   major. GitHub squash-merges once every required check is green.
4. A major update, or any PR whose checks fail, waits for a human.
   `@dependabot rebase`, `@dependabot ignore this major version` etc. work as
   usual in PR comments.

The merge is made with the workflow's `GITHUB_TOKEN`, so it does not trigger
the push-to-`main` CI run. That is fine: the PR already ran the identical
gate on the exact commit that was merged.

## One-time repository setup

The workflows are inert until the repository allows auto-merge and has a
ruleset with required checks (auto-merge refuses to enable without one).
Both are a `gh` call away:

```bash
# 1. allow the auto-merge feature
gh api -X PATCH repos/zeeb-cloud/zeebpy -F allow_auto_merge=true

# 2. protect main with the ruleset (PRs only, CI jobs required, admins may bypass)
gh api -X POST repos/zeeb-cloud/zeebpy/rulesets --input .github/rulesets/main.json

# 3. (recommended) Dependabot security updates, so an advisory on a pinned
#    package gets a PR immediately instead of at the next weekly run
gh api -X PUT repos/zeeb-cloud/zeebpy/automated-security-fixes

# 4. (optional) labels Dependabot puts on its PRs — it creates missing ones
#    itself, this only picks the colours
gh label create dependencies --color 0366d6 --description "Dependency updates" || true
gh label create python --color 3572A5 || true
gh label create github-actions --color 2088FF || true
gh label create demo --color fbca04 || true
```

To update the ruleset later, edit the JSON and `PUT` it back
(`gh api repos/zeeb-cloud/zeebpy/rulesets` lists the id; the live one is 26749928):

```bash
gh api -X PUT repos/zeeb-cloud/zeebpy/rulesets/26749928 --input .github/rulesets/main.json
```
 Rename a CI job and the ruleset's `required_status_checks`
must follow, or every PR blocks on a check that never reports.

## Actions are pinned by commit

`uses:` lines pin a full SHA with the version as a trailing comment. That is
what makes a compromised or re-tagged action release harmless. Dependabot
understands the comment and bumps both together; never pin a new action by
tag alone.
