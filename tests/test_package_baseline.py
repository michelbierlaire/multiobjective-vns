"""Smoke tests for the package development baseline.

Created: 2026-10-29T13:10:12+00:00
Author: Michel Bierlaire, EPFL
"""

import multiobjective_vns


def test_package_has_american_english_description() -> None:
    """Expose the package name and description through its public module."""
    assert multiobjective_vns.__doc__ is not None
    assert multiobjective_vns.__doc__.splitlines()[0] == (
        'Reusable multiobjective variable neighborhood search framework.'
    )
