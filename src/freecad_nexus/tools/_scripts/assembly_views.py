"""Native exploded-view objects with deterministic placement/preview calculation."""

import math
from typing import TYPE_CHECKING

import FreeCAD as App
import Part
import CommandCreateView as NativeView
import UtilsAssembly

if TYPE_CHECKING:
    from .assembly_joints import native_assembly, assembly_explicit_solve
    from .assembly_state import json_value


def native_exploded_view(name):
    doc = App.ActiveDocument
    view = doc.getObject(name) if doc else None
    if view is None or not isinstance(
        getattr(view, "Proxy", None), NativeView.ExplodedView
    ):
        raise ValueError("Native exploded view not found: " + name)
    assembly = view.Proxy.getAssembly(view)
    if assembly is None:
        raise ValueError("Exploded view has no Assembly")
    return view, assembly


def view_members(assembly):
    """Resolve instance-owned Group paths; AssemblyLink children are real copies.

    FreeCAD AssemblyLink generates names such as Child001, not the linked
    source's Child. Enumerate those instances instead of resolving source
    objects by name. Coordinates in this map are relative to the assembly.
    """
    nodes = {}

    def visit(container, prefix, parent_placement, parent_visible, ancestors):
        if container in ancestors:
            raise ValueError("Cyclic Assembly hierarchy")
        for obj in getattr(container, "Group", []):
            if not hasattr(obj, "Placement"):
                if obj.TypeId == "App::DocumentObjectGroup":
                    visit(
                        obj,
                        prefix + obj.Name + ".",
                        parent_placement,
                        parent_visible,
                        ancestors + [container],
                    )
                continue
            if obj.isDerivedFrom("Assembly::JointGroup") or obj.TypeId in (
                "Assembly::ViewGroup",
                "Assembly::SimulationGroup",
                "Assembly::BomGroup",
            ):
                continue
            path = prefix + obj.Name + "."
            placement = parent_placement * obj.Placement
            visible = parent_visible and bool(getattr(obj, "Visibility", True))
            if hasattr(container, "isElementVisible"):
                visible = visible and container.isElementVisible(obj.Name) != 0
            node = {
                "object": obj,
                "path": path,
                "placement": placement,
                "visible": visible,
                "shape": None,
            }
            nodes[path] = node
            if obj.isDerivedFrom("App::Part"):
                visit(obj, path, placement, visible, ancestors + [container])
            else:
                shape = Part.getShape(obj).copy()
                if not shape.isNull():
                    # getShape includes obj.Placement but not parent containers.
                    shape.Placement = parent_placement * shape.Placement
                    node["shape"] = shape
            # Empty origins/containers have no material and are not targets.
            if node["shape"] is None and not any(
                p.startswith(path) and p != path for p in nodes
            ):
                del nodes[path]

    visit(assembly, "", App.Placement(), True, [])
    return nodes


def exploded_paths(assembly, paths, nodes=None):
    nodes = view_members(assembly) if nodes is None else nodes
    if not paths:
        raise ValueError("Select at least one component path")
    normalized = []
    for path in paths:
        if not isinstance(path, str):
            raise TypeError("Component paths must be strings")
        path = path if path.endswith(".") else path + "."
        if path not in nodes:
            raise ValueError("Component instance path not in Assembly: " + path)
        normalized.append(path)
    if len(set(normalized)) != len(normalized):
        raise ValueError("Duplicate component path")
    if any(p != q and q.startswith(p) for p in normalized for q in normalized):
        raise ValueError(
            "Do not select a component and its descendant in the same step"
        )
    return normalized


def view_number(value, field):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError(field + " must be finite")
    return float(value)


def view_vector(value, field):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(field + " must contain three coordinates")
    return App.Vector(*[view_number(v, field) for v in value])


def view_move_type(value):
    aliases = {
        "translate": "Normal",
        "rotate": "Normal",
        "normal": "Normal",
        "radial": "Radial",
    }
    if value.lower() not in aliases:
        raise ValueError("Move type must be Normal, Translate, Rotate or Radial")
    return aliases[value.lower()]


def view_transform(translation, axis, angle, center):
    translation = view_vector(translation, "translation")
    axis = view_vector(axis, "rotation_axis")
    center = view_vector(center, "rotation_center")
    angle = view_number(angle, "rotation_angle")
    if axis.Length < 1e-14:
        raise ValueError("Rotation axis must be nonzero")
    rotation = App.Rotation(axis, angle)
    return App.Placement(translation + center - rotation.multVec(center), rotation)


