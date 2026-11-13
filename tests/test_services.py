"""Tests for execution, persistence, and reporting service protocols.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.records.failures import ComponentFailure, FailureKind
from multiobjective_vns.records.results import ExecutionResult
from multiobjective_vns.records.state import Checkpoint, CheckpointReference
from multiobjective_vns.services import (
    CheckpointStore,
    Executor,
    RunLogger,
    SolutionFormatter,
)


class FakeServices:
    component_id = 'service'
    configuration_id = 'service-v1'
    checkpoint: Checkpoint

    def execute(
        self, evaluator: object, solution: object, objectives: object
    ) -> ExecutionResult:
        return ExecutionResult(
            None,
            ComponentFailure(
                FailureKind.INFRASTRUCTURE_FAILURE,
                'service',
                'execution',
                'executor unavailable',
            ),
        )

    def save(self, checkpoint: Checkpoint) -> CheckpointReference:
        return 'checkpoint-ref'

    def load(self, reference: CheckpointReference) -> Checkpoint:
        return self.checkpoint

    def describe(self, solution: object) -> str:
        return str(solution)


def test_service_interfaces_are_structural() -> None:
    services = FakeServices()
    executor: Executor = services
    store: CheckpointStore = services
    formatter: SolutionFormatter = services

    result = executor.execute(None, None, None)  # type: ignore[arg-type]
    assert result.failure is not None
    assert store.save(None) == 'checkpoint-ref'  # type: ignore[arg-type]
    assert formatter.describe(('solution',)) == "('solution',)"
    assert callable(RunLogger.publish_view)
