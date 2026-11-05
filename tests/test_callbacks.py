"""Tests for application callback protocol contracts.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.callbacks import (
    ApplicationEvaluator,
    CodecPayload,
    Operator,
    Solution,
    SolutionCodec,
    SolutionIdentity,
    StatePayload,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation


class CallbackBundle:
    component_id = 'callbacks'
    configuration_id = 'callbacks-v1'

    def evaluate(
        self, solution: object, objectives: ObjectiveDeclaration
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (0, 0))

    def equivalent(self, left: object, right: object) -> bool:
        return left == right

    def capture(self, solution: object) -> object:
        return solution

    def restore(self, payload: object) -> object:
        return payload

    def call(self, context: CandidateContext) -> OperatorResult:
        return OperatorResult(m=0, candidate=None)


def test_application_callbacks_are_structural_protocols() -> None:
    callbacks = CallbackBundle()
    evaluator: ApplicationEvaluator = callbacks
    identity: SolutionIdentity = callbacks
    codec: SolutionCodec = callbacks
    operator: Operator = callbacks

    assert evaluator.evaluate((0,), None) == ApplicationEvaluation(True, True, (0, 0))  # type: ignore[arg-type]
    assert identity.equivalent((0,), (0,))
    assert codec.restore(codec.capture((0,))) == (0,)
    assert operator.call(None).m == 0  # type: ignore[arg-type]
    solution: Solution = (1, 0)
    payload: CodecPayload = codec.capture(solution)
    state_payload: StatePayload = payload
    assert state_payload == solution