def remember_view_pivot(step, center):
    # A Placement records the endpoint transform, not the rotation center.
    # Store the requested pivot separately for physically correct partial
    # steps, guarded by the endpoint signature against later native GUI edits.
    for name, kind in [
        ("MCPRotationCenter", "App::PropertyVector"),
        ("MCPInterpolationTransform", "App::PropertyPlacement"),
    ]:
        if name not in step.PropertiesList:
            step.addProperty(kind, name, "MCP animation")
            step.setEditorMode(name, 2)
    step.MCPRotationCenter = view_vector(center, "rotation_center")
    step.MCPInterpolationTransform = step.MovementTransform


def view_pivot(step):
    if all(
        name in step.PropertiesList
        for name in ("MCPRotationCenter", "MCPInterpolationTransform")
    ):
        if step.MovementTransform.isSame(step.MCPInterpolationTransform, 1e-12):
            return step.MCPRotationCenter
    return None


def interpolate_view_transform(step, fraction):
    move = step.MovementTransform
    rotation = App.Rotation().slerp(move.Rotation, fraction)
    center = view_pivot(step)
    if center is None:
        # Native/imported steps expose an endpoint only; a pivot cannot be
        # uniquely recovered from arbitrary translation+rotation.
        return App.Placement(move.Base * fraction, rotation)
    translation = move.Base - center + move.Rotation.multVec(center)
    return App.Placement(
        translation * fraction + center - rotation.multVec(center), rotation
    )


def exploded_step_data(step, view=None):
    refs = step.References
    return {
        "step": step.Name,
        "view": view.Name if view else None,
        "label": step.Label,
        "move_type": step.MoveType,
        "references": json_value(refs),
        "component_paths": list(refs[1]) if refs and refs[0] else [],
        "transform": json_value(step.MovementTransform),
        "vector": list(step.MovementTransform.Base),
        "applied": False,
        "rotation_center": json_value(view_pivot(step)),
        "interpolation": "pivot rotation with linear translation"
        if view_pivot(step) is not None
        else "endpoint translation and rotation; native pivot unavailable",
    }


def create_exploded_view(assembly_name, name):
    assembly = native_assembly(assembly_name)
    view = UtilsAssembly.getViewGroup(assembly).newObject("App::FeaturePython", name)
    NativeView.ExplodedView(view)
    NativeView.ViewProviderExplodedView(view.ViewObject)
    assembly.Document.recompute()
    return {"name": view.Name, "type": view.TypeId, "assembly": assembly.Name}


def create_exploded_step(
    view_name, paths, translation, axis, angle, center, kind, name
):
    view, assembly = native_exploded_view(view_name)
    paths = exploded_paths(assembly, paths)
    kind = view_move_type(kind)
    if kind == "Radial" and angle != 0:
        raise ValueError("Radial moves do not apply rotation")
    transform = view_transform(translation, axis, angle, center)
    with assembly_explicit_solve():
        step = view.newObject("App::FeaturePython", name or "Step")
        NativeView.ExplodedViewStep(step, NativeView.ExplodedViewStepTypes.index(kind))
        NativeView.ViewProviderExplodedViewStep(step.ViewObject)
        step.References = [assembly, paths]
        step.MovementTransform = transform
        remember_view_pivot(step, center)
        assembly.Document.recompute()
    result = exploded_step_data(step, view)
    result["component"] = paths[0].rstrip(".") if len(paths) == 1 else None
    return result


def find_exploded_step(name):
    doc = App.ActiveDocument
    step = doc.getObject(name) if doc else None
    if not step or not isinstance(
        getattr(step, "Proxy", None), NativeView.ExplodedViewStep
    ):
        raise ValueError("Native exploded step not found")
    views = [
        v
        for v in step.InList
        if isinstance(getattr(v, "Proxy", None), NativeView.ExplodedView)
        and step in v.Group
    ]
    if len(views) != 1:
        raise ValueError("Step must belong to one exploded view")
    return step, views[0]


def edit_exploded_step(name, paths, kind, transform, label):
    step, view = find_exploded_step(name)
    _, assembly = native_exploded_view(view.Name)
    if paths is not None:
        paths = exploded_paths(assembly, paths)
    kind = view_move_type(kind) if kind is not None else step.MoveType
    placement = step.MovementTransform
    if transform is not None:
        if not isinstance(transform, dict) or set(transform) - {
            "translation",
            "rotation_axis",
            "rotation_angle",
            "rotation_center",
        }:
            raise ValueError("Unknown transform field")
        # Explicit transform is a complete replacement; omitted edit field
        # preserves the complete original MovementTransform.
        placement = view_transform(
            transform.get("translation", [0, 0, 0]),
            transform.get("rotation_axis", [0, 0, 1]),
            transform.get("rotation_angle", 0),
            transform.get("rotation_center", [0, 0, 0]),
        )
    if kind == "Radial" and abs(placement.Rotation.Angle) > 1e-12:
        raise ValueError("Radial moves do not apply rotation")
    if label is not None and not isinstance(label, str):
        raise TypeError("label must be a string")
    with assembly_explicit_solve():
        if paths is not None:
            step.References = [assembly, paths]
        step.MoveType = kind
        step.MovementTransform = placement
        if transform is not None:
            remember_view_pivot(step, transform.get("rotation_center", [0, 0, 0]))
        if label is not None:
            step.Label = label
        assembly.Document.recompute()
    return exploded_step_data(step, view)


