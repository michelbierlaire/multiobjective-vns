"""Smoke tests for the Sphinx documentation build.

Created: 2026-10-29T13:10:12+00:00
Author: Michel Bierlaire, EPFL
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_sphinx_builds_api_documentation(tmp_path: Path) -> None:
    """Build the documentation with warnings treated as errors."""
    source = PROJECT_ROOT / 'docs' / 'source'
    output = tmp_path / 'html'
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'sphinx',
            '-W',
            '--keep-going',
            '-b',
            'html',
            '-c',
            str(source),
            str(source),
            str(output),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, f'{result.stdout}\n{result.stderr}'
    assert (output / 'index.html').is_file()
