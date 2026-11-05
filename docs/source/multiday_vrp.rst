Multi-day vehicle routing
=========================

The ``multiobjective_vns.multiday_vrp`` subpackage loads a validated VRP
instance, binds the instance to the evaluator and feature-selected Operator
catalog, and exposes the resulting callbacks for the generic search engine.
The application caller constructs and supplies the initial feasible
``VrpSolution``. Detailed problem, file-format, solution, codec, and Operator
contracts are maintained in ``docs/design/multiday_vrp*.md``.

Run the minimal deterministic example from its own directory:

.. code-block:: console

   cd examples/multiday_vrp/minimal
   uv run --project ../../.. python run.py

The runner writes a JSON Lines event log and a human-readable executive
summary under ``multiobjective-vns-logs/`` in the current working directory.

Application assembly
--------------------

.. autoclass:: multiobjective_vns.multiday_vrp.VrpApplication
   :members:

.. autofunction:: multiobjective_vns.multiday_vrp.load_vrp_application

.. autofunction:: multiobjective_vns.multiday_vrp.build_vrp_application

.. autofunction:: multiobjective_vns.multiday_vrp.load_vrp_instance

.. autofunction:: multiobjective_vns.multiday_vrp.build_default_vrp_operator_catalog

Solution value and codec
------------------------

.. autoclass:: multiobjective_vns.multiday_vrp.VrpSolution

.. autoclass:: multiobjective_vns.multiday_vrp.VrpSolutionCodec

.. autoclass:: multiobjective_vns.multiday_vrp.VrpSolutionIdentity
