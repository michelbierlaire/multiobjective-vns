"""Closed status vocabularies used by candidate and registry records.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from enum import StrEnum


class SubmissionSource(StrEnum):
    """Origin of a candidate submission."""

    INITIAL = 'initial'
    OPERATOR = 'operator'


class ProcessingStatus(StrEnum):
    """Availability of a classifiable application assessment."""

    COMPLETED = 'completed'
    FAILED = 'failed'


class ApplicationAssessment(StrEnum):
    """Application's summary finding for a solution."""

    VALID = 'valid'
    INVALID = 'invalid'
    INFEASIBLE = 'infeasible'


class SearchDisposition(StrEnum):
    """Engine classification of a candidate proposal's registry handling."""

    REGISTERED = 'registered'
    DUPLICATE = 'duplicate'
    DOMINATED = 'dominated'
    OBJECTIVE_VECTOR_DUPLICATE = 'objective_vector_duplicate'
    ARCHIVED = 'archived'
    OBJECTIVE_BOUND_EXCEEDED = 'objective_bound_exceeded'


class ArchiveStatus(StrEnum):
    """Current or historical archive standing of an identified solution."""

    PARETO_OPTIMAL = 'Pareto optimal'
    DOMINATED = 'Dominated'
    OBJECTIVE_VECTOR_DUPLICATE = 'Objective-vector duplicate'
    REJECTED = 'Rejected'


class CompositionPosition(StrEnum):
    """Position of an Operator call within an ordered composition."""

    FIRST = 'first'
    SECOND = 'second'


class TerminationReason(StrEnum):
    """Public termination classifications for search and maintenance calls."""

    BUDGET_EXHAUSTED = 'budget_exhausted'
    BUDGET_POLICY_STOP = 'budget_policy_stop'
    COMPONENT_FAILURE = 'component_failure'
    INITIALIZATION_FAILED = 'initialization_failed'
    INTERRUPTED = 'interrupted'
    MAINTENANCE_COMPLETED = 'maintenance_completed'
    NO_AVAILABLE_OPERATOR = 'no_available_operator'
    NO_AVAILABLE_RESTORATION_OPERATOR = 'no_available_restoration_operator'
