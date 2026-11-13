"""Tests for Pareto-view rendering helpers.

Created: 2026-11-12T12:44:55+00:00
Author: Michel Bierlaire, EPFL
"""

import math
import runpy
import tkinter as tk
from tkinter import ttk
from typing import Literal, cast

import pytest

from multiobjective_vns.pareto_viewer.app import objective_choice_id
from multiobjective_vns.pareto_viewer.data import (
    ObjectiveValueView,
    ObjectiveView,
    SolutionView,
    ViewExport,
)
from multiobjective_vns.pareto_viewer.plotting import (
    axis_tick_labels,
    axis_tick_positions,
    draw_legends,
    draw_objective_plot,
    marker_area,
    marker_category,
    size_legend_samples,
    status_legend_labels,
)


def _solution(
    archive_status: Literal[
        'pareto_optimal',
        'dominated',
        'objective_vector_duplicate',
        'rejected',
    ]
    | None,
    assessment: Literal['valid', 'invalid', 'infeasible'] | None,
    processing: Literal['completed', 'failed'] = 'completed',
) -> SolutionView:
    return SolutionView(
        solution_id='solution:1',
        processing_status=processing,
        assessment=assessment,
        archive_status=archive_status,
        objective_values=(),
        description=None,
    )


def test_marker_category_follows_design_precedence() -> None:
    assert marker_category(_solution('pareto_optimal', 'valid')) == 'pareto'
    assert marker_category(_solution('rejected', 'valid')) == 'rejected'
    assert (
        marker_category(_solution('objective_vector_duplicate', 'valid'))
        == 'objective_duplicate'
    )
    assert marker_category(_solution(None, 'invalid')) == 'invalid'
    assert marker_category(_solution(None, 'infeasible')) == 'infeasible'
    assert marker_category(_solution(None, 'valid')) == 'other'
    assert marker_category(_solution(None, None, 'failed')) == 'failed'


def test_marker_area_is_positive_and_monotone() -> None:
    low = marker_area(0.0)
    middle = marker_area(0.5)
    high = marker_area(1.0)
    assert 0.0 < low < middle < high
    assert math.isclose(low, 25.0)
    assert math.isclose(high, 225.0)


def test_axis_tick_positions_have_one_hundred_intervals() -> None:
    ticks = axis_tick_positions()
    assert len(ticks) == 101
    assert ticks[0] == 0.0
    assert ticks[-1] == 1.0


def _plot_export(
    values: tuple[tuple[str, str, str], ...], positions: tuple[float, ...]
) -> ViewExport:
    solutions = tuple(
        SolutionView(
            f'solution:{index}',
            'completed',
            'valid',
            'pareto_optimal',
            (
                ObjectiveValueView('distance', distance, position),
                ObjectiveValueView('risk', risk, position),
                ObjectiveValueView('waiting', waiting, position),
            ),
            None,
        )
        for index, ((distance, risk, waiting), position) in enumerate(
            zip(values, positions, strict=True)
        )
    )
    return ViewExport(
        invocation_id='run',
        problem_instance_id='problem',
        record_scope='pareto',
        commit_sequence=1,
        is_final=True,
        objectives=(
            ObjectiveView('distance', 'Distance', 'minimize'),
            ObjectiveView('risk', 'Risk', 'minimize'),
            ObjectiveView('waiting', 'Waiting', 'minimize'),
        ),
        solutions=solutions,
    )


def test_axis_tick_labels_use_observed_values_at_nearest_major_rank() -> None:
    export = _plot_export(
        (('short', 'low', 'few'), ('medium', 'mid', 'some'), ('long', 'high', 'many')),
        (0.0, 0.5, 1.0),
    )

    assert axis_tick_labels(export, objective_index=0) == (
        (0.0, 'short'),
        (0.5, 'medium'),
        (1.0, 'long'),
    )


def test_axis_tick_labels_show_one_value_at_its_rank_position() -> None:
    export = _plot_export((('same', 'same-risk', 'same-wait'),), (0.5,))

    assert axis_tick_labels(export, objective_index=0) == ((0.5, 'same'),)


def test_axis_tick_labels_keep_endpoint_values_and_suppress_repeated_levels() -> None:
    export = _plot_export(
        tuple((f'value-{i}', f'risk-{i}', f'wait-{i}') for i in range(11)),
        tuple(i / 10 for i in range(11)),
    )

    labels = axis_tick_labels(export, objective_index=0)

    assert labels[0] == (0.0, 'value-0')
    assert labels[-1] == (1.0, 'value-10')
    assert len(labels) == 11


def test_axis_tick_labels_assign_colliding_values_to_nearest_level() -> None:
    export = _plot_export(
        (
            ('best', 'low', 'few'),
            ('near-44', 'mid-44', 'some'),
            ('near-45', 'mid-45', 'some-more'),
            ('worst', 'high', 'many'),
        ),
        (0.0, 0.44, 0.45, 1.0),
    )

    assert axis_tick_labels(export, objective_index=0) == (
        (0.0, 'best'),
        (0.4, 'near-44'),
        (1.0, 'worst'),
    )


class _RecordingCanvas:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def delete(self, *_args: object) -> None:
        pass

    def winfo_width(self) -> int:
        return 800

    def winfo_height(self) -> int:
        return 600

    def create_line(self, *_args: object, **_options: object) -> int:
        return 1

    def create_text(self, *_args: object, **options: object) -> int:
        text = options.get('text')
        if isinstance(text, str):
            self.texts.append(text)
        return len(self.texts)

    def create_rectangle(self, *_args: object, **_options: object) -> int:
        return 1

    def create_oval(self, *_args: object, **_options: object) -> int:
        return 1

    def create_polygon(self, *_args: object, **_options: object) -> int:
        return 1


def test_plot_displays_objective_values_instead_of_rank_coordinates() -> None:
    export = _plot_export(
        (
            ('10 km', 'early', '5 min'),
            ('20 km', 'on-time', '10 min'),
            ('40 km', 'late', '20 min'),
        ),
        (0.0, 0.5, 1.0),
    )
    canvas = _RecordingCanvas()

    draw_objective_plot(
        cast(tk.Canvas, canvas),
        export,
        ('distance', 'risk', 'waiting'),
        None,
    )

    assert 'Distance' in canvas.texts
    assert 'Risk' in canvas.texts
    assert {'10 km', '20 km', '40 km', 'early', 'on-time', 'late'} <= set(canvas.texts)
    assert not any('(normalized rank)' in text for text in canvas.texts)
    assert not any(text in {'0.0', '0.1', '0.2', '1.0'} for text in canvas.texts)
    assert 'Point spacing follows objective rank' in canvas.texts


def test_size_legend_shows_values_without_normalized_rank_prefix() -> None:
    export = _plot_export(
        (('short', 'low', 'few'), ('medium', 'mid', 'some'), ('long', 'high', 'many')),
        (0.0, 0.5, 1.0),
    )
    label = _RecordingLabel()
    canvas = _RecordingCanvas()

    draw_legends(cast(ttk.Label, label), cast(tk.Canvas, canvas), export, 'waiting')

    assert {'few', 'some', 'many'} <= set(canvas.texts)
    assert not any(text.startswith(('0:', '0.5:', '1:')) for text in canvas.texts)
    assert 'Marker area: Waiting' in canvas.texts


class _RecordingLabel:
    def __init__(self) -> None:
        self.text = ''

    def configure(self, *, text: str) -> None:
        self.text = text


def test_objective_choice_keeps_the_stable_id_separate_from_its_label() -> None:
    objective = ObjectiveView('distance[mean]', 'Total distance', 'minimize')
    choice = 'Total distance  [distance[mean]]'

    assert objective_choice_id(choice) == objective.objective_id


def test_size_legend_uses_objective_formatted_values_at_scale_endpoints() -> None:
    export = ViewExport(
        invocation_id='run',
        problem_instance_id='problem',
        record_scope='pareto',
        commit_sequence=1,
        is_final=True,
        objectives=(ObjectiveView('size', 'Size', 'minimize'),),
        solutions=(
            SolutionView(
                'solution:1',
                'completed',
                'valid',
                'pareto_optimal',
                (ObjectiveValueView('size', 'small', 0.0),),
                None,
            ),
            SolutionView(
                'solution:2',
                'completed',
                'valid',
                'pareto_optimal',
                (ObjectiveValueView('size', 'large', 1.0),),
                None,
            ),
        ),
    )

    samples = size_legend_samples(export, objective_index=0)

    assert samples[0] == (0.0, 'small')
    assert samples[-1] == (1.0, 'large')


def test_size_legend_shows_one_actual_value_at_its_only_rank() -> None:
    export = ViewExport(
        invocation_id='run',
        problem_instance_id='problem',
        record_scope='registry',
        commit_sequence=1,
        is_final=False,
        objectives=(ObjectiveView('size', 'Size', 'minimize'),),
        solutions=(
            SolutionView(
                'solution:1',
                'completed',
                'valid',
                'pareto_optimal',
                (ObjectiveValueView('size', 'middle', 0.5),),
                None,
            ),
        ),
    )

    samples = size_legend_samples(export, objective_index=0)

    assert samples == ((0.5, 'middle'),)


def test_status_legend_covers_each_marker_category() -> None:
    assert status_legend_labels() == (
        ('pareto', 'Pareto'),
        ('rejected', 'Previously Pareto'),
        ('objective_duplicate', 'Objective tie'),
        ('other', 'Other valid'),
        ('infeasible', 'Infeasible'),
        ('invalid', 'Invalid'),
        ('failed', 'Failed'),
    )


def test_module_entrypoint_exits_with_gui_main_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import multiobjective_vns.pareto_viewer.app as app

    monkeypatch.setattr(app, 'main', lambda: 7)

    try:
        runpy.run_module(
            'multiobjective_vns.pareto_viewer.__main__', run_name='__main__'
        )
    except SystemExit as error:
        assert error.code == 7
    else:
        raise AssertionError('module entrypoint did not exit')


def test_main_reports_tk_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import multiobjective_vns.pareto_viewer.app as app

    def fail_to_create_root() -> None:
        raise tk.TclError('Tk is unavailable')

    monkeypatch.setattr(tk, 'Tk', fail_to_create_root)

    with pytest.raises(SystemExit) as error:
        app.main([])

    assert error.value.code == 2
    assert 'working Tk/Tcl installation' in capsys.readouterr().err
