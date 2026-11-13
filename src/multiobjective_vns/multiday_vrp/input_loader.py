"""Read and validate a normalized multi-day VRP input bundle.

Created: 2026-11-03T16:40:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from multiobjective_vns.multiday_vrp.extensions import (
    VrpExtensionRegistry,
    empty_vrp_extension_registry,
)
from multiobjective_vns.multiday_vrp.input_errors import VrpInputError
from multiobjective_vns.multiday_vrp.input_features import _derive_features
from multiobjective_vns.multiday_vrp.input_objectives import (
    _active_penalty_categories,
    _check_hos_selection,
    _parse_objectives,
    _validate_custom_parameters,
    _validate_hos_configuration,
    _validate_objective_rule_bindings,
    _validate_objectives,
)
from multiobjective_vns.multiday_vrp.input_records import (
    LoadedScenarioBank,
    LoadedVrpInstance,
    PackageTimeWindowRecord,
    PickupSymmetryGroup,
    VrpInstanceSettings,
    VrpObjectiveData,
    VrpPackageData,
)
from multiobjective_vns.multiday_vrp.input_rules import (
    _parse_settings_and_event_objectives,
)
from multiobjective_vns.multiday_vrp.input_schema import (
    ALLOWED_FILE_KEYS,
    BASE_FILE_KEYS,
    FILE_COLUMNS,
    MANIFEST_OPTIONAL,
    MANIFEST_REQUIRED,
    SIMPLE_RULES,
    UNIT_SECONDS,
)
from multiobjective_vns.multiday_vrp.input_spatial import _parse_spatial_data
from multiobjective_vns.multiday_vrp.input_tables import _parse_tables
from multiobjective_vns.multiday_vrp.input_utils import (
    read_toml_table,
)
from multiobjective_vns.multiday_vrp.input_validation import (
    _file_path,
    _filename,
    _id_value,
    _load_optional_toml,
    _missing_file,
    _read_table,
    _relative_path,
    _require_equal_arc_catalog,
    _toml_integer,
    _toml_string,
    _validate_objective_omissions,
    _validate_setting_relations,
)
from multiobjective_vns.multiday_vrp.scenario_bank import load_scenario_bank

type _Table = dict[str, object]

__all__ = ['load_vrp_instance']


def load_vrp_instance(
    path: str | Path,
    *,
    extension_registry: VrpExtensionRegistry | None = None,
) -> LoadedVrpInstance:
    """Load and validate one VRP bundle from its ``instance.toml`` file.

    :param path: Instance manifest path.
    :param extension_registry: Registry binding custom measures and rules.
    :returns: Immutable normalized instance data.
    :raises VrpInputError: If input data, references, or declarations are invalid.
    :raises VrpExtensionConfigurationError: If a custom validator rejects data.
    """
    manifest_path = Path(path)
    manifest = read_toml_table(manifest_path)
    _validate_manifest_shape(manifest, manifest_path.name)
    registry = extension_registry or empty_vrp_extension_registry()
    version = _toml_integer(manifest, 'format_version', manifest_path.name)
    if version != 1:
        raise VrpInputError(
            file=manifest_path.name,
            column='format_version',
            reason='supported format_version is 1',
        )
    instance_id = _toml_string(manifest, 'instance_id', manifest_path.name)
    _id_value(instance_id, manifest_path.name, 0, 'instance_id')
    scenario_file = _toml_string(manifest, 'scenario_bank_file', manifest_path.name)
    scenario_path = _relative_path(
        manifest_path.parent, scenario_file, manifest_path.name, 'scenario_bank_file'
    )
    scenario_bank = load_scenario_bank(scenario_path)
    files_value = manifest['files']
    if type(files_value) is not dict:
        raise VrpInputError(
            file=manifest_path.name, column='files', reason='must be a table'
        )
    files = cast(dict[str, object], files_value)
    _validate_file_map(files, manifest_path.name)
    objectives_path = _file_path(
        manifest_path.parent, files, 'objectives', manifest_path.name
    )
    if objectives_path is None:
        raise _missing_file('objectives')
    objective_data, objective_locations = _parse_objectives(objectives_path)
    settings = _parse_settings(manifest, manifest_path.name, scenario_bank)
    for simple_rule, table_key in (
        ('daily_work', 'daily_work_limits'),
        ('daily_driving', 'daily_driving_limits'),
        ('continuous_driving', 'continuous_driving_limits'),
    ):
        if simple_rule in settings.active_simple_rules and table_key not in files:
            raise _missing_file(table_key)
    if (
        settings.active_simple_rules
        & {'minimum_rest', 'daily_work', 'continuous_driving'}
        and 'person_rule_limits' not in files
    ):
        raise _missing_file('person_rule_limits')

    paths = {
        key: _file_path(
            manifest_path.parent, files, key, manifest_path.name, required=False
        )
        for key in FILE_COLUMNS
    }
    for key in BASE_FILE_KEYS - {'objectives'}:
        if paths[key] is None:
            raise VrpInputError(
                file=manifest_path.name,
                column=f'files.{key}',
                reason='required file is missing',
            )
    tables = {
        key: _read_table(path_value, FILE_COLUMNS[key])
        for key, path_value in paths.items()
        if path_value is not None
    }
    for key in FILE_COLUMNS:
        tables.setdefault(key, ())

    planning_dates = scenario_bank.planning_dates
    time_unit = scenario_bank.time_unit
    duration_scale = UNIT_SECONDS[time_unit]
    horizon = scenario_bank.day_boundaries[-1][-1]
    origin = datetime.combine(
        planning_dates[0], time.min, tzinfo=ZoneInfo(scenario_bank.time_zone)
    )

    (
        network_data,
        fleet_data,
        package_data,
        intervention_data,
        personnel_data,
        relations,
        work_rules,
        event_data,
        omission_penalties,
    ) = _parse_tables(
        tables=tables,
        paths=paths,
        settings=settings,
        scenario_bank=scenario_bank,
        origin=origin,
        duration_scale=duration_scale,
        horizon=horizon,
    )
    spatial_data = _parse_spatial_data(
        manifest,
        tables['node_coordinates'],
        paths['node_coordinates'],
        network_data,
        package_data,
        intervention_data,
    )
    pickup_symmetry_groups = _parse_pickup_symmetry_groups(
        manifest.get('pickup_symmetry_groups'), package_data, manifest_path.name
    )
    if (
        settings.exact_active_vehicle_count is not None
        and settings.exact_active_vehicle_count > len(fleet_data.vehicles)
    ):
        raise VrpInputError(
            file=manifest_path.name,
            column='exact_active_vehicle_count',
            reason='must not exceed the number of vehicles',
        )
    event_data = _parse_settings_and_event_objectives(
        tables, paths, objective_data, event_data
    )
    _require_equal_arc_catalog(
        network_data.arcs, scenario_bank.arc_ids, _filename(paths['arcs'])
    )
    _validate_setting_relations(
        paths,
        network_data,
        fleet_data,
        package_data,
        intervention_data,
        personnel_data,
        relations,
        work_rules,
        settings,
        scenario_bank,
    )
    objective_data = VrpObjectiveData(
        objectives=objective_data.objectives,
        penalty_functions=objective_data.penalty_functions,
        objective_omission_penalties=omission_penalties,
    )
    _validate_objective_omissions(
        objective_data,
        settings,
        package_data,
        intervention_data,
        _filename(paths['objective_omission_penalties']),
    )
    application_data = _load_optional_toml(
        manifest_path.parent, manifest, 'application_data_file', manifest_path.name
    )
    hos_configuration = _load_optional_toml(
        manifest_path.parent, manifest, 'hos_configuration_file', manifest_path.name
    )
    _check_hos_selection(settings, hos_configuration, registry, manifest_path.name)
    active_categories = _active_penalty_categories(
        settings, package_data, intervention_data, network_data, personnel_data
    )
    time_window_violation_objective_id = _validate_objectives(
        objective_data,
        active_categories,
        event_data,
        registry,
        objectives_path.name,
    )
    feature_profile = _derive_features(
        scenario_bank,
        network_data,
        fleet_data,
        package_data,
        intervention_data,
        personnel_data,
        relations,
        event_data,
        objective_data,
        settings,
        spatial_data,
    )
    instance = LoadedVrpInstance(
        format_version=1,
        instance_id=instance_id,
        scenario_bank=scenario_bank,
        settings=settings,
        network_data=network_data,
        spatial_data=spatial_data,
        fleet_data=fleet_data,
        package_data=package_data,
        pickup_symmetry_groups=pickup_symmetry_groups,
        intervention_data=intervention_data,
        personnel_data=personnel_data,
        activity_relations=relations,
        work_rule_data=work_rules,
        event_data=event_data,
        objective_data=objective_data,
        time_window_violation_objective_id=time_window_violation_objective_id,
        application_data=application_data,
        hos_configuration=hos_configuration,
        hos_rule_definitions=(),
        feature_profile=feature_profile,
    )
    instance = _validate_hos_configuration(
        instance, registry, manifest.get('hos_configuration_file')
    )
    _validate_objective_rule_bindings(instance, objectives_path.name)
    _validate_custom_parameters(
        instance,
        registry,
        objective_locations,
    )
    return instance


def _validate_manifest_shape(manifest: _Table, filename: str) -> None:
    missing = MANIFEST_REQUIRED - manifest.keys()
    extra = manifest.keys() - (MANIFEST_REQUIRED | MANIFEST_OPTIONAL)
    if missing:
        key = min(missing)
        raise VrpInputError(file=filename, column=key, reason='required key is missing')
    if extra:
        key = min(extra)
        raise VrpInputError(file=filename, column=key, reason='unknown key')


def _parse_pickup_symmetry_groups(
    value: object,
    package_data: VrpPackageData,
    filename: str,
) -> tuple[PickupSymmetryGroup, ...]:
    if value is None:
        return ()
    if type(value) is not list:
        raise VrpInputError(
            file=filename,
            column='pickup_symmetry_groups',
            reason='pickup symmetry groups must be an array of tables',
        )

    packages = {package.package_id: package for package in package_data.packages}
    windows_by_package: dict[str, list[PackageTimeWindowRecord]] = {}
    for window in package_data.package_time_windows:
        if window.activity == 'pickup':
            windows_by_package.setdefault(window.package_id, []).append(window)

    groups: list[PickupSymmetryGroup] = []
    group_ids: set[str] = set()
    member_ids: set[str] = set()
    for index, raw_group in enumerate(value, start=1):
        column = f'pickup_symmetry_groups[{index}]'
        if type(raw_group) is not dict:
            raise VrpInputError(
                file=filename,
                column=column,
                reason='pickup symmetry group must be a table',
            )
        group = cast(dict[str, object], raw_group)
        if set(group) != {'group_id', 'package_ids'}:
            raise VrpInputError(
                file=filename,
                column=column,
                reason='pickup symmetry group must contain group_id and package_ids',
            )
        group_id_value = group['group_id']
        if type(group_id_value) is not str:
            raise VrpInputError(
                file=filename,
                column=f'{column}.group_id',
                reason='pickup symmetry group ID must be a string',
            )
        group_id = _id_value(group_id_value, filename, index, f'{column}.group_id')
        if group_id in group_ids:
            raise VrpInputError(
                file=filename,
                column=f'{column}.group_id',
                reason=f'duplicate pickup symmetry group ID {group_id!r}',
            )
        package_ids_value = group['package_ids']
        if type(package_ids_value) is not list:
            raise VrpInputError(
                file=filename,
                column=f'{column}.package_ids',
                reason='pickup symmetry package_ids must be an array',
            )
        package_ids: list[str] = []
        for package_index, package_id_value in enumerate(package_ids_value, start=1):
            if type(package_id_value) is not str:
                raise VrpInputError(
                    file=filename,
                    column=f'{column}.package_ids[{package_index}]',
                    reason='package ID must be a string',
                )
            package_ids.append(
                _id_value(
                    package_id_value,
                    filename,
                    index,
                    f'{column}.package_ids[{package_index}]',
                )
            )
        if len(package_ids) < 2:
            raise VrpInputError(
                file=filename,
                column=f'{column}.package_ids',
                reason='pickup symmetry group needs at least two packages',
            )
        if len(package_ids) != len(set(package_ids)):
            raise VrpInputError(
                file=filename,
                column=f'{column}.package_ids',
                reason='pickup symmetry group package IDs must be distinct',
            )
        unknown_ids = tuple(
            package_id for package_id in package_ids if package_id not in packages
        )
        if unknown_ids:
            raise VrpInputError(
                file=filename,
                column=f'{column}.package_ids',
                reason=f'pickup symmetry group has unknown package {unknown_ids[0]!r}',
            )
        overlap = member_ids.intersection(package_ids)
        if overlap:
            raise VrpInputError(
                file=filename,
                column=f'{column}.package_ids',
                reason=(
                    f'package {min(overlap)!r} belongs to multiple pickup symmetry groups'
                ),
            )

        reference = packages[package_ids[0]]
        reference_windows = _pickup_window_signature(package_ids[0], windows_by_package)
        for package_id in package_ids[1:]:
            package = packages[package_id]
            if package.pickup_node_id != reference.pickup_node_id:
                raise VrpInputError(
                    file=filename,
                    column=f'{column}.package_ids',
                    reason='pickup symmetry group members must have the same pickup node',
                )
            if package.pickup_service_duration != reference.pickup_service_duration:
                raise VrpInputError(
                    file=filename,
                    column=f'{column}.package_ids',
                    reason=(
                        'pickup symmetry group members must have the same '
                        'pickup service duration'
                    ),
                )
            if (
                _pickup_window_signature(package_id, windows_by_package)
                != reference_windows
            ):
                raise VrpInputError(
                    file=filename,
                    column=f'{column}.package_ids',
                    reason=(
                        'pickup symmetry group members must have the same '
                        'pickup-window alternatives'
                    ),
                )

        groups.append(PickupSymmetryGroup(group_id, tuple(package_ids)))
        group_ids.add(group_id)
        member_ids.update(package_ids)
    return tuple(groups)


def _pickup_window_signature(
    package_id: str,
    windows_by_package: dict[str, list[PackageTimeWindowRecord]],
) -> tuple[tuple[int, float, float], ...]:
    windows = windows_by_package.get(package_id, [])
    return tuple(
        (window.window_index, window.start_time, window.end_time)
        for window in sorted(windows, key=lambda item: item.window_index)
    )


def _validate_file_map(files: dict[str, object], filename: str) -> None:
    extra = files.keys() - ALLOWED_FILE_KEYS
    if extra:
        key = min(extra)
        raise VrpInputError(
            file=filename, column=f'files.{key}', reason='unknown file key'
        )
    missing = BASE_FILE_KEYS - files.keys()
    if missing:
        key = min(missing)
        raise VrpInputError(
            file=filename,
            column=f'files.{key}',
            reason='required file reference is missing',
        )
    for key, value in files.items():
        if type(value) is not str or not value:
            raise VrpInputError(
                file=filename,
                column=f'files.{key}',
                reason='must be a nonempty relative path',
            )


def _parse_settings(
    manifest: _Table, filename: str, scenario_bank: LoadedScenarioBank
) -> VrpInstanceSettings:
    load_unit = _toml_string(manifest, 'load_unit', filename)
    if not load_unit or load_unit.strip() != load_unit:
        raise VrpInputError(
            file=filename, column='load_unit', reason='must be nonempty text'
        )
    service_coverage_mode = _toml_string(manifest, 'service_coverage_mode', filename)
    if service_coverage_mode not in {'mandatory', 'penalized_omission'}:
        raise VrpInputError(
            file=filename,
            column='service_coverage_mode',
            reason="must be 'mandatory' or 'penalized_omission'",
        )
    staff_depot_mode = _toml_string(manifest, 'staff_depot_mode', filename)
    if staff_depot_mode not in {'available_at_assignment', 'depot_continuity'}:
        raise VrpInputError(
            file=filename,
            column='staff_depot_mode',
            reason="must be 'available_at_assignment' or 'depot_continuity'",
        )
    raw_exact_vehicle_count = manifest.get('exact_active_vehicle_count')
    if raw_exact_vehicle_count is not None and type(raw_exact_vehicle_count) is not int:
        raise VrpInputError(
            file=filename,
            column='exact_active_vehicle_count',
            reason='must be an integer',
        )
    if raw_exact_vehicle_count is None:
        exact_active_vehicle_count = None
    else:
        exact_active_vehicle_count = raw_exact_vehicle_count
        if exact_active_vehicle_count < 0:
            raise VrpInputError(
                file=filename,
                column='exact_active_vehicle_count',
                reason='must be nonnegative',
            )
    rule_treatment = _toml_string(manifest, 'rule_treatment', filename)
    if rule_treatment not in {'penalty', 'compliance'}:
        raise VrpInputError(
            file=filename,
            column='rule_treatment',
            reason="must be 'penalty' or 'compliance'",
        )
    raw_rules = manifest['active_simple_rules']
    if type(raw_rules) is not list or any(type(item) is not str for item in raw_rules):
        raise VrpInputError(
            file=filename,
            column='active_simple_rules',
            reason='must be an array of strings',
        )
    rules = cast(list[str], raw_rules)
    if len(set(rules)) != len(rules) or any(item not in SIMPLE_RULES for item in rules):
        raise VrpInputError(
            file=filename,
            column='active_simple_rules',
            reason='must contain unique supported rule IDs',
        )
    hos_regime_id = _toml_string(manifest, 'hos_regime_id', filename)
    hos_regime_version = _toml_string(manifest, 'hos_regime_version', filename)
    _id_value(hos_regime_id, filename, 0, 'hos_regime_id')
    if hos_regime_id == 'none' and hos_regime_version != 'none':
        raise VrpInputError(
            file=filename,
            column='hos_regime_version',
            reason="must be 'none' when hos_regime_id is 'none'",
        )
    if hos_regime_id != 'none' and not hos_regime_version:
        raise VrpInputError(
            file=filename,
            column='hos_regime_version',
            reason='must be nonempty for an active HOS regime',
        )
    if hos_regime_id != 'none':
        _id_value(hos_regime_version, filename, 0, 'hos_regime_version')
    return VrpInstanceSettings(
        load_unit=load_unit,
        service_coverage_mode=service_coverage_mode,
        staff_depot_mode=staff_depot_mode,
        exact_active_vehicle_count=exact_active_vehicle_count,
        rule_treatment=rule_treatment,
        active_simple_rules=frozenset(rules),
        hos_regime_id=hos_regime_id,
        hos_regime_version=hos_regime_version,
    )
