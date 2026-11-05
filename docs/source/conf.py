"""Sphinx configuration for the public API documentation.

Created: 2026-10-29T13:10:12+00:00
Author: Michel Bierlaire, EPFL
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

project = 'multiobjective-vns'
author = 'Michel Bierlaire, EPFL'
copyright = '2026, Michel Bierlaire, EPFL'
release = '0.1.0'

extensions = ['sphinx.ext.autodoc']
templates_path = ['_templates']
exclude_patterns = ['_build']
root_doc = 'index'
html_theme = 'alabaster'
autodoc_typehints = 'description'