def inspect_exploded_view(name):
    view, assembly = native_exploded_view(name)
    nodes = view_members(assembly)
    steps = []
    for step in view.Group:
        if not isinstance(getattr(step, "Proxy", None), NativeView.ExplodedViewStep):
            steps.append(
                {
                    "step": step.Name,
                    "valid": False,
                    "error": "Not a native exploded step",
                }
            )
            continue
        data = exploded_step_data(step, view)
        try:
            validate_view_step(step, assembly, nodes)
            data["valid"] = True
        except Exception as exc:
            data.update(valid=False, error=str(exc))
        steps.append(data)
    return {
        "view": view.Name,
        "assembly": assembly.Name,
        "steps": steps,
        "components": [
            {
                "path": p,
                "object": n["object"].Name,
                "type": n["object"].TypeId,
                "visible": n["visible"],
                "placement": json_value(n["placement"]),
                "has_shape": n["shape"] is not None,
            }
            for p, n in nodes.items()
        ],
        "coordinate_space": "assembly",
        "valid": all(s["valid"] for s in steps),
    }


def validate_view_step(step, assembly, nodes):
    if not isinstance(getattr(step, "Proxy", None), NativeView.ExplodedViewStep):
        raise ValueError("View contains non-native step")
    refs = step.References
    if not refs or refs[0] is not assembly:
        raise ValueError("Step reference root must be this Assembly")
    paths = exploded_paths(assembly, list(refs[1]), nodes)
    if step.MoveType not in NativeView.ExplodedViewStepTypes:
        raise ValueError("Invalid native move type")
    if any(
        not math.isfinite(x)
        for x in list(step.MovementTransform.Base)
        + list(step.MovementTransform.Rotation.Q)
    ):
        raise ValueError("Nonfinite step transform")
    return paths


def exploded_calculation(name, progress, include_lines, include_hidden):
    """Read native step transforms; compose instance geometry without mutation.

    Native getExplodedShape overwrites each shape placement and its line starts
    refer to initial geometry even after previous steps. Compose deltas with
    original shape placements and recalculate each successive line start.
    Progress is [0,step_count], with fractional steps interpolated via slerp.
    """
    view, assembly = native_exploded_view(name)
    nodes = view_members(assembly)
    steps = list(view.Group)
    progress = len(steps) if progress is None else view_number(progress, "progress")
    if not 0 <= progress <= len(steps):
        raise ValueError("progress must be between zero and step_count")
    refs = [validate_view_step(step, assembly, nodes) for step in steps]
    initial_bounds = App.BoundBox()
    for node in nodes.values():
        if node["shape"] is not None and (include_hidden or node["visible"]):
            initial_bounds.add(node["shape"].optimalBoundingBox(False, False))
    if not initial_bounds.isValid():
        raise ValueError(
            "Assembly has no geometry under the requested visibility filter"
        )
    center, size = initial_bounds.Center, initial_bounds.DiagonalLength
    # Deltas are indexed by complete instance paths, never by source names.
    deltas = {path: App.Placement() for path in nodes}
    lines = []

    def bounds_for(path):
        bounds = App.BoundBox()
        for key, node in nodes.items():
            if (key == path or key.startswith(path)) and node["shape"] is not None:
                shape = node["shape"].copy()
                shape.Placement = deltas[key] * shape.Placement
                bounds.add(shape.optimalBoundingBox(False, False))
        if not bounds.isValid():
            raise ValueError("Selected component contains no geometry: " + path)
        return bounds

    for index, (step, paths) in enumerate(zip(steps, refs)):
        fraction = max(0, min(1, progress - index))
        if fraction == 0:
            break
        move = step.MovementTransform
        if step.MoveType == "Radial" and size <= 1e-14:
            raise ValueError("Radial explosion requires nonzero assembly bounds")
        for path in paths:
            start = bounds_for(path).Center
            if step.MoveType == "Radial":
                delta = App.Placement(
                    (start - center) * (4 * move.Base.Length / size * fraction),
                    App.Rotation(),
                )
            else:
                delta = interpolate_view_transform(step, fraction)
            for key in deltas:
                if key == path or key.startswith(path):
                    deltas[key] = delta * deltas[key]
            end = bounds_for(path).Center
            lines.append(
                {"step": step.Name, "component_path": path, "start": start, "end": end}
            )
    global_placement = assembly.getGlobalPlacement()
    shapes = []
    components = []
    for path, node in nodes.items():
        if node["shape"] is None or not (include_hidden or node["visible"]):
            continue
        shape = node["shape"].copy()
        shape.Placement = global_placement * deltas[path] * shape.Placement
        shapes.append(shape)
        bounds = shape.optimalBoundingBox(False, False)
        components.append(
            {
                "path": path,
                "object": node["object"].Name,
                "visible": node["visible"],
                "placement": json_value(
                    global_placement * deltas[path] * node["placement"]
                ),
                "bounds": {
                    "min": [bounds.XMin, bounds.YMin, bounds.ZMin],
                    "max": [bounds.XMax, bounds.YMax, bounds.ZMax],
                },
            }
        )
    line_data = []
    for line in lines:
        if not any(
            (path == line["component_path"] or path.startswith(line["component_path"]))
            and node["shape"] is not None
            and (include_hidden or node["visible"])
            for path, node in nodes.items()
        ):
            continue
        start = global_placement.multVec(line["start"])
        end = global_placement.multVec(line["end"])
        line_data.append(
            {
                "step": line["step"],
                "component_path": line["component_path"],
                "start": list(start),
                "end": list(end),
            }
        )
        if include_lines and (end - start).Length > 1e-7:
            shapes.append(Part.makeLine(start, end))
    if not shapes:
        raise ValueError("No exploded geometry to export")
    shape = Part.makeCompound(shapes)
    result = {
        "view": view.Name,
        "assembly": assembly.Name,
        "progress": progress,
        "step_count": len(steps),
        "components": components,
        "lines": line_data,
        "coordinate_space": "document",
        "assembly_placement": json_value(global_placement),
        "placements_modified": False,
        "calculation": "native step transforms with composed hierarchy and sequential line origins",
    }
    return shape, result


