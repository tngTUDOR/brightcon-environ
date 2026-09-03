"""Rebuild JupyterHub Python environments in response to GitHub push webhooks."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("brightcon-environ")
except PackageNotFoundError:
    # Imported from a source tree that was never installed.
    __version__ = "0.0.0"
