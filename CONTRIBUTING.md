# Contributing

## Dev setup

```
uv sync --extra dev
```

## Checks before opening a PR

```
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --strict src/pciv
uv run pytest -q
```

## Filing issues

Open an issue at <https://github.com/patschmitt91/PCIV/issues>. Use
the Bug Report or Feature Request template.