def export_exploded_shape(view_name, name, progress, include_lines, include_hidden):
    shape, result = exploded_calculation(
        view_name, progress, include_lines, include_hidden
    )
    if not shape.isValid():
        raise ValueError("Exploded geometry is invalid")
    out = App.ActiveDocument.addObject("Part::Feature", name)
    out.Shape = shape
    with assembly_explicit_solve():
        App.ActiveDocument.recompute()
    result.update(name=out.Name, valid=shape.isValid(), faces=len(shape.Faces))
    return result


def exploded_frames(view_name, frames_per_step, include_hidden):
    view, _ = native_exploded_view(view_name)
    if type(frames_per_step) is not int or not 1 <= frames_per_step <= 1000:
        raise ValueError("frames_per_step must be an integer from 1 to 1000")
    total = len(view.Group) * frames_per_step
    frames = []
    for index in range(total + 1):
        _, data = exploded_calculation(
            view_name, index / frames_per_step, False, include_hidden
        )
        frames.append(
            {
                "index": index,
                "progress": data["progress"],
                "components": data["components"],
            }
        )
    return {
        "view": view_name,
        "frames": frames,
        "frame_count": len(frames),
        "frames_per_step": frames_per_step,
        "coordinate_space": "document",
        "placements_modified": False,
        "interpolation": "stored pivot with linear translation and shortest-arc slerp; native endpoint fallback when pivot unknown",
    }


def reorder_exploded_steps(view_name, names):
    view, _ = native_exploded_view(view_name)
    lookup = {step.Name: step for step in view.Group}
    if len(names) != len(lookup) or set(names) != set(lookup):
        raise ValueError("step_names must contain every view step exactly once")
    view.Group = [lookup[name] for name in names]
    return {"view": view.Name, "steps": [step.Name for step in view.Group]}


def remove_exploded_step(name):
    step, view = find_exploded_step(name)
    record = exploded_step_data(step, view)
    view.Document.removeObject(step.Name)
    return {"removed_step": record["step"], "view": view.Name}


def remove_exploded_view(name):
    view, _ = native_exploded_view(name)
    names = [step.Name for step in view.Group]
    # Do not recursively delete references/assembly components.
    for step in list(view.Group):
        others = [
            v
            for v in step.InList
            if v is not view
            and isinstance(getattr(v, "Proxy", None), NativeView.ExplodedView)
        ]
        if others:
            raise ValueError("Step is shared with another exploded view")
    for step in names:
        view.Document.removeObject(step)
    view.Document.removeObject(view.Name)
    return {"removed_view": name, "removed_steps": names}
