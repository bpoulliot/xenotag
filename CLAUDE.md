# Xenotag — Claude Code Instructions

## Git Workflow

- **Never commit directly to `main`, and never branch in the shared checkout.** `~/dev/xenotag`
  is shared with other sessions and the overnight runner, so a switch there carries their
  uncommitted work onto your branch. Create a feature branch in a worktree of your own:
  ```
  git -C ~/dev/xenotag fetch origin
  git -C ~/dev/xenotag worktree add /tmp/xt-<slug> -b feat/<short-description> origin/main
  cd /tmp/xt-<slug>
  ```
  and `git -C ~/dev/xenotag worktree remove /tmp/xt-<slug>` once its PR has merged.
- Branch names: `feat/`, `fix/`, `chore/` prefixes matching the commit type.
- After pushing a branch, open a PR rather than merging locally.

## Commit Style

- Follow Conventional Commits: `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`.
- Scope in parens when useful: `fix(poster):`, `feat(tagger):`.
- One logical change per commit; don't bundle unrelated fixes.

## Code Conventions

- Run `ruff check` and `black --check` before committing. Fix any violations.
