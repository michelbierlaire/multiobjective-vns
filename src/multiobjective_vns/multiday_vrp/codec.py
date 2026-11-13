"""Byte encoding and restoration for multi-day VRP solutions.

Created: 2026-11-03T16:06:52+01:00
Author: Michel Bierlaire, EPFL
"""

import json
from typing import cast

from multiobjective_vns.callbacks import CodecPayload
from multiobjective_vns.identifiers import ComponentConfigurationId, ComponentId
from multiobjective_vns.multiday_vrp.solution import (
    ActivityKind,
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
    TransferOperationStop,
    TransferRole,
    TransferStep,
    TransferStepRef,
    VehicleDayRoute,
    VrpSolution,
)


def _require_fields(
    value: object, expected: frozenset[str], name: str
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f'{name} fields do not match the VRP codec schema')
    return value


def _encode_float(value: float) -> str:
    return value.hex()


def _decode_float(value: object, name: str) -> float:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a hexadecimal float string')
    try:
        return float.fromhex(value)
    except ValueError as error:
        raise ValueError(f'{name} is not a hexadecimal float') from error


def _require_text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f'{name} must be a string')
    return value


def _require_integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f'{name} must be an integer')
    return value


def _require_boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f'{name} must be a Boolean')
    return value


def _encode_stop(
    stop: ActivityStop | TransferOperationStop | DepotOnlyStop,
) -> dict[str, object]:
    timing = {
        'pre_service_wait': _encode_float(stop.pre_service_wait),
        'wait': _encode_float(stop.wait),
        'break_duration': _encode_float(stop.break_duration),
        'tour_end': stop.tour_end,
    }
    if isinstance(stop, ActivityStop):
        return {
            'kind': 'activity',
            'activity_ref': {
                'kind': stop.activity_ref.kind,
                'activity_id': stop.activity_ref.activity_id,
            },
            'team_member_ids': sorted(stop.team_member_ids),
            **timing,
        }
    if isinstance(stop, TransferOperationStop):
        return {
            'kind': 'transfer_operation',
            'transfer_step_ref': {
                'package_id': stop.transfer_step_ref.package_id,
                'step_number': stop.transfer_step_ref.step_number,
            },
            'role': stop.role,
            **timing,
        }
    return {'kind': 'depot_only', 'node_id': stop.node_id, **timing}


def _decode_stop(value: object) -> ActivityStop | TransferOperationStop | DepotOnlyStop:
    if not isinstance(value, dict):
        raise TypeError('each encoded route stop must be an object')
    kind = value.get('kind')
    timing_fields = frozenset(
        {'pre_service_wait', 'wait', 'break_duration', 'tour_end'}
    )
    if kind == 'activity':
        record = _require_fields(
            value,
            timing_fields | {'kind', 'activity_ref', 'team_member_ids'},
            'activity stop',
        )
        activity = _require_fields(
            record['activity_ref'],
            frozenset({'kind', 'activity_id'}),
            'activity reference',
        )
        member_ids = record['team_member_ids']
        if not isinstance(member_ids, list):
            raise TypeError('team_member_ids must be a JSON array')
        return ActivityStop(
            activity_ref=ActivityRef(
                kind=cast(ActivityKind, _require_text(activity['kind'], 'kind')),
                activity_id=_require_text(activity['activity_id'], 'activity_id'),
            ),
            team_member_ids=frozenset(member_ids),
            pre_service_wait=_decode_float(
                record['pre_service_wait'], 'pre_service_wait'
            ),
            wait=_decode_float(record['wait'], 'wait'),
            break_duration=_decode_float(record['break_duration'], 'break_duration'),
            tour_end=_require_boolean(record['tour_end'], 'tour_end'),
        )
    if kind == 'transfer_operation':
        record = _require_fields(
            value,
            timing_fields | {'kind', 'transfer_step_ref', 'role'},
            'transfer-operation stop',
        )
        reference = _require_fields(
            record['transfer_step_ref'],
            frozenset({'package_id', 'step_number'}),
            'transfer-step reference',
        )
        return TransferOperationStop(
            transfer_step_ref=TransferStepRef(
                package_id=_require_text(reference['package_id'], 'package_id'),
                step_number=_require_integer(reference['step_number'], 'step_number'),
            ),
            role=cast(TransferRole, _require_text(record['role'], 'role')),
            pre_service_wait=_decode_float(
                record['pre_service_wait'], 'pre_service_wait'
            ),
            wait=_decode_float(record['wait'], 'wait'),
            break_duration=_decode_float(record['break_duration'], 'break_duration'),
            tour_end=_require_boolean(record['tour_end'], 'tour_end'),
        )
    if kind == 'depot_only':
        record = _require_fields(
            value, timing_fields | {'kind', 'node_id'}, 'depot-only stop'
        )
        return DepotOnlyStop(
            node_id=_require_text(record['node_id'], 'node_id'),
            pre_service_wait=_decode_float(
                record['pre_service_wait'], 'pre_service_wait'
            ),
            wait=_decode_float(record['wait'], 'wait'),
            break_duration=_decode_float(record['break_duration'], 'break_duration'),
            tour_end=_require_boolean(record['tour_end'], 'tour_end'),
        )
    raise ValueError(f'unsupported VRP route-stop kind: {kind!r}')


