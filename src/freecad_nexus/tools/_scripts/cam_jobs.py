"""Job inspection, readiness checks and SetupSheet edits in native FreeCAD."""

import math
from typing import TYPE_CHECKING

import FreeCAD as App
import Path.Base.Util as PathUtil

if TYPE_CHECKING:
    from .cam_operations import operation_job, operation_path_status
    from .cam_controllers import controller_data
    from .cam_properties import apply_parameters


def cam_json_value(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, App.Units.Quantity):
        return {"value": value.Value, "unit": str(value.Unit), "quantity": str(value)}
    if isinstance(value, App.Vector):
        return list(value)
    if isinstance(value, App.Placement):
        return {"base": list(value.Base), "rotation": list(value.Rotation.Q)}
    if isinstance(value, (list, tuple)):
        return [cam_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): cam_json_value(v) for k, v in value.items()}
    if hasattr(value, "Document") and hasattr(value, "Name"):
        return {"document": value.Document.Name, "object": value.Name}
    return str(value)


def job_validation(job, recompute=False):
    """Report concrete native/model/tool/path problems before posting."""
    if recompute:
        job.Document.recompute()
    issues, warnings, operations = [], [], []
    models = list(job.Model.Group) if job.Model else []
    controllers = list(job.Tools.Group) if job.Tools else []
    if not models:
        issues.append("Job has no model objects")
    for model in models:
        if (
            not hasattr(model, "Shape")
            or model.Shape.isNull()
            or not model.Shape.isValid()
            or not model.isValid()
        ):
            issues.append("Invalid or empty model: " + model.Name)
    stock = job.Stock
    if (
        stock is None
        or stock.Shape.isNull()
        or not stock.Shape.isValid()
        or not stock.isValid()
    ):
        issues.append("Job has no valid stock")
    if not job.Fixtures:
        issues.append("Job has no work coordinate systems")
    if len(set(job.Fixtures)) != len(job.Fixtures):
        issues.append("Job contains duplicate work coordinate systems")
    if not job.SetupSheet or not job.SetupSheet.isValid():
        issues.append("Job has no valid SetupSheet")
    if not controllers:
        issues.append("Job has no tool controllers")
    numbers = [tc.ToolNumber for tc in controllers]
    if len(numbers) != len(set(numbers)):
        issues.append("Job contains duplicate tool numbers")
    top_level = list(job.Operations.Group) if job.Operations else []
    active_count, machining_count = 0, 0
    for op in top_level:
        active = bool(PathUtil.activeForOp(op))
        if not hasattr(op, "Path"):
            issues.append("Job contains a non-Path operation: " + op.Name)
            continue
        status = operation_path_status(op)
        status.update(name=op.Name, active=active)
        operations.append(status)
        if not active:
            continue
        active_count += 1
        machining_count += status["machining_motion_command_count"]
        if not op.isValid():
            issues.append(
                "Invalid active operation: " + op.Name + ": " + op.getStatusString()
            )
        if "Touched" in op.State:
            issues.append("Active operation requires recompute: " + op.Name)
        if not op.Path.Commands:
            issues.append("Active operation has empty path: " + op.Name)
        tc = PathUtil.toolControllerForOp(op)
        if status["machining_motion_command_count"] and tc is None:
            issues.append("Machining operation has no ToolController: " + op.Name)
        if tc is not None:
            if tc not in controllers:
                issues.append("Operation ToolController is outside Job: " + op.Name)
            elif tc.Tool is None or not tc.Tool.isValid() or not tc.isValid():
                issues.append("Invalid controller or ToolBit: " + tc.Name)
            else:
                if getattr(tc.Tool, "Diameter", App.Units.Quantity(0)).Value <= 0:
                    issues.append("Tool diameter must be positive: " + tc.Name)
                if tc.HorizFeed.Value <= 0 or tc.VertFeed.Value <= 0:
                    warnings.append("Nonpositive feed on controller: " + tc.Name)
                if tc.SpindleSpeed <= 0:
                    warnings.append("Zero spindle speed on controller: " + tc.Name)
    if active_count == 0:
        issues.append("Job has no active operations")
    if machining_count == 0:
        issues.append("Job has no machining or probing motion")
    return {
        "job": job.Name,
        "valid": not issues,
        "issues": issues,
        "warnings": warnings,
        "model_count": len(models),
        "tool_count": len(controllers),
        "operation_count": len(top_level),
        "active_operation_count": active_count,
        "operations": operations,
        "recomputed": recompute,
        "scope": "native object, reference and generated-path checks; no collision or machining correctness proof",
    }


def inspect_job(job_name):
    job = operation_job(job_name)
    return {
        "name": job.Name,
        "type": job.TypeId,
        "model": [obj.Name for obj in job.Model.Group],
        "stock": job.Stock.Name if job.Stock else None,
        "setup_sheet": job.SetupSheet.Name if job.SetupSheet else None,
        "cycle_time": cam_json_value(job.CycleTime),
        "post_processor": job.PostProcessor,
        "post_processor_args": job.PostProcessorArgs,
        "split_output": job.SplitOutput,
        "order_output_by": job.OrderOutputBy,
        "fixtures": list(job.Fixtures),
        "output": job.PostProcessorOutputFile,
        "rotation_center": rotation_center_status(job),
        "tools": [controller_data(tc) for tc in job.Tools.Group],
        "operations": [
            {
                "name": op.Name,
                "type": op.TypeId,
                "active": bool(PathUtil.activeForOp(op)),
                **operation_path_status(op),
            }
            for op in job.Operations.Group
        ],
    }


