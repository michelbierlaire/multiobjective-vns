"""Plot layouts, markers, and rank-based legends for the Pareto viewer.

Created: 2026-11-12T12:44:55+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Mapping
from tkinter import ttk

from multiobjective_vns.pareto_viewer.data import (
    ObjectiveValueView,
    SolutionView,
    ViewExport,
)

_MIN_AREA = 25.0
_MAX_AREA = 225.0
_PLOT_MARGIN = (68.0, 28.0, 28.0, 56.0)


def axis_tick_positions() -> tuple[float, ...]:
    """Return 101 normalized positions for a 100-interval display grid."""
    return tuple(index / 100 for index in range(101))


def axis_tick_labels(
    export: ViewExport, objective_index: int
) -> tuple[tuple[float, str], ...]:
    """Place formatted values at major ticks nearest to their rank positions.

    :param export: Validated viewer data for one invocation and scope.
    :param objective_index: Index of the selected objective.
    :returns: Major tick positions and formatted objective labels. Each
        observed rank value appears at most once; endpoint ticks show the
        lowest and highest observed values.
    :raises TypeError: If ``objective_index`` is not an integer.
    :raises IndexError: If ``objective_index`` is outside the objective list.
    """
    levels = _objective_rank_levels(export, objective_index)
    if not levels:
        return ()
    if len(levels) == 1:
        return (levels[0],)

    ticks = tuple(tick_index / 10 for tick_index in range(11))
    assigned: dict[float, tuple[float, str]] = {}
    for level in levels:
        tick = min(ticks, key=lambda position: (abs(position - level[0]), position))
        current = assigned.get(tick)
        if current is None or (abs(level[0] - tick), level[0]) < (
            abs(current[0] - tick),
            current[0],
        ):
            assigned[tick] = level

    assigned[0.0] = levels[0]
    assigned[1.0] = levels[-1]
    return tuple((tick, assigned[tick][1]) for tick in ticks if tick in assigned)


def marker_area(normalized_position: float) -> float:
    """Map a normalized third-objective position to positive marker area."""
    if not isinstance(normalized_position, (float, int)) or isinstance(
        normalized_position, bool
    ):
        raise TypeError('normalized_position must be a real number')
    position = float(normalized_position)
    if not math.isfinite(position) or not 0.0 <= position <= 1.0:
        raise ValueError('normalized_position must be finite and in [0, 1]')
    return _MIN_AREA + position * (_MAX_AREA - _MIN_AREA)


def marker_category(solution: SolutionView) -> str:
    """Return marker category using the viewer design's status precedence."""
    if solution.archive_status == 'pareto_optimal':
        return 'pareto'
    if solution.archive_status == 'rejected':
        return 'rejected'
    if solution.archive_status == 'objective_vector_duplicate':
        return 'objective_duplicate'
    if solution.processing_status == 'failed':
        return 'failed'
    if solution.assessment == 'invalid':
        return 'invalid'
    if solution.assessment == 'infeasible':
        return 'infeasible'
    return 'other'


def size_legend_samples(
    export: ViewExport, objective_index: int
) -> tuple[tuple[float, str], ...]:
    """Return formatted examples for up to five distinct size levels.

    Each displayed value is the observed objective value whose normalized
    position is closest to a quarter-scale marker. Ties prefer lower ranks.
    """
    observations = _objective_rank_levels(export, objective_index)
    levels = (0.0, 0.25, 0.5, 0.75, 1.0)
    if not observations:
        return ((0.5, 'unavailable'),)
    if len(observations) <= len(levels):
        return observations
    positions = tuple(position for position, _value in observations)
    selected_positions = {
        min(positions, key=lambda position: (abs(position - level), position))
        for level in levels
    }
    return tuple(level for level in observations if level[0] in selected_positions)


def _objective_rank_levels(
    export: ViewExport, objective_index: int
) -> tuple[tuple[float, str], ...]:
    if isinstance(objective_index, bool) or not isinstance(objective_index, int):
        raise TypeError('objective_index must be an integer')
    if not 0 <= objective_index < len(export.objectives):
        raise IndexError('objective_index is outside the objective list')
    distinct: dict[float, str] = {}
    for solution in export.solutions:
        objective_value: ObjectiveValueView = solution.objective_values[objective_index]
        position = objective_value.normalized_position
        display_value = objective_value.display_value
        if position is not None and display_value is not None:
            distinct.setdefault(position, display_value)
    return tuple(sorted(distinct.items()))


def status_legend_labels() -> tuple[tuple[str, str], ...]:
    """Return marker categories and their user-facing legend labels."""
    return (
        ('pareto', 'Pareto'),
        ('rejected', 'Previously Pareto'),
        ('objective_duplicate', 'Objective tie'),
        ('other', 'Other valid'),
        ('infeasible', 'Infeasible'),
        ('invalid', 'Invalid'),
        ('failed', 'Failed'),
    )


