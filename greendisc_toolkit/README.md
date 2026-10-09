# Green DiSC toolkit actions

Composite actions used by the Green DiSC toolkit template, which helps research groups and teams gather and organise evidence for [Green DiSC](https://github.com/Cambridge-Sustainable-Computing-Lab/greenDiSC) certification.

| Action | Purpose |
|---|---|
| [`sync_criteria`](sync_criteria/README.md) | Generate `criteria.yml` from the upstream Green DiSC criteria |

## Development

Python tooling uses [uv](https://docs.astral.sh/uv/). From this folder:

```sh
uv run pytest       # tests fetch the upstream criteria into .cache/ on first run
uv run ruff check
uv run ruff format
```

Python versions follow [SPEC 0](https://scientific-python.org/specs/spec-0000/).
