"""Coalesce candidate profiles and select weighted representatives.

Created: 2026-11-03T17:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping

from multiobjective_vns.multiday_vrp.scenario_generation.errors import (
    ScenarioReductionError,
)
from multiobjective_vns.multiday_vrp.scenario_generation.records import (
    CandidateAssignment,
    ProfileCoordinate,
    ProtectedTrafficGroup,
    ScenarioProbability,
)

__all__ = [
    'CandidateProfile',
    'ReductionResult',
    'coalesce_profiles',
    'reduce_profiles',
]


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    block_start_date: str
    source_block_start_dates: tuple[str, ...]
    profile: Mapping[ProfileCoordinate, float]
    probability: float
    group_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class ReductionResult:
    candidates: tuple[CandidateProfile, ...]
    retained_indices: tuple[int, ...]
    assignments: tuple[CandidateAssignment, ...]
    probabilities: tuple[ScenarioProbability, ...]
    reduction_cost: float
    effective_distance_weights: Mapping[ProfileCoordinate, float]
    group_representatives: Mapping[str, tuple[str, ...]]


@dataclass(slots=True)
class _CoalescedProfile:
    first_date: str
    source_dates: list[str]
    profile: dict[ProfileCoordinate, float]
    probability: float
    group_ids: set[str]


def coalesce_profiles(
    profiles: list[
        tuple[str, Mapping[ProfileCoordinate, float], float, frozenset[str]]
    ],
    coordinate_order: tuple[ProfileCoordinate, ...],
) -> tuple[CandidateProfile, ...]:
    """Combine exact profile duplicates and add their probability mass."""
    grouped: dict[tuple[float, ...], _CoalescedProfile] = {}
    for block_start, profile, probability, memberships in profiles:
        signature = tuple(profile[coordinate] for coordinate in coordinate_order)
        if signature not in grouped:
            grouped[signature] = _CoalescedProfile(
                block_start, [], dict(profile), 0.0, set()
            )
        candidate = grouped[signature]
        candidate.source_dates.append(block_start)
        candidate.probability += probability
        candidate.group_ids.update(memberships)
    candidates = [
        CandidateProfile(
            block_start_date=item.first_date,
            source_block_start_dates=tuple(item.source_dates),
            profile=item.profile,
            probability=item.probability,
            group_ids=frozenset(item.group_ids),
        )
        for item in grouped.values()
    ]
    return tuple(candidates)


def reduce_profiles(
    candidates: tuple[CandidateProfile, ...],
    scenario_count: int,
    raw_weights: Mapping[ProfileCoordinate, float],
    groups: tuple[ProtectedTrafficGroup, ...],
) -> ReductionResult:
    """Apply normalized weighted-L1 greedy backward scenario reduction."""
    if not candidates:
        raise ScenarioReductionError(reason='no candidate profiles are available')
    coordinates = tuple(candidates[0].profile)
    ranges = {
        coordinate: max(item.profile[coordinate] for item in candidates)
        - min(item.profile[coordinate] for item in candidates)
        for coordinate in coordinates
    }
    variable = tuple(
        coordinate for coordinate in coordinates if ranges[coordinate] > 0.0
    )
    effective_weights: dict[ProfileCoordinate, float] = {}
    if variable:
        raw = {coordinate: raw_weights.get(coordinate, 1.0) for coordinate in variable}
        total_weight = sum(raw.values())
        if not isfinite(total_weight) or total_weight <= 0.0:
            raise ScenarioReductionError(
                reason='scenario-distance weights must have a positive sum over varying dimensions'
            )
        effective_weights = {
            coordinate: value / total_weight for coordinate, value in raw.items()
        }
    distances = _distance_matrix(candidates, variable, ranges, effective_weights)
    memberships = [item.group_ids for item in candidates]
    for group in groups:
        available = sum(group.group_id in member for member in memberships)
        if available < group.minimum_representatives:
            raise ScenarioReductionError(
                reason=(
                    f'protected group {group.group_id!r} has {available} distinct '
                    f'candidate profiles but requires {group.minimum_representatives}'
                )
            )
        if scenario_count < group.minimum_representatives:
            raise ScenarioReductionError(
                reason=(
                    f'scenario_count {scenario_count} cannot satisfy protected group '
                    f'{group.group_id!r} minimum {group.minimum_representatives}'
                )
            )

    retained = list(range(len(candidates)))
    while len(retained) > scenario_count:
        chosen: int | None = None
        chosen_cost = float('inf')
        for candidate_index in retained:
            proposed = [index for index in retained if index != candidate_index]
            if any(
                sum(group.group_id in memberships[index] for index in proposed)
                < group.minimum_representatives
                for group in groups
            ):
                continue
            proposed_cost = _reduction_cost(candidates, proposed, distances)
            if proposed_cost < chosen_cost:
                chosen = candidate_index
                chosen_cost = proposed_cost
        if chosen is None:
            raise ScenarioReductionError(
                reason='requested scenario_count cannot satisfy protected traffic groups'
            )
        retained.remove(chosen)

    retained_scenario_ids = {
        index: f'scenario-{position:06d}'
        for position, index in enumerate(retained, start=1)
    }
    assigned_to: dict[int, int] = {}
    for candidate_index in range(len(candidates)):
        if candidate_index in retained_scenario_ids:
            assigned_to[candidate_index] = candidate_index
            continue
        nearest = min(retained, key=lambda index: distances[candidate_index][index])
        assigned_to[candidate_index] = nearest
    mass_by_retained = {index: 0.0 for index in retained}
    assignments: list[CandidateAssignment] = []
    for index, candidate in enumerate(candidates):
        recipient = assigned_to[index]
        mass_by_retained[recipient] += candidate.probability
        retained_candidate = index in retained_scenario_ids
        assignments.append(
            CandidateAssignment(
                source_block_start_dates=candidate.source_block_start_dates,
                source_probability=candidate.probability,
                retained_scenario_id=retained_scenario_ids[recipient],
                retained=retained_candidate,
                transferred_probability_mass=0.0
                if retained_candidate
                else candidate.probability,
            )
        )
    probabilities = tuple(
        ScenarioProbability(retained_scenario_ids[index], mass_by_retained[index])
        for index in retained
    )
    total = sum(item.probability for item in probabilities)
    if not isfinite(total) or total <= 0.0:
        raise ScenarioReductionError(reason='scenario probability total is invalid')
    probabilities = tuple(
        ScenarioProbability(item.scenario_id, item.probability / total)
        for item in probabilities
    )
    group_representatives = {
        group.group_id: tuple(
            retained_scenario_ids[index]
            for index in retained
            if group.group_id in memberships[index]
        )
        for group in groups
    }
    return ReductionResult(
        candidates=candidates,
        retained_indices=tuple(retained),
        assignments=tuple(assignments),
        probabilities=probabilities,
        reduction_cost=_reduction_cost(candidates, retained, distances),
        effective_distance_weights=effective_weights,
        group_representatives=group_representatives,
    )


def _distance_matrix(
    candidates: tuple[CandidateProfile, ...],
    coordinates: tuple[ProfileCoordinate, ...],
    ranges: Mapping[ProfileCoordinate, float],
    weights: Mapping[ProfileCoordinate, float],
) -> list[list[float]]:
    result = [[0.0] * len(candidates) for _ in candidates]
    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            distance = sum(
                weights[coordinate]
                * abs(
                    candidates[left].profile[coordinate]
                    - candidates[right].profile[coordinate]
                )
                / ranges[coordinate]
                for coordinate in coordinates
            )
            result[left][right] = distance
            result[right][left] = distance
    return result


def _reduction_cost(
    candidates: tuple[CandidateProfile, ...],
    retained: list[int],
    distances: list[list[float]],
) -> float:
    if not retained:
        return float('inf')
    return sum(
        candidate.probability
        * min(distances[index][representative] for representative in retained)
        for index, candidate in enumerate(candidates)
    )