def nearest_point(
    points: Mapping[str, tuple[float, float]],
    x: float,
    y: float,
    *,
    tolerance: float,
) -> str | None:
    """Return the closest point ID when its screen distance is within tolerance."""
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError('point coordinates must be finite')
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('tolerance must be finite and nonnegative')
    if not points:
        return None
    solution_id, distance = min(
        (
            (solution_id, math.hypot(x - point_x, y - point_y))
            for solution_id, (point_x, point_y) in points.items()
        ),
        key=lambda item: item[1],
    )
    return solution_id if distance <= tolerance else None


def draw_objective_plot(
    canvas: tk.Canvas,
    export: ViewExport | None,
    objective_ids: tuple[str, ...],
    selected_solution_id: str | None,
) -> dict[str, tuple[float, float]]:
    """Render the current scatter plot and return coordinates by solution ID."""
    canvas.delete('all')
    if (
        export is None
        or len(objective_ids) != 3
        or any(not item for item in objective_ids)
    ):
        canvas.create_text(
            max(20, canvas.winfo_width() / 2),
            max(20, canvas.winfo_height() / 2),
            text='Select three objectives with usable values to plot the archive.',
            fill='#222222',
            width=max(200, canvas.winfo_width() - 80),
        )
        return {}

    objective_indices = {
        objective.objective_id: index
        for index, objective in enumerate(export.objectives)
    }
    try:
        x_index, y_index, size_index = (
            objective_indices[objective_id] for objective_id in objective_ids
        )
    except KeyError:
        return {}

    width = max(320.0, float(canvas.winfo_width()))
    height = max(260.0, float(canvas.winfo_height()))
    left, top, right, bottom = _PLOT_MARGIN
    plot_left, plot_right = left, width - right
    plot_top, plot_bottom = top, height - bottom
    plot_width, plot_height = plot_right - plot_left, plot_bottom - plot_top
    x_tick_labels = dict(axis_tick_labels(export, x_index))
    y_tick_labels = dict(axis_tick_labels(export, y_index))
    for tick in axis_tick_positions():
        x = plot_left + tick * plot_width
        y = plot_bottom - tick * plot_height
        color = '#d7d7d7' if round(tick * 100) % 10 == 0 else '#eeeeee'
        canvas.create_line(x, plot_top, x, plot_bottom, fill=color)
        canvas.create_line(plot_left, y, plot_right, y, fill=color)
        if round(tick * 100) % 10 == 0:
            major_position = round(tick * 10) / 10
            x_label = x_tick_labels.get(major_position)
            y_label = y_tick_labels.get(major_position)
            if x_label is not None:
                canvas.create_text(
                    x,
                    plot_bottom + 15,
                    text=x_label,
                    fill='#222222',
                    font=('TkDefaultFont', 8),
                )
            if y_label is not None:
                canvas.create_text(
                    plot_left - 8,
                    y,
                    text=y_label,
                    fill='#222222',
                    font=('TkDefaultFont', 8),
                    anchor='e',
                )
    canvas.create_rectangle(
        plot_left,
        plot_top,
        plot_right,
        plot_bottom,
        outline='#222222',
    )
    canvas.create_text(
        (plot_left + plot_right) / 2,
        height - 16,
        text=export.objectives[x_index].label,
        fill='#111111',
    )
    canvas.create_text(
        18,
        (plot_top + plot_bottom) / 2,
        text=export.objectives[y_index].label,
        fill='#111111',
        angle=90,
    )
    canvas.create_text(
        plot_right,
        5,
        text='Point spacing follows objective rank',
        fill='#555555',
        font=('TkDefaultFont', 8),
        anchor='ne',
    )

    centers: dict[str, tuple[float, float]] = {}
    for solution in export.solutions:
        x_position = solution.objective_values[x_index].normalized_position
        y_position = solution.objective_values[y_index].normalized_position
        size_position = solution.objective_values[size_index].normalized_position
        if x_position is None or y_position is None or size_position is None:
            continue
        x = plot_left + x_position * plot_width
        y = plot_bottom - y_position * plot_height
        radius = math.sqrt(marker_area(size_position) / math.pi)
        _draw_marker(canvas, solution, x, y, radius, selected_solution_id)
        centers[solution.solution_id] = (x, y)

    if not centers:
        canvas.create_text(
            (plot_left + plot_right) / 2,
            (plot_top + plot_bottom) / 2,
            text='No records have usable values for all three objectives.',
            fill='#222222',
        )
    _draw_overlap_counts(canvas, centers)
    return centers


