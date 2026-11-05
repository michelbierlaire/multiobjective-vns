# Minimal multi-day VRP example

This example contains a small deterministic ScenarioBank and a minimal VRP
instance. It demonstrates loading the instance, assembling the application
callbacks and feature-selected Operator catalog, and running the generic
`MultiobjectiveVNS` API from a feasible initial plan. The plan routes one
vehicle through two depot nodes and back to its overnight depot. The runner
selects the catalog's 2-opt Operator for a single bounded search attempt.

Run it from this directory with:

```console
uv run python run.py
```

The run uses a fixed random seed and writes one structured event log and one
human-readable executive summary under `multiobjective-vns-logs/` in the
current working directory. `run.py` also exposes `run_example()` so tests or
other Python code can choose a temporary log directory and inspect the
returned `RunResult`.

The instance has no service activities; it is a compact route-search example.
Applications with requests or interventions construct their own initial
`VrpSolution` values from the problem data.
