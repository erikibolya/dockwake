# Contributing

Thanks for taking a look at DockWake.

## Workflow

- `main` is the stable branch.
- `dev` is the integration branch.
- Create a short-lived branch off `dev`: `feat/<feature>`, `fix/<bug>`,
  `docs/<topic>` or `chore/<topic>`.
- Open a pull request into `dev`. Keep it focused and include tests.
- `dev` and `main` are protected: required PR, `test`, `e2e` and `build`
  checks, no force pushes, no deletions.
- Releases are cut from a tag after `dev` has been merged into `main`.

## Tests

```bash
python -m unittest discover -s tests -v   # stdlib unit + HTTP integration
python -m pytest e2e -q                   # Playwright browser test
ruff check .
```

New features need automated coverage of their user-visible path and persisted
state. Bug fixes start with a failing regression test. See
[`TESTING.md`](TESTING.md) for the inventory.

## Style

- Python 3.12, standard library only at runtime.
- `ruff check .` and `ruff format .` must pass.
- Keep the app small and dependency-free; prefer the platform over libraries.
- UI changes should be checked with the Playwright test and a real browser.

## Security

Do not open public issues for security problems. Use GitHub's private
vulnerability reporting (Security → Advisories) or contact the maintainer.