def draw_legends(
    status_label: ttk.Label,
    size_canvas: tk.Canvas,
    export: ViewExport | None,
    size_objective_id: str,
) -> None:
    """Render the status key and the selected objective's size scale."""
    status_label.configure(
        text=(
            'Status: ● Pareto  |  × Previously Pareto  |  ◇ Objective tie  '
            '|  ○ Other valid  '
            '|  △ Infeasible\n'
            'Broken outline: Invalid  |  Broken outline + !: Failed'
        )
    )
    size_canvas.delete('all')
    if export is None or not size_objective_id:
        return
    objective_index = next(
        (
            index
            for index, objective in enumerate(export.objectives)
            if objective.objective_id == size_objective_id
        ),
        None,
    )
    if objective_index is None:
        return
    width = max(340, size_canvas.winfo_width())
    samples = size_legend_samples(export, objective_index)
    label = export.objectives[objective_index].label
    size_canvas.create_text(
        4,
        2,
        text=f'Marker area: {label}',
        anchor='nw',
        fill='#111111',
    )
    spacing = max(60.0, (width - 20.0) / len(samples))
    for index, (position, display_value) in enumerate(samples):
        center_x = 12.0 + spacing * (index + 0.5)
        center_y = 30.0
        radius = math.sqrt(marker_area(position) / math.pi)
        size_canvas.create_oval(
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
            fill='white',
            outline='#222222',
        )
        size_canvas.create_text(
            center_x,
            51,
            text=display_value,
            fill='#111111',
            font=('TkDefaultFont', 8),
        )


def _draw_overlap_counts(
    canvas: tk.Canvas, centers: Mapping[str, tuple[float, float]]
) -> None:
    grouped: dict[tuple[int, int], int] = {}
    for x, y in centers.values():
        key = (round(x), round(y))
        grouped[key] = grouped.get(key, 0) + 1
    for (x, y), count in grouped.items():
        if count > 1:
            canvas.create_text(
                x + 8,
                y - 8,
                text=f'×{count}',
                fill='#111111',
                anchor='sw',
                font=('TkDefaultFont', 8, 'bold'),
            )


def _draw_marker(
    canvas: tk.Canvas,
    solution: SolutionView,
    x: float,
    y: float,
    radius: float,
    selected_solution_id: str | None,
) -> None:
    category = marker_category(solution)
    tag = f'point:{solution.solution_id}'
    if category == 'pareto':
        canvas.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            fill='#c62828',
            outline='#111111',
            width=2,
            tags=(tag,),
        )
    elif category == 'rejected':
        arm = max(4.0, radius)
        canvas.create_line(
            x - arm,
            y - arm,
            x + arm,
            y + arm,
            fill='#111111',
            width=2,
            tags=(tag,),
        )
        canvas.create_line(
            x - arm,
            y + arm,
            x + arm,
            y - arm,
            fill='#111111',
            width=2,
            tags=(tag,),
        )
    elif category == 'objective_duplicate':
        canvas.create_polygon(
            x,
            y - radius,
            x + radius,
            y,
            x,
            y + radius,
            x - radius,
            y,
            fill='#fff3cd',
            outline='#7a5a00',
            width=2,
            tags=(tag,),
        )
    elif category == 'infeasible':
        canvas.create_polygon(
            x,
            y - radius,
            x + radius,
            y + radius,
            x - radius,
            y + radius,
            fill='white',
            outline='#111111',
            width=2,
            tags=(tag,),
        )
        canvas.create_oval(
            x - 2,
            y - 2,
            x + 2,
            y + 2,
            fill='#111111',
            outline='#111111',
            tags=(tag,),
        )
    elif category in ('invalid', 'failed'):
        canvas.create_polygon(
            x,
            y - radius,
            x + radius,
            y - radius / 2,
            x + radius,
            y + radius / 2,
            x,
            y + radius,
            x - radius,
            y + radius / 2,
            x - radius,
            y - radius / 2,
            fill='white',
            outline='#111111',
            width=2,
            dash=(3, 2),
            tags=(tag,),
        )
        if category == 'failed':
            canvas.create_text(
                x,
                y,
                text='!',
                fill='#111111',
                font=('TkDefaultFont', max(7, int(radius * 1.5)), 'bold'),
                tags=(tag,),
            )
    else:
        canvas.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            fill='white',
            outline='#222222',
            width=1,
            tags=(tag,),
        )
    if selected_solution_id == solution.solution_id:
        canvas.create_oval(
            x - radius - 3,
            y - radius - 3,
            x + radius + 3,
            y + radius + 3,
            outline='#111111',
            width=1,
            dash=(2, 2),
            tags=(tag,),
        )


__all__ = [
    'axis_tick_labels',
    'axis_tick_positions',
    'draw_legends',
    'draw_objective_plot',
    'marker_area',
    'marker_category',
    'nearest_point',
    'size_legend_samples',
    'status_legend_labels',
]
