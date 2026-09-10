"""ToolController lifecycle with JSON results and explicit RPM/mm-min units."""

import math
from typing import TYPE_CHECKING

import FreeCAD as App
from Path.Main.Job import ObjectJob
from Path.Tool import Controller
from Path.Tool.toolbit import ToolBit

if TYPE_CHECKING:
    # Supplied by the preceding cam_properties runtime in bridge commands.
    from .cam_properties import apply_parameters, check_parameters
    from .transaction import mcp_resume_transaction


def native_controller(name):
    doc = App.ActiveDocument
    obj = doc.getObject(name) if doc else None
    if obj is None or not isinstance(
        getattr(obj, "Proxy", None), Controller.ToolController
    ):
        raise ValueError("Native ToolController not found: " + name)
    return obj


def controller_settings(number, spindle, horizontal, vertical):
    values = {}
    if number is not None:
        if type(number) is not int or not 1 <= number <= 10000:
            raise ValueError("Tool number must be an integer between 1 and 10000")
        values["ToolNumber"] = number
    for key, value in [
        ("SpindleSpeed", spindle),
        ("HorizFeed", horizontal),
        ("VertFeed", vertical),
    ]:
        if value is None:
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Finite non-negative value required: " + key)
        values[key] = value if key == "SpindleSpeed" else str(value) + " mm/min"
    return values


def controller_data(controller):
    """Keep native Quantities out of transport JSON; report explicit units."""
    tool = controller.Tool
    properties = {}
    if tool:
        for key in (
            "Diameter",
            "Length",
            "CuttingEdgeHeight",
            "Flutes",
            "Material",
            "ShapeType",
            "Pitch",
        ):
            if key not in tool.PropertiesList:
                continue
            value = getattr(tool, key)
            properties[key] = (
                str(value) if isinstance(value, App.Units.Quantity) else value
            )
    return {
        "name": controller.Name,
        "label": controller.Label,
        "tool_number": controller.ToolNumber,
        "spindle_speed": controller.SpindleSpeed,
        "horizontal_feed": controller.HorizFeed.getValueAs("mm/min").Value,
        "vertical_feed": controller.VertFeed.getValueAs("mm/min").Value,
        "spindle_direction": str(controller.SpindleDir),
        "spindle_unit": "rpm",
        "feed_unit": "mm/min",
        "tool_name": tool.Name if tool else None,
        "tool": properties,
        "toolbit": tool.Proxy.to_dict()
        if tool and isinstance(tool.Proxy, ToolBit)
        else None,
    }


def check_tool_number(controller, number):
    if number is None:
        return
    for obj in controller.Document.Objects:
        if (
            isinstance(getattr(obj, "Proxy", None), ObjectJob)
            and controller in obj.Tools.Group
        ):
            if any(
                tc is not controller and tc.ToolNumber == number
                for tc in obj.Tools.Group
            ):
                raise ValueError("Job already contains this tool number")


def set_controller(name, number, spindle, horizontal, vertical, tool_parameters):
    controller = native_controller(name)
    values = controller_settings(number, spindle, horizontal, vertical)
    check_tool_number(controller, number)
    if tool_parameters:
        if controller.Tool is None:
            raise ValueError("ToolController has no ToolBit")
        check_parameters(controller.Tool, tool_parameters)
    apply_parameters(controller, values)
    if tool_parameters:
        apply_parameters(controller.Tool, tool_parameters)
    controller.Document.recompute()
    if not controller.isValid() or (controller.Tool and not controller.Tool.isValid()):
        raise ValueError("Native ToolController or ToolBit recompute failed")
    return controller_data(controller)


def add_controller(
    job_name,
    number,
    spindle,
    horizontal,
    vertical,
    tool_parameters,
    label="",
    create_new=False,
    shape_id="",
):
    doc = App.ActiveDocument
    job = doc.getObject(job_name) if doc else None
    if job is None or not isinstance(getattr(job, "Proxy", None), ObjectJob):
        raise ValueError("Native CAM Job required")
    controller_settings(number, spindle, horizontal, vertical)
    existing = list(job.Tools.Group)
    controller = existing[0] if existing and not create_new else None
    if any(tc is not controller and tc.ToolNumber == number for tc in existing):
        raise ValueError("Job already contains this tool number")
    tool = None
    if shape_id or controller is None:
        bit = ToolBit.from_shape_id(shape_id or "endmill.fcstd")
        tool = bit.attach_to_doc(doc=doc)
        mcp_resume_transaction()
        check_parameters(tool, tool_parameters)
    if controller is None:
        controller = Controller.Create(
            name=label or "ToolController",
            tool=tool,
            toolNumber=number,
            assignViewProvider=bool(App.GuiUp),
        )
        job.Proxy.addToolController(controller)
    elif tool is not None:
        controller.Tool = tool
    if label:
        controller.Label = label
    result = set_controller(
        controller.Name, number, spindle, horizontal, vertical, tool_parameters
    )
    return result | {"job": job.Name, "tool_asset": shape_id or None}
