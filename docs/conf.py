"""Sphinx configuration."""

from brightcon_environ import __version__

project = "brightcon-environ"
author = "Tomás Navarrete Gutiérrez"
copyright = f"2026, {author}"
release = __version__
version = release

extensions = ["myst_parser"]

exclude_patterns = ["_build"]

html_theme = "furo"
html_title = f"{project} {release}"

myst_heading_anchors = 3
