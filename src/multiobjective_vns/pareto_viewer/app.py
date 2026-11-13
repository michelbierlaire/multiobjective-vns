"""Interactive desktop viewer for generic Pareto-view exports.

Created: 2026-11-12T12:44:55+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import argparse
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Sequence

from multiobjective_vns.pareto_viewer.data import (
    ObjectiveView,
    SolutionView,
    ViewExport,
    read_view_export,
)
from multiobjective_vns.pareto_viewer.discovery import (
    ViewSource,
    discover_view_sources,
    preferred_view_path,
    refresh_view_source,
)
from multiobjective_vns.pareto_viewer.plotting import (
    draw_legends,
    draw_objective_plot,
    marker_category,
    nearest_point,
)


class ParetoViewer:
    """Tk desktop interface for opening and monitoring viewer exports."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        source_path: Path | None = None,
        refresh_interval_ms: int = 1000,
    ) -> None:
        self.root = root
        self.refresh_interval_ms = refresh_interval_ms
        self.source: ViewSource | None = None
        self.export: ViewExport | None = None
        self._file_signature: tuple[int, int] | None = None
        self._point_centers: dict[str, tuple[float, float]] = {}
        self._point_by_id: dict[str, SolutionView] = {}
        self._selected_id: str | None = None
        self._build_widgets()
        self.root.title('Multiobjective VNS Pareto Viewer')
        self.root.geometry('1180x760')
        if source_path is not None:
            self._load_path(source_path)
        else:
            self._load_default_source()
        self.root.after(self.refresh_interval_ms, self._poll_file)

    def _build_widgets(self) -> None:
        toolbar = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text='Open…', command=self._open_dialog).pack(side=tk.LEFT)
        self.source_label = ttk.Label(toolbar, text='No view loaded')
        self.source_label.pack(side=tk.LEFT, padx=12)
        self.status_label = ttk.Label(toolbar, text='')
        self.status_label.pack(side=tk.RIGHT)

        selectors = ttk.Frame(self.root, padding=(8, 4, 8, 8))
        selectors.pack(fill=tk.X)
        self.axis_vars = [tk.StringVar() for _index in range(3)]
        self.axis_boxes: list[ttk.Combobox] = []
        titles = ('Horizontal axis', 'Vertical axis', 'Marker area')
        for index, title in enumerate(titles):
            ttk.Label(selectors, text=title).pack(side=tk.LEFT, padx=(0, 4))
            box = ttk.Combobox(
                selectors,
                textvariable=self.axis_vars[index],
                state='readonly',
                width=32,
            )
            box.pack(side=tk.LEFT, padx=(0, 14))
            box.bind('<<ComboboxSelected>>', self._selection_changed)
            self.axis_boxes.append(box)

        body = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        plot_panel = ttk.Frame(body)
        record_panel = ttk.Frame(body, width=350)
        body.add(plot_panel, weight=4)
        body.add(record_panel, weight=1)
        self.canvas = tk.Canvas(
            plot_panel,
            background='white',
            highlightthickness=1,
            highlightbackground='#777777',
        )
        self.canvas.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        self.canvas.bind('<Configure>', self._canvas_resized)
        self.canvas.bind('<Button-1>', self._select_nearest_point)
        self.canvas.bind('<Motion>', self._inspect_hover)
        self.canvas.bind('<Leave>', self._clear_hover)

        legend_panel = ttk.Frame(plot_panel, padding=(4, 4, 4, 0))
        legend_panel.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_legend = ttk.Label(legend_panel, anchor='w')
        self.status_legend.pack(fill=tk.X)
        self.size_legend_canvas = tk.Canvas(
            legend_panel,
            height=58,
            background='white',
            highlightthickness=0,
        )
        self.size_legend_canvas.pack(fill=tk.X)
        self.hover_label = ttk.Label(legend_panel, anchor='w')
        self.hover_label.pack(fill=tk.X)
        self.hover_label.configure(text='Hover over a point for its ID and values')

        ttk.Label(record_panel, text='Solutions').pack(anchor='w')
        self.solution_list = tk.Listbox(record_panel, exportselection=False)
        self.solution_list.pack(fill=tk.BOTH, expand=True, pady=(4, 8))
        self.solution_list.bind('<<ListboxSelect>>', self._list_selection_changed)
        ttk.Label(record_panel, text='Selected record').pack(anchor='w')
        self.details = tk.Text(record_panel, height=12, width=42, wrap=tk.WORD)
        self.details.pack(fill=tk.X, pady=(4, 0))
        self.details.configure(state=tk.DISABLED)

    def _load_default_source(self) -> None:
        try:
            sources = discover_view_sources(Path.cwd())
        except (OSError, ValueError) as error:
            self._show_error(f'Could not inspect viewer exports: {error}')
            self._open_dialog()
            return
        if len(sources) == 1:
            self._load_source(sources[0])
            return
        if len(sources) > 1:
            self._open_dialog(
                title='Choose an invocation to view',
                initial_directory=Path.cwd(),
            )
            return
        self._open_dialog(
            title='Open a Pareto-view export', initial_directory=Path.cwd()
        )

    def _open_dialog(
        self,
        *,
        title: str = 'Open a Pareto-view export',
        initial_directory: Path | None = None,
    ) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            title=title,
            initialdir=str(initial_directory or Path.cwd()),
            filetypes=(
                ('Pareto-view JSON', '*.registry-view.json *.pareto-view.json'),
                ('JSON', '*.json'),
                ('All files', '*'),
            ),
        )
        if selected:
            self._load_path(Path(selected))

    def _load_path(self, path: Path) -> None:
        try:
            preferred = preferred_view_path(path)
            export = read_view_export(preferred)
            self.source = self._source_for_path(preferred, export)
            self._install_export(export)
            self._file_signature = _signature(preferred)
        except (OSError, ValueError, TypeError) as error:
            self._show_error(f'Could not load {path}: {error}')

    def _source_for_path(self, path: Path, export: ViewExport) -> ViewSource:
        registry = path if export.record_scope == 'registry' else None
        pareto = path if export.record_scope == 'pareto' else None
        sibling = path.parent / f'{export.invocation_id}.registry-view.json'
        if sibling.exists():
            registry = sibling
        return ViewSource(
            export.invocation_id,
            path,
            export.record_scope,
            registry,
            pareto,
        )

    def _load_source(self, source: ViewSource) -> None:
        self.source = source
        self._load_path(source.path)

    def _install_export(self, export: ViewExport) -> None:
        previous_selection = tuple(
            objective_choice_id(variable.get()) for variable in self.axis_vars
        )
        self.export = export
        self._point_by_id = {
            solution.solution_id: solution for solution in export.solutions
        }
        if self._selected_id not in self._point_by_id:
            self._selected_id = None
        elif self._selected_id is not None:
            self._write_details(self._point_by_id[self._selected_id])
        self.source_label.configure(
            text=(
                f'{export.invocation_id} · {export.record_scope} · '
                f'commit {export.commit_sequence}'
                f'{" · final" if export.is_final else " · live"}'
            )
        )
        usable_values: list[ObjectiveView] = []
        for index, objective in enumerate(export.objectives):
            if any(
                solution.objective_values[index].normalized_position is not None
                for solution in export.solutions
            ):
                usable_values.append(objective)
        usable = tuple(usable_values)
        if not usable:
            usable = export.objectives
        labels = tuple(_objective_choice(objective) for objective in usable)
        for box in self.axis_boxes:
            box['values'] = labels
        if len(usable) >= 3:
            usable_ids = {objective.objective_id for objective in usable}
            if len(set(previous_selection)) == 3 and all(
                objective_id in usable_ids for objective_id in previous_selection
            ):
                choices_by_id = {
                    objective.objective_id: _objective_choice(objective)
                    for objective in usable
                }
                for variable, objective_id in zip(
                    self.axis_vars, previous_selection, strict=True
                ):
                    variable.set(choices_by_id[objective_id])
            else:
                self._set_default_axis_selections(usable)
            for box in self.axis_boxes:
                box.configure(state='readonly')
        else:
            for variable in self.axis_vars:
                variable.set('')
            for box in self.axis_boxes:
                box.configure(state='disabled')
        self.solution_list.delete(0, tk.END)
        for solution in export.solutions:
            self.solution_list.insert(
                tk.END,
                f'{solution.solution_id}  [{_status_text(solution)}]',
            )
        self.status_label.configure(
            text=(
                'Choose three objectives to plot'
                if len(usable) < 3
                else f'{len(export.solutions)} records'
            )
        )
        self._draw_legends()
        self._draw_plot()

    def _set_default_axis_selections(
        self, objectives: tuple[ObjectiveView, ...]
    ) -> None:
        for index in range(3):
            self.axis_vars[index].set(_objective_choice(objectives[index]))

    def _selection_changed(self, event: tk.Event[tk.Misc]) -> None:
        del event
        chosen = [variable.get() for variable in self.axis_vars]
        if len(set(chosen)) != 3:
            changed = next(
                (
                    index
                    for index, value in enumerate(chosen)
                    if chosen.count(value) > 1
                ),
                0,
            )
            available = [box_value for box_value in self.axis_boxes[changed]['values']]
            replacement = next(
                (value for value in available if value not in chosen), None
            )
            if replacement is not None:
                self.axis_vars[changed].set(str(replacement))
        self._draw_plot()
        self._draw_legends()

    def _draw_plot(self) -> None:
        objective_ids = tuple(
            objective_choice_id(variable.get()) for variable in self.axis_vars
        )
        self._point_centers = draw_objective_plot(
            self.canvas,
            self.export,
            objective_ids,
            self._selected_id,
        )

    def _canvas_resized(self, event: tk.Event[tk.Misc]) -> None:
        del event
        self._draw_plot()
        self._draw_legends()

    def _draw_legends(self) -> None:
        draw_legends(
            self.status_legend,
            self.size_legend_canvas,
            self.export,
            objective_choice_id(self.axis_vars[2].get()),
        )

    def _select_nearest_point(self, event: tk.Event[tk.Misc]) -> None:
        solution_id = nearest_point(
            self._point_centers, event.x, event.y, tolerance=14.0
        )
        if solution_id is not None:
            self._select_solution(solution_id)

    def _inspect_hover(self, event: tk.Event[tk.Misc]) -> None:
        solution_id = nearest_point(
            self._point_centers, event.x, event.y, tolerance=18.0
        )
        if solution_id is None:
            self._clear_hover(event)
            return
        solution = self._point_by_id.get(solution_id)
        if solution is None:
            return
        objective_indices = (
            {
                objective.objective_id: index
                for index, objective in enumerate(self.export.objectives)
            }
            if self.export is not None
            else {}
        )
        selected_values = []
        for variable in self.axis_vars:
            objective_id = objective_choice_id(variable.get())
            index = objective_indices.get(objective_id)
            if index is not None:
                value = solution.objective_values[index].display_value
                selected_values.append(f'{objective_id}={value}')
        values_text = '; '.join(selected_values)
        self.hover_label.configure(
            text=(
                f'Hover: {solution.solution_id} · '
                f'{_status_text(solution)}'
                + (f' · {values_text}' if values_text else '')
            )
        )

    def _clear_hover(self, event: tk.Event[tk.Misc]) -> None:
        del event
        self.hover_label.configure(text='Hover over a point for its ID and values')

    def _list_selection_changed(self, event: tk.Event[tk.Misc]) -> None:
        del event
        selection = self.solution_list.curselection()  # type: ignore[no-untyped-call]
        if selection and self.export is not None:
            solution = self.export.solutions[selection[0]]
            self._select_solution(solution.solution_id)

    def _select_solution(self, solution_id: str) -> None:
        solution = self._point_by_id.get(solution_id)
        if solution is None:
            return
        self._selected_id = solution_id
        self._write_details(solution)
        self._draw_plot()

    def _write_details(self, solution: SolutionView) -> None:
        lines = [
            f'Solution: {solution.solution_id}',
            f'Processing: {solution.processing_status}',
            f'Assessment: {solution.assessment or "unavailable"}',
            f'Archive status: {solution.archive_status or "unclassified"}',
        ]
        if solution.description:
            lines.extend(('', solution.description))
        lines.append('')
        lines.extend(
            f'{value.objective_id}: {value.display_value or "unavailable"}'
            for value in solution.objective_values
        )
        self.details.configure(state=tk.NORMAL)
        self.details.delete('1.0', tk.END)
        self.details.insert('1.0', '\n'.join(lines))
        self.details.configure(state=tk.DISABLED)

    def _poll_file(self) -> None:
        source = self.source
        if source is not None:
            try:
                refreshed = refresh_view_source(source)
                signature = _signature(refreshed.path)
                if signature != self._file_signature or refreshed.path != source.path:
                    export = read_view_export(refreshed.path)
                    self.source = refreshed
                    self._file_signature = signature
                    self._install_export(export)
                self.status_label.configure(
                    text=(
                        f'{len(self.export.solutions)} records'
                        if self.export is not None
                        else ''
                    )
                )
            except (OSError, ValueError, TypeError) as error:
                self.status_label.configure(text=f'Reload error: {error}')
        self.root.after(self.refresh_interval_ms, self._poll_file)

    def _show_error(self, message: str) -> None:
        self.status_label.configure(text=message)
        messagebox.showerror('Pareto Viewer', message, parent=self.root)


