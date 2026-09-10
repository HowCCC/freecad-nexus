"""FreeCAD Nexus Server - AI assistant integration for FreeCAD.

SPDX-License-Identifier: MIT
Copyright (c) 2025 Sean P. Kane (GitHub: spkane)

This package provides an MCP (Model Context Protocol) server that enables
integration between AI assistants (Claude, GPT, etc.) and FreeCAD, allowing
AI-assisted development and debugging of 3D models, macros, and workbenches.

Example:
    Run FreeCAD Nexus::

        $ freecad-nexus

    Or with Python::

        >>> from freecad_nexus.server import main
        >>> main()
"""

from importlib.metadata import PackageNotFoundError, version

__version__: str
try:
    __version__ = version("freecad-nexus")
except PackageNotFoundError:
    # Package is not installed (running from source without pip install -e)
    # Fall back to the generated _version.py if available
    try:
        from freecad_nexus._version import __version__ as _v

        __version__ = _v
    except ImportError:
        __version__ = "0.0.0.dev0+unknown"

__author__ = "FreeCAD Nexus contributors"

from freecad_nexus.server import mcp

__all__ = ["__version__", "mcp"]