def _encode_solution(solution: VrpSolution) -> dict[str, object]:
    routes = []
    for route in solution.routes:
        routes.append(
            {
                'vehicle_id': route.vehicle_id,
                'day_index': route.day_index,
                'overnight_depot_id': route.overnight_depot_id,
                'start_time': _encode_float(route.start_time),
                'driver_id': route.driver_id,
                'stops': [_encode_stop(stop) for stop in route.stops],
                'legs': [list(leg.arc_ids) for leg in route.legs],
            }
        )
    return {
        'format': 'multiobjective-vns-multiday-vrp-solution',
        'version': 1,
        'routes': routes,
        'transfer_steps': [
            {
                'package_id': step.package_id,
                'step_number': step.step_number,
                'facility_id': step.facility_id,
            }
            for step in solution.transfer_steps
        ],
    }


def _decode_solution(document: object) -> VrpSolution:
    root = _require_fields(
        document,
        frozenset({'format', 'version', 'routes', 'transfer_steps'}),
        'VRP solution',
    )
    if root['format'] != 'multiobjective-vns-multiday-vrp-solution':
        raise ValueError('unsupported VRP solution codec format')
    if type(root['version']) is not int or root['version'] != 1:
        raise ValueError('unsupported VRP solution codec version')
    encoded_routes = root['routes']
    encoded_transfers = root['transfer_steps']
    if not isinstance(encoded_routes, list) or not isinstance(encoded_transfers, list):
        raise TypeError('routes and transfer_steps must be JSON arrays')
    routes: list[VehicleDayRoute] = []
    for encoded_route in encoded_routes:
        route = _require_fields(
            encoded_route,
            frozenset(
                {
                    'vehicle_id',
                    'day_index',
                    'overnight_depot_id',
                    'start_time',
                    'driver_id',
                    'stops',
                    'legs',
                }
            ),
            'vehicle-day route',
        )
        stops = route['stops']
        legs = route['legs']
        if not isinstance(stops, list) or not isinstance(legs, list):
            raise TypeError('route stops and legs must be JSON arrays')
        if any(not isinstance(leg, list) for leg in legs):
            raise TypeError('each route leg must be a JSON array')
        routes.append(
            VehicleDayRoute(
                vehicle_id=_require_text(route['vehicle_id'], 'vehicle_id'),
                day_index=_require_integer(route['day_index'], 'day_index'),
                overnight_depot_id=_require_text(
                    route['overnight_depot_id'], 'overnight_depot_id'
                ),
                start_time=_decode_float(route['start_time'], 'start_time'),
                driver_id=_require_text(route['driver_id'], 'driver_id'),
                stops=tuple(_decode_stop(stop) for stop in stops),
                legs=tuple(RouteLeg(tuple(leg)) for leg in legs),
            )
        )
    transfers: list[TransferStep] = []
    for encoded_transfer in encoded_transfers:
        transfer = _require_fields(
            encoded_transfer,
            frozenset({'package_id', 'step_number', 'facility_id'}),
            'transfer step',
        )
        transfers.append(
            TransferStep(
                package_id=_require_text(transfer['package_id'], 'package_id'),
                step_number=_require_integer(transfer['step_number'], 'step_number'),
                facility_id=_require_text(transfer['facility_id'], 'facility_id'),
            )
        )
    return VrpSolution(tuple(routes), tuple(transfers))


class VrpSolutionCodec:
    """Encode the immutable VRP solution record tree as versioned JSON bytes."""

    __slots__ = ()

    component_id: ComponentId = 'multiobjective-vns:multiday-vrp:solution-codec'
    configuration_id: ComponentConfigurationId = 'multiday-vrp-solution-json-v1'

    def capture(self, solution: object) -> CodecPayload:
        """Encode a type-correct VRP solution as UTF-8 JSON bytes.

        :param solution: Application value resolved as a ``VrpSolution``.
        :returns: Compact UTF-8 JSON bytes in the version-1 solution format.
        :raises TypeError: If ``solution`` is not exactly a ``VrpSolution``.
        """
        if type(solution) is not VrpSolution:
            raise TypeError('solution must be exactly a VrpSolution')
        return json.dumps(
            _encode_solution(solution),
            ensure_ascii=True,
            allow_nan=False,
            separators=(',', ':'),
            sort_keys=True,
        ).encode('utf-8')

    def restore(self, payload: object) -> VrpSolution:
        """Decode a version-1 VRP solution payload.

        :param payload: Bytes previously returned by :meth:`capture`.
        :returns: A reconstructed immutable ``VrpSolution``.
        :raises TypeError: If ``payload`` is not bytes or has malformed field
            types.
        :raises ValueError: If the JSON format, version, or content is invalid.
        """
        if type(payload) is not bytes:
            raise TypeError('VRP codec payloads must be bytes')
        try:
            document = json.loads(payload.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError('VRP codec payload is not valid UTF-8 JSON') from error
        return _decode_solution(document)
