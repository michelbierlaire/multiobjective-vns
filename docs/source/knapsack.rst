Multiobjective knapsack
=======================

The ``multiobjective_vns.knapsack`` subpackage loads a version-one TOML
instance and constructs the application callbacks and neighborhood Operators
for the core search. See ``docs/design/knapsack.md`` for the input format,
problem model, Operator behavior, and a complete search example.

The repository includes a runnable example for the published P410 instance in
``examples/knapsack/p410/``. Its objective contributions are maximized by the
knapsack model and converted to minimization values for the core package. Run
it from its own directory so its log files remain there:

.. code-block:: console

   cd examples/knapsack/p410
   uv run --project ../../.. python run.py

The runner reads ``instance.toml`` beside itself and writes the structured
event log and human-readable summary into ``multiobjective-vns-logs/`` under
the current working directory.

.. automodule:: multiobjective_vns.knapsack
   :members:
