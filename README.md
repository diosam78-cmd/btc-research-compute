# btc-research-compute

Public, generic compute engine for systematic crypto backtesting research.

## Purpose

This repository contains reusable research primitives that can be developed,
tested, and executed with GitHub Actions without exposing private strategy
selection or private research results.

The public side may contain:

- generic Turtle/Donchian/ATR-style primitives
- deterministic fee, slippage, sizing, and portfolio exposure logic
- shared-wallet gross leverage gates
- synthetic tests and reproducibility checks
- CI workflows for the public software itself

The public side must **not** contain:

- exchange API keys, webhook credentials, tokens, or deploy credentials
- private production parameters or selected candidate configurations
- private optimization grids or hidden research hypotheses
- private performance results, trade logs, or portfolio decisions
- Pine/Binance production automation code that is intended to remain private

## Current phase

Phase 1 establishes a parameter-driven core with no production defaults.
Every strategy value must be supplied explicitly by a caller. The included
unit tests use synthetic toy values only.

The next phase will add a generic event-driven Turtle simulator and a
reproducibility harness. Private/public transport will be designed separately
so secrets and private results are not written to public logs or artifacts.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

CI runs on GitHub-hosted Linux runners and uploads no research artifacts.
