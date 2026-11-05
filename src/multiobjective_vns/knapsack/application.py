"""Assemble core callbacks and Operator registrations for a knapsack instance.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass
from pathlib import Path

from multiobjective_vns.knapsack.codec import KnapsackSolutionCodec
from multiobjective_vns.knapsack.evaluation import KnapsackApplicationEvaluator
from multiobjective_vns.knapsack.identity import KnapsackSolutionIdentity
from multiobjective_vns.knapsack.instance import KnapsackInstance
from multiobjective_vns.knapsack.objectives import build_objective_declaration
from multiobjective_vns.knapsack.operators.catalog import (
    KnapsackOperatorCatalog,
    ProfileRegistration,
    build_operator_catalog,
)
from multiobjective_vns.knapsack.toml_io import load_instance
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.registrations import OperatorGroup, OperatorRegistration


@dataclass(frozen=True, slots=True)
class KnapsackApplication:
    """Application callbacks and registered Operators bound to one instance."""

    instance: KnapsackInstance
    solution_codec: KnapsackSolutionCodec
    solution_identity: KnapsackSolutionIdentity
    objective_declaration: ObjectiveDeclaration
    application_evaluator: KnapsackApplicationEvaluator
    operator_catalog: KnapsackOperatorCatalog

    @property
    def problem_instance_id(self) -> str:
        """Return the problem identity used by the core search constructor."""
        return self.instance.problem_instance_id

    @property
    def operators(self) -> tuple[OperatorRegistration, ...]:
        """Return core registrations for the built-in and profile Operators."""
        return self.operator_catalog.operators

    @property
    def operator_groups(self) -> tuple[OperatorGroup, ...]:
        """Return caller-provided soft composition hints."""
        return self.operator_catalog.operator_groups


def load_knapsack_application(
    path: str | Path,
    *,
    profiles: tuple[ProfileRegistration, ...] = (),
    operator_groups: tuple[OperatorGroup, ...] = (),
) -> KnapsackApplication:
    """Load a TOML instance and construct its core application components.

    :param path: Path to the version-one TOML instance file.
    :param profiles: Optional named profile configurations.
    :param operator_groups: Optional composition hints over registered IDs.
    :returns: Components ready to pass to :class:`MultiobjectiveVNS`.
    :raises KnapsackInputError: If the input file is malformed.
    :raises ValueError: If profiles or groups do not match the instance catalog.
    """
    instance = load_instance(path)
    return build_knapsack_application(
        instance,
        profiles=profiles,
        operator_groups=operator_groups,
    )


def build_knapsack_application(
    instance: KnapsackInstance,
    *,
    profiles: tuple[ProfileRegistration, ...] = (),
    operator_groups: tuple[OperatorGroup, ...] = (),
) -> KnapsackApplication:
    """Build application callbacks and catalog from validated instance data.

    :param instance: Validated immutable knapsack instance.
    :param profiles: Optional named profile configurations.
    :param operator_groups: Optional composition hints over registered IDs.
    :returns: Components ready to pass to :class:`MultiobjectiveVNS`.
    """
    catalog = build_operator_catalog(
        instance,
        profiles=profiles,
        operator_groups=operator_groups,
    )
    return KnapsackApplication(
        instance=instance,
        solution_codec=KnapsackSolutionCodec(),
        solution_identity=KnapsackSolutionIdentity(),
        objective_declaration=build_objective_declaration(instance),
        application_evaluator=KnapsackApplicationEvaluator(instance),
        operator_catalog=catalog,
    )
