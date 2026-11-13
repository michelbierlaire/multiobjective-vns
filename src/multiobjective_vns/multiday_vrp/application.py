"""Assemble validated VRP data and callbacks for the generic VNS API.

Created: 2026-11-03T23:54:30+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from multiobjective_vns.identifiers import (
    ObjectiveId,
    ProblemInstanceId,
    identifier_text,
)
from multiobjective_vns.multiday_vrp.codec import VrpSolutionCodec
from multiobjective_vns.multiday_vrp.evaluation.evaluator import (
    VrpApplicationEvaluator,
)
from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    build_vrp_objective_declaration,
)
from multiobjective_vns.multiday_vrp.extensions import (
    VrpExtensionRegistry,
    empty_vrp_extension_registry,
)
from multiobjective_vns.multiday_vrp.identity import VrpSolutionIdentity
from multiobjective_vns.multiday_vrp.input_loader import load_vrp_instance
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.catalog import (
    VrpOperatorCatalog,
    VrpOperatorCatalogSettings,
    build_default_vrp_operator_catalog,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.route_path import (
    AdditivePathCostRegistration,
)
from multiobjective_vns.multiday_vrp.scenario_generation.records import (
    PostHorizonRule,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.registrations import (
    OperatorCompositionRegistration,
    OperatorGroup,
    OperatorRegistration,
)

__all__ = [
    'VrpApplication',
    'build_vrp_application',
    'load_vrp_application',
]


@dataclass(frozen=True, slots=True)
class VrpApplication:
    """Callbacks, catalog, and identity bound to one validated VRP instance.

    The fields can be passed directly to the corresponding
    :class:`multiobjective_vns.MultiobjectiveVNS` constructor arguments.

    :param instance: Normalized input data and its feature profile.
    :param problem_instance_id: Stable identity for the bound problem data.
    :param solution_codec: Immutable VRP plan checkpoint codec.
    :param solution_identity: Canonical VRP plan identity callback bound to the
        loaded pickup symmetry declarations.
    :param objective_declaration: Ordered objective comparison contract.
    :param application_evaluator: Evaluator bound to the instance and extensions.
    :param operators: Feature-selected default Operator registrations.
    :param pre_registered_compositions: Default composite Operators enabled by
        active features and selected objective targets.
    :param operator_groups: Validated soft composition hints.
    """

    instance: LoadedVrpInstance
    problem_instance_id: ProblemInstanceId
    solution_codec: VrpSolutionCodec
    solution_identity: VrpSolutionIdentity
    objective_declaration: ObjectiveDeclaration
    application_evaluator: VrpApplicationEvaluator
    operators: tuple[OperatorRegistration, ...]
    pre_registered_compositions: tuple[OperatorCompositionRegistration, ...]
    operator_groups: tuple[OperatorGroup, ...]


def load_vrp_application(
    path: str | Path,
    *,
    problem_instance_id: ProblemInstanceId | None = None,
    extension_registry: VrpExtensionRegistry | None = None,
    post_horizon_rules: Mapping[tuple[str, str], PostHorizonRule] | None = None,
    additive_path_costs: Mapping[ObjectiveId, AdditivePathCostRegistration]
    | None = None,
    settings: VrpOperatorCatalogSettings = VrpOperatorCatalogSettings(),
    operator_groups: tuple[OperatorGroup, ...] = (),
) -> VrpApplication:
    """Load a VRP bundle and assemble its generic-search application callbacks.

    The same extension registry is used for input validation and objective
    evaluation. When ``problem_instance_id`` is omitted, the instance
    manifest's ``instance_id`` is used.

    :param path: Path to the VRP ``instance.toml`` file.
    :param problem_instance_id: Optional identity for the bound problem data.
    :param extension_registry: Registry for custom objective and rule callbacks.
    :param post_horizon_rules: ScenarioBank travel-time extensions.
    :param additive_path_costs: Optional exact path-cost callbacks by objective.
    :param settings: Default Operator catalog configuration.
    :param operator_groups: Soft composition hints over the generated Operators.
    :returns: Immutable application assembly ready for ``MultiobjectiveVNS``.
    :raises VrpInputError: If the instance bundle fails input validation.
    :raises ValueError: If catalog settings, groups, or identities are invalid.
    :raises NotImplementedError: If an active catalog feature has no implemented
        default Operator.
    """
    registry = (
        empty_vrp_extension_registry()
        if extension_registry is None
        else extension_registry
    )
    instance = load_vrp_instance(path, extension_registry=registry)
    return build_vrp_application(
        instance,
        problem_instance_id=problem_instance_id,
        extension_registry=registry,
        post_horizon_rules=post_horizon_rules,
        additive_path_costs=additive_path_costs,
        settings=settings,
        operator_groups=operator_groups,
    )


def build_vrp_application(
    instance: LoadedVrpInstance,
    *,
    problem_instance_id: ProblemInstanceId | None = None,
    extension_registry: VrpExtensionRegistry | None = None,
    post_horizon_rules: Mapping[tuple[str, str], PostHorizonRule] | None = None,
    additive_path_costs: Mapping[ObjectiveId, AdditivePathCostRegistration]
    | None = None,
    settings: VrpOperatorCatalogSettings = VrpOperatorCatalogSettings(),
    operator_groups: tuple[OperatorGroup, ...] = (),
) -> VrpApplication:
    """Assemble callbacks and feature-selected Operators for loaded data.

    Callers that loaded ``instance`` with a custom extension registry pass the
    same registry here. The instance's ``instance_id`` supplies the default
    problem identity; callers can provide a different stable identity for the
    fully bound instance.

    :param instance: Validated immutable instance data.
    :param problem_instance_id: Optional identity for the bound problem data.
    :param extension_registry: Registry used to validate custom declarations.
    :param post_horizon_rules: ScenarioBank travel-time extensions.
    :param additive_path_costs: Optional exact path-cost callbacks by objective.
    :param settings: Default Operator catalog configuration.
    :param operator_groups: Soft composition hints over generated Operator IDs.
    :returns: Application callbacks and registrations for the generic search.
    :raises TypeError: If an application input has an unsupported record type.
    :raises ValueError: If identities or Operator groups are inconsistent.
    :raises NotImplementedError: If an active catalog feature has no implemented
        default Operator.
    """
    if not isinstance(instance, LoadedVrpInstance):
        raise TypeError('instance must be a LoadedVrpInstance')
    if not isinstance(operator_groups, tuple) or any(
        not isinstance(group, OperatorGroup) for group in operator_groups
    ):
        raise TypeError('operator_groups must be a tuple of OperatorGroup values')
    if extension_registry is not None and not isinstance(
        extension_registry, VrpExtensionRegistry
    ):
        raise TypeError('extension_registry must be a VrpExtensionRegistry or None')
    if problem_instance_id is None:
        problem_instance_id = instance.instance_id
    identifier_text(problem_instance_id)

    objectives = build_vrp_objective_declaration(instance)
    effective_post_horizon_rules = (
        None if post_horizon_rules is None else dict(post_horizon_rules)
    )
    evaluator = VrpApplicationEvaluator(
        instance,
        extension_registry=extension_registry,
        post_horizon_rules=effective_post_horizon_rules,
    )
    evaluators_by_instance: dict[
        int, tuple[LoadedVrpInstance, VrpApplicationEvaluator]
    ] = {id(instance): (instance, evaluator)}

    def assess_plan(
        assessment_instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        cached = evaluators_by_instance.get(id(assessment_instance))
        if cached is None or cached[0] is not assessment_instance:
            assessment_evaluator = VrpApplicationEvaluator(
                assessment_instance,
                extension_registry=extension_registry,
                post_horizon_rules=effective_post_horizon_rules,
            )
            evaluators_by_instance[id(assessment_instance)] = (
                assessment_instance,
                assessment_evaluator,
            )
        else:
            assessment_evaluator = cached[1]
        return assessment_evaluator.evaluate(solution, objective_declaration)

    assessor = cast(VrpPlanAssessor, assess_plan)
    catalog = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id=problem_instance_id,
        objective_declaration=objectives,
        assess_plan=assessor,
        additive_path_costs=additive_path_costs,
        settings=settings,
    )
    _validate_operator_groups(operator_groups, catalog)

    return VrpApplication(
        instance=instance,
        problem_instance_id=problem_instance_id,
        solution_codec=VrpSolutionCodec(),
        solution_identity=VrpSolutionIdentity(instance),
        objective_declaration=objectives,
        application_evaluator=evaluator,
        operators=catalog.operators,
        pre_registered_compositions=catalog.pre_registered_compositions,
        operator_groups=operator_groups,
    )


def _validate_operator_groups(
    groups: tuple[OperatorGroup, ...], catalog: VrpOperatorCatalog
) -> None:
    group_ids: set[object] = set()
    group_texts: set[str] = set()
    operator_ids = {registration.operator_id for registration in catalog.operators}
    operator_ids.update(
        registration.operator_id for registration in catalog.pre_registered_compositions
    )
    for group in groups:
        group_text = identifier_text(group.group_id)
        if group.group_id in group_ids or group_text in group_texts:
            raise ValueError('OperatorGroup IDs must be unique')
        group_ids.add(group.group_id)
        group_texts.add(group_text)
        if not group.operator_ids <= operator_ids:
            raise ValueError('OperatorGroup members must be registered Operators')
