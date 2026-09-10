"""Native operation creation with deterministic tool selection and path status.

FreeCAD operations normally ask the GUI to choose a ToolController while the
factory runs. Supply a scoped, noninteractive choice before initialization;
restore the native chooser even when construction fails.
"""

from contextlib import contextmanager
import importlib
from typing import TYPE_CHECKING

import FreeCAD as App
from Path.Main.Job import ObjectJob
from PathScripts import PathUtils

if TYPE_CHECKING:
    # Supplied by the preceding cam_properties runtime in bridge commands.
    from .cam_properties import apply_parameters


def operation_job(name):
    doc = App.ActiveDocument
    job = doc.getObject(name) if doc else None
    if job is None or not isinstance(getattr(job, "Proxy", None), ObjectJob):
        raise ValueError("Native CAM Job not found: " + name)
    return job


@contextmanager
def operation_tool_selection(job, controller_name):
    """Choose a supported controller without changing Job groups or GUI state."""
    requested = job.Document.getObject(controller_name) if controller_name else None
    if controller_name and (requested is None or requested not in job.Tools.Group):
        raise ValueError("ToolController must belong to this Job")
    original = PathUtils.findToolController

    def choose(obj, proxy, name=None):
        if PathUtils.findParentJob(obj) is not job:
            return original(obj, proxy, name)
        candidates = [requested] if requested is not None else list(job.Tools.Group)
        for controller in candidates:
            tool = getattr(controller, "Tool", None)
            if tool is not None and proxy.isToolSupported(obj, tool):
                return controller
        raise ValueError("No compatible ToolController for this operation")

    PathUtils.findToolController = choose
    try:
        yield choose
    finally:
        PathUtils.findToolController = original


def create_native_operation(job, module_name, name, controller_name=""):
    """Initialize the real factory and check prior-operation tool inheritance."""
    module = importlib.import_module("Path.Op." + module_name)
    with operation_tool_selection(job, controller_name) as choose:
        obj = module.Create(name, None, job)
        if hasattr(obj, "ToolController"):
            # Native defaults can copy a prior operation's tool, bypassing the
            # chooser. Revalidate it before executing the configured operation.
            obj.ToolController = choose(obj, obj.Proxy)
    return obj


def operation_path_status(obj):
    """Describe generated motion separately from comments and setup commands."""
    commands = list(obj.Path.Commands)
    rapid, machining = 0, 0
    for command in commands:
        name = command.Name.upper()
        if not name.startswith("G"):
            continue
        try:
            code = float(name[1:])
        except ValueError:
            continue
        coordinates = bool(set(command.Parameters) & set("XYZABCUVW"))
        if code == 0 and coordinates:
            rapid += 1
        elif (
            code in (1, 2, 3, 5, 33, 73, 74, 76, 81, 82, 83, 84, 85, 86, 87, 88, 89)
            or 38 <= code < 39
        ) and coordinates:
            machining += 1
    active = bool(getattr(obj, "Active", True))
    valid = obj.isValid()
    status = (
        "invalid"
        if not valid
        else "inactive"
        if not active
        else "empty"
        if not commands
        else "generated"
        if machining
        else "no_machining_motion"
    )
    return {
        "command_count": len(commands),
        "rapid_motion_command_count": rapid,
        "machining_motion_command_count": machining,
        "path_status": status,
        "path_ready": status == "generated",
        "native_valid": valid,
        "native_state": list(obj.State),
        "path_warning": None
        if status == "generated"
        else "Operation has no active valid machining motion; check geometry, tool and parameters",
    }


def add_operation(
    job_name, module_name, base_name, subelements, parameters, controller_name=""
):
    """Create native geometry references and scalar settings in one transaction."""
    job = operation_job(job_name)
    base = operation_model(job, base_name) if base_name else None
    if base_name and (
        base is None or not hasattr(base, "Shape") or base.Shape.isNull()
    ):
        raise ValueError("Nonempty base geometry required")
    if subelements and base is None:
        raise ValueError("Subelements require a base object")
    for element in subelements:
        if not element or base.Shape.getElement(element).isNull():
            raise ValueError("Base subelement not found: " + element)
    obj = create_native_operation(job, module_name, module_name, controller_name)
    if base is not None:
        if not hasattr(obj, "Base"):
            raise ValueError("This operation does not accept Base geometry")
        obj.Base = [(base, tuple(subelements))]
    applied = apply_parameters(obj, parameters)
    job.Proxy.addOperation(obj)
    job.Document.recompute()
    if not obj.isValid():
        raise ValueError("Native operation recompute failed: " + obj.getStatusString())
    return {
        "name": obj.Name,
        "operation": module_name.lower(),
        "native_module": "Path.Op." + module_name,
        "native_type": obj.TypeId,
        "job": job.Name,
        "base_model": base.Name if base else None,
        "active": bool(obj.Active),
        "tool_controller": obj.ToolController.Name
        if getattr(obj, "ToolController", None)
        else None,
        "parameters_applied": applied,
        "parameters_rejected": [],
        "parameter_errors": [],
        "property_count": len(obj.PropertiesList),
        **operation_path_status(obj),
    }


def operation_model(job, name):
    """Resolve a design source to its uniquely instanced machining clone."""
    source = job.Document.getObject(name) if isinstance(name, str) else None
    if source is None:
        raise ValueError("Base object not found")
    if source in job.Model.Group:
        return source
    matches = [
        model for model in job.Model.Group
        if job.Proxy.baseObject(job, model) is source
    ]
    if len(matches) != 1:
        raise ValueError(
            "Source must resolve to one Job clone; select an explicit clone when repeated"
        )
    return matches[0]


def operation_schema(job_name, module_name, controller_name=""):
    """Read actual properties of an ephemeral native operation, then remove it."""
    job = operation_job(job_name)
    obj = create_native_operation(job, module_name, "SchemaProbe", controller_name)
    try:
        return {
            "operation": module_name.lower(),
            "native_module": "Path.Op." + module_name,
            "properties": [
                {
                    "name": key,
                    "type": obj.getTypeIdOfProperty(key),
                    "group": obj.getGroupOfProperty(key),
                    "documentation": obj.getDocumentationOfProperty(key),
                    "enum": obj.getEnumerationsOfProperty(key)
                    if obj.getTypeIdOfProperty(key) == "App::PropertyEnumeration"
                    else None,
                }
                for key in obj.PropertiesList
            ],
        }
    finally:
        job.Document.removeObject(obj.Name)


def update_operation(name, parameters, recompute=True):
    """Apply parameters and reject failed recomputes with a stale prior path."""
    doc = App.ActiveDocument
    obj = doc.getObject(name) if doc else None
    if (
        obj is None
        or not obj.isDerivedFrom("Path::Feature")
        or not hasattr(obj, "ToolController")
    ):
        raise ValueError("Native Path operation required")
    applied = apply_parameters(obj, parameters)
    if recompute:
        obj.touch()
        doc.recompute()
        if not obj.isValid():
            raise ValueError(
                "Native operation recompute failed: " + obj.getStatusString()
            )
    status = operation_path_status(obj)
    if not recompute:
        status.update(
            path_ready=False,
            path_status="recompute_pending",
            path_warning="Path still represents the previous parameters; recompute is required",
        )
    return {
        "name": obj.Name,
        "applied": applied,
        "rejected": [],
        "errors": [],
        "commands": len(obj.Path.Commands),
        "length": float(obj.Path.Length),
        "recomputed": recompute,
        **status,
    }