def _signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def _objective_choice(objective: ObjectiveView) -> str:
    return f'{objective.label}  [{objective.objective_id}]'


def objective_choice_id(choice: str) -> str:
    """Extract the stable objective ID from a selector label."""
    marker = '  ['
    if not isinstance(choice, str) or marker not in choice or not choice.endswith(']'):
        return ''
    return choice.rsplit(marker, 1)[1][:-1]


def _status_text(solution: SolutionView) -> str:
    return marker_category(solution).replace('_', ' ')


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the GUI, auto-loading the current directory when possible."""
    parser = argparse.ArgumentParser(description='View a multiobjective VNS archive')
    parser.add_argument(
        'path',
        nargs='?',
        type=Path,
        help='viewer JSON file; registry scope is preferred when available',
    )
    parser.add_argument(
        '--refresh-interval-ms',
        type=int,
        default=1000,
        help='interval for checking live exports (default: 1000 ms)',
    )
    arguments = parser.parse_args(argv)
    if arguments.refresh_interval_ms < 100:
        parser.error('--refresh-interval-ms must be at least 100')
    try:
        root = tk.Tk()
    except tk.TclError as error:
        parser.exit(
            2,
            f'error: viewer requires a working Tk/Tcl installation: {error}\n',
        )
    ParetoViewer(
        root,
        source_path=arguments.path,
        refresh_interval_ms=arguments.refresh_interval_ms,
    )
    root.mainloop()
    return 0


__all__ = [
    'ParetoViewer',
    'main',
    'objective_choice_id',
]
