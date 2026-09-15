# Sovra — Model Factory

Fine-tuning + evaluation factory that finds the lowest-cost model satisfying a
target agent product's locked quality, safety, privacy, latency, reliability,
and operational requirements. See `SPEC.md` for the product specification and
`docs/architecture/ARCHITECTURE.md` for the seven-component reuse architecture.

## Layout

This is a mixed-language monorepo: a TypeScript/JS workspace (managed by
`pnpm`) for the UI and shared contracts, and a Python workspace (managed by
`uv`) for the API and the worker packages that wrap the reused components
(Axolotl, Inspect AI, MLflow, vLLM, Temporal, LiteLLM, Argilla).

| Path | Component | Reused engine |
|---|---|---|
| `apps/web` | Unified Factory UI | — |
| `apps/api` | Factory API + policy | — |
| `workers/training` | Training worker | Axolotl |
| `workers/evaluation` | Evaluation worker | Inspect AI |
| `workers/data` | Data/review worker | Argilla |
| `workers/deploy` | Deployment controller | vLLM / LiteLLM |
| `packages/shared` | Shared schema/adapter contracts | — |

Each component directory currently holds a `README.md` describing its role
and a manifest (`package.json` or `pyproject.toml`); implementation lands in
later queue items (see `QUEUE.md`).

## Quick start

JS/TS workspace:

```sh
pnpm install
```

Python workspace:

```sh
uv sync
```

Neither command runs application code yet — FTF-0001 only establishes the
workspace layout. Formatting/lint/type checks (FTF-0002) and the test
harness (FTF-0003) are separate queue items.

## Verifying the layout

```sh
python3 tests/test_ftf_0001_monorepo_layout.py
```