def setup_sheet_data(job):
    sheet = job.SetupSheet
    if sheet is None:
        raise ValueError("Job has no SetupSheet")
    return {
        "job": job.Name,
        "setup_sheet": sheet.Name,
        "properties": {
            key: cam_json_value(getattr(sheet, key))
            for key in sheet.PropertiesList
            if key != "Proxy"
        },
        "schema": [
            {
                "name": key,
                "type": sheet.getTypeIdOfProperty(key),
                "description": sheet.getDocumentationOfProperty(key),
                "enum": sheet.getEnumerationsOfProperty(key)
                if sheet.getTypeIdOfProperty(key) == "App::PropertyEnumeration"
                else None,
            }
            for key in sheet.PropertiesList
            if key != "Proxy"
        ],
    }


def edit_setup_sheet(job_name, parameters, recompute):
    job = operation_job(job_name)
    sheet = job.SetupSheet
    if sheet is None:
        raise ValueError("Job has no SetupSheet")
    values = dict(parameters)
    for key, value in list(values.items()):
        if key not in sheet.PropertiesList:
            raise ValueError("Unknown SetupSheet property: " + key)
        if sheet.getTypeIdOfProperty(key) == "App::PropertySpeed" and type(value) in (
            int,
            float,
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError("Rapid speed must be finite and nonnegative")
            values[key] = str(value) + " mm/min"
    applied = apply_parameters(sheet, values)
    if recompute:
        job.Document.recompute()
        invalid = [
            obj.Name
            for obj in [sheet] + list(job.Operations.Group)
            if not obj.isValid()
        ]
        if invalid:
            raise ValueError(
                "Native SetupSheet/operation recompute failed: " + str(invalid)
            )
    result = setup_sheet_data(job)
    result.update(
        applied=applied,
        recomputed=recompute,
        scope="linked operation expressions update on recompute; creation-default fields apply to new operations",
    )
    return result


def job_path_objects(job):
    """Include hidden bases under dressups and nested operation compounds."""
    objects = []
    visited = set()

    def visit(obj):
        if obj is None or obj.Name in visited:
            return
        visited.add(obj.Name)
        if hasattr(obj, "Path"):
            objects.append(obj)
        for child in getattr(obj, "Group", []):
            visit(child)
        base = getattr(obj, "Base", None)
        if hasattr(base, "TypeId") and base.isDerivedFrom("Path::Feature"):
            visit(base)

    if hasattr(job, "Path"):
        objects.append(job)
    visit(job.Operations)
    return objects


def rotation_center_status(job):
    target = (
        job.MCPRotationCenter if "MCPRotationCenter" in job.PropertiesList else None
    )
    paths = []
    for obj in job_path_objects(job):
        center = obj.Path.Center
        paths.append(
            {
                "name": obj.Name,
                "center": list(center),
                "matches_requested": (center - target).Length < 1e-9
                if target is not None
                else None,
            }
        )
    return {
        "job": job.Name,
        "requested_center": list(target) if target is not None else None,
        "paths": paths,
        "consistent": all(row["matches_requested"] for row in paths)
        if target is not None
        else None,
        "scope": "native Path.Center metadata; does not move models or rewrite G-code commands",
        "persistence": "requested center persists as a Job property; native regeneration/reopen may reset Path.Center; reapply with cam_set_center_of_rotation",
    }


def set_rotation_center(job_name, coordinates):
    job = operation_job(job_name)
    if any(
        type(value) not in (int, float) or not math.isfinite(value)
        for value in coordinates
    ):
        raise ValueError("Rotation center coordinates must be finite numbers in mm")
    center = App.Vector(*coordinates)
    # Complete pending regeneration before setting the Path metadata. Native
    # operations construct new Path values during execute, resetting Center.
    job.Document.recompute()
    objects = job_path_objects(job)
    for obj in objects:
        if not obj.isValid():
            raise ValueError("Invalid CAM object: " + obj.Name)
        proxy = getattr(obj, "Proxy", None)
        if type(proxy).__module__ == "Path.Dressup.Gui.AxisMap":
            derived = proxy.center(obj)
            if (derived - center).Length > 1e-9:
                raise ValueError(
                    "Requested center conflicts with AxisMap Radius on " + obj.Name
                )
    before = {obj.Name: obj.Path.toGCode() for obj in objects}
    if "MCPRotationCenter" not in job.PropertiesList:
        job.addProperty(
            "App::PropertyVector",
            "MCPRotationCenter",
            "MCP",
            "Requested native Path rotation center in mm",
        )
    job.MCPRotationCenter = center
    for obj in objects:
        # Native setCenterOfRotation assumes Operations.Path exists; current
        # operations groups do not have it. Reassign copies for native Undo.
        path = obj.Path.copy()
        path.Center = center
        obj.Path = path
    result = rotation_center_status(job)
    if not result["consistent"]:
        raise ValueError("Native paths did not retain the requested center")
    if any(obj.Path.toGCode() != before[obj.Name] for obj in objects):
        raise ValueError("Setting rotation metadata unexpectedly changed G-code")
    result.update(
        center=list(center),
        commands_modified=False,
        models_modified=False,
        backend="native Path.Center on Job, nested operations and dressup bases",
    )
    return result
