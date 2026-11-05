"""Tests for reporting and run-provenance contracts.

Created: 2026-10-29T13:46:09+00:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.identifiers import ChoiceId, CompositionId, SolutionId
from multiobjective_vns.records import PromotedCompositionReport as RecordsReport
from multiobjective_vns.records.reporting import (
    ChoiceStatistics,
    ModificationCountFrequency,
    ParetoSolutionReport,
    PromotedCompositionReport,
    ReportDiagnostic,
    ReportDiagnosticKind,
)
from multiobjective_vns.records.results import (
    PromotedCompositionReport as ResultsReport,
)


def test_report_diagnostic_requires_fields_for_its_kind() -> None:
    diagnostic = ReportDiagnostic(
        kind=ReportDiagnosticKind.SOLUTION_DESCRIPTION_FAILURE,
        source='formatter',
        solution_id=SolutionId(1),
        objective_id=None,
        reason='description callback failed',
    )

    assert diagnostic.solution_id == SolutionId(1)
    with pytest.raises(ValueError, match='solution-description'):
        ReportDiagnostic(
            kind=ReportDiagnosticKind.SOLUTION_DESCRIPTION_FAILURE,
            source='formatter',
            solution_id=None,
            objective_id=None,
            reason='description callback failed',
        )


def test_choice_statistics_record_attempts_and_modification_counts() -> None:
    statistics = ChoiceStatistics(
        choice_id=ChoiceId.for_operator('swap'),
        k=2,
        modification_count_definition='number of changed item decisions',
        attempt_count=2,
        archive_admission_count=1,
        archive_reward_total=0.75,
        modification_count_frequencies=(
            ModificationCountFrequency(m=0, count=1),
            ModificationCountFrequency(m=2, count=1),
        ),
    )

    assert statistics.admission_rate == 0.5
    assert statistics.archive_reward_rate == 0.375
    assert ParetoSolutionReport(SolutionId(1), (3, 7), 'sample').description == 'sample'


def test_promoted_composition_report_keeps_origin_and_operator_metadata() -> None:
    source = CompositionId('first', 'second')
    report = PromotedCompositionReport(
        operator_id='promoted:composition-1',
        source_composition_id=source,
        component_operator_ids=('first', 'second'),
        first_admitted_solution_id=SolutionId(3),
        max_neighborhood_size=4,
        feasibility_restoration=True,
        inherited_group_ids=frozenset(('repair',)),
        promoted_in_current_invocation=True,
    )

    assert report.source_composition_id == source
    assert report.component_operator_ids == ('first', 'second')
    assert report.first_admitted_solution_id == SolutionId(3)
    assert report.promoted_in_current_invocation is True
    assert RecordsReport is PromotedCompositionReport
    assert ResultsReport is PromotedCompositionReport


def test_promoted_composition_report_requires_matching_components() -> None:
    with pytest.raises(ValueError, match='component_operator_ids'):
        PromotedCompositionReport(
            operator_id='promoted:composition-1',
            source_composition_id=CompositionId('first', 'second'),
            component_operator_ids=('first', 'other'),
            first_admitted_solution_id=SolutionId(3),
            max_neighborhood_size=1,
            feasibility_restoration=False,
            inherited_group_ids=frozenset(),
            promoted_in_current_invocation=False,
        )
