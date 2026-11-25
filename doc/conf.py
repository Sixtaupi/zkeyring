# SPDX-License-Identifier: Apache-2.0

import tomllib
from pathlib import Path

with (Path(__file__).parents[1] / 'pyproject.toml').open('rb') as pyproject:
    release = tomllib.load(pyproject)['project']['version']

project = 'zkeyring'
author = 'zkeyring contributors'
copyright = '2026, zkeyring contributors'

extensions = ['sphinx.ext.intersphinx', 'sphinx_copybutton', 'sphinx_design']
exclude_patterns = ['_build_sphinx', '_build_doxygen', 'deploy', 'Thumbs.db', '.DS_Store']

html_theme = 'sphinx_rtd_theme'
html_title = f'zkeyring {release}'
html_static_path = ['_static']
html_css_files = ['zkeyring.css']
html_show_sourcelink = False
html_theme_options = {
    'collapse_navigation': False,
    'navigation_depth': 2,
    'version_selector': False,
    'language_selector': False,
}

intersphinx_mapping = {'zephyr': ('https://docs.zephyrproject.org/latest/', None)}
