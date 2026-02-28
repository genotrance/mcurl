# Agent Guidelines for mcurl

## Git commits

- Keep commit messages to a single line (no multi-line messages).

## Before pushing to GitHub

- Test all affected configurations locally (if possible) before pushing to GitHub.
- Cancel all old/running jobs on GitHub Actions before pushing new changes.
- Monitor jobs after pushing until they complete and confirm they pass.
- Do not wait for all jobs to finish before starting fixes — use `gh run view --log-failed` to fetch failure logs as soon as a job fails, diagnose immediately, and push fixes as soon as you have a clear picture of all failing cases.

## Documentation

- Keep `docs/` and `README.md` up to date with any changes made to the code, build system, or CI configuration.
- Update `docs/ci.md` when workflows change, `docs/build.md` when the build system changes, and `docs/testing.md` when test structure changes.
- Docs should explain **what was done and why** so they serve as a future reference of how the project evolved. Do not copy file contents (config snippets, YAML, TOML) verbatim into docs — refer to files by name and describe the intent instead.

## Scope discipline

- Do not remove any capability or support unless explicitly asked by the user.
- Do not weaken or delete tests without explicit direction.

## Test coverage

- Make sure test cases test all features and configurations of the project.
- When adding new features or fixing bugs, add or update tests to cover the new behaviour.
- All tests must pass on CPython 3.9–3.14 (including free-threaded 3.14t) and PyPy 3.10/3.11 before merging.
