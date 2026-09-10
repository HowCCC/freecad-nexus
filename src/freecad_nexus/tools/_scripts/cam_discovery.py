"""Discover installed CAM dressup modules without importing GUI-only modules."""

import importlib.util

import FreeCAD as App


def dressup_catalog():
    definitions = [
        ("Boundary", "Path.Dressup.Boundary", True, False),
        ("Array", "Path.Dressup.Array", True, False),
        ("Tags", "Path.Dressup.Tags", True, False),
        ("DogboneII", "Path.Dressup.DogboneII", True, False),
        ("RampEntry", "Path.Dressup.Gui.RampEntry", True, True),
        ("LeadInOut", "Path.Dressup.Gui.LeadInOut", True, True),
        ("Dragknife", "Path.Dressup.Gui.Dragknife", True, True),
        ("AxisMap", "Path.Dressup.Gui.AxisMap", True, True),
        ("ZCorrect", "Path.Dressup.Gui.ZCorrect", True, True),
        ("Dogbone", "Path.Dressup.Gui.Dogbone", True, True),
    ]
    items = []
    for name, module, supported, gui in definitions:
        try:
            installed = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError, AttributeError):
            installed = False
        item = {
            "dressup": name,
            "module": module,
            "installed": installed,
            "mcp_creation_supported": supported,
            "gui_required": gui,
            "available": installed and supported and (not gui or bool(App.GuiUp)),
        }
        items.append(item)
    return items
