"""Native Job model clones and machining setup geometry transformations."""

import math
import re
from typing import TYPE_CHECKING

import FreeCAD as App
import Part
import Path.Main.Job as PathJob

if TYPE_CHECKING:
    from .cam_operations import operation_job, operation_path_status
    from .cam_jobs import cam_json_value, job_path_objects, rotation_center_status
    from .cam_stock import job_models, stock_kind, stock_data


def setup_bounds(objects):
    bounds = App.BoundBox()
    for obj in objects:
        if not hasattr(obj, "Shape") or obj.Shape.isNull() or not obj.Shape.isValid():
            raise ValueError("Invalid setup geometry: " + obj.Name)
        bounds.add(obj.Shape.optimalBoundingBox(False, False))
    if not bounds.isValid():
        raise ValueError("Job has no model geometry")
    return bounds


def setup_data(job):
    models = []
    for model in job.Model.Group:
        source = job.Proxy.baseObject(job, model)
        bounds = setup_bounds([model])
        models.append(
            {
                "name": model.Name,
                "source": source.Name,
                "source_document": source.Document.Name,
                "native_resource_clone": PathJob.isResourceClone(job, model, "Model"),
                "placement": cam_json_value(model.Placement),
                "source_placement": cam_json_value(source.Placement),
                "bounds": {
                    "min": [bounds.XMin, bounds.YMin, bounds.ZMin],
                    "max": [bounds.XMax, bounds.YMax, bounds.ZMax],
                },
            }
        )
    return {
        "job": job.Name,
        "models": models,
        "stock": stock_data(job) if job.Stock else None,
        "operations": [
            {"name": obj.Name, **operation_path_status(obj)}
            for obj in job.Operations.Group
        ],
        "rotation_center": rotation_center_status(job),
        "coordinate_space": "document mm; clone Placements define machining setup",
        "wcs": list(job.Fixtures),
        "stock_coverage_verified": False,
    }


def refit_setup_stock(job):
    if not job.Stock or stock_kind(job.Stock) != "FromBase":
        raise ValueError("Stock refit requires native FromBase stock")
    bounds = setup_bounds(list(job.Model.Group))
    job.Stock.Placement = App.Placement(
        App.Vector(bounds.XMin, bounds.YMin, bounds.ZMin), App.Rotation()
    )
    job.Stock.touch()


def regenerate_setup(job):
    # Job.Model is a plain group: changing a clone placement does not reliably
    # mark automatic-base operations as touched. Mark all nested native paths.
    if job.Stock:
        job.Stock.touch()
    for obj in job_path_objects(job):
        obj.touch()
    job.Document.recompute()
    checked = list(job.Model.Group) + job_path_objects(job)
    if job.Stock:
        checked.append(job.Stock)
    invalid = [obj.Name for obj in checked if not obj.isValid()]
    if invalid:
        raise ValueError("Setup recompute failed: " + ", ".join(invalid))
    setup_bounds(list(job.Model.Group))


def setup_vector(values, field):
    if (
        not isinstance(values, (list, tuple))
        or len(values) != 3
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
    ):
        raise ValueError(field + " requires three finite coordinates")
    return App.Vector(*values)


def setup_reference(job, object_name, element, point_mode, direction_mode, parameters):
    """Resolve geometry in the native Job clone/stock document frame."""
    obj = job.Document.getObject(object_name)
    if obj is None or (obj not in job.Model.Group and obj is not job.Stock):
        raise ValueError("Reference must be a model clone or stock inside this Job")
    if point_mode not in ("auto", "center_of_mass", "bounds_center", "parameter"):
        raise ValueError(
            "point_mode must be auto, center_of_mass, bounds_center or parameter"
        )
    if direction_mode not in ("auto", "normal", "tangent", "axis", "none"):
        raise ValueError("direction_mode must be auto, normal, tangent, axis or none")
    if element and not re.fullmatch(r"(?:Face|Edge|Vertex)[1-9][0-9]*", element):
        raise ValueError("subelement must be a one-based FaceN, EdgeN or VertexN")
    setup_bounds([obj])
    shape = obj.Shape.getElement(element) if element else obj.Shape
    if shape.isNull():
        raise ValueError("Reference geometry is empty")
    parameters = list(parameters)
    if len(parameters) != 2 or any(
        type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1
        for p in parameters
    ):
        raise ValueError("parameters requires two normalized numbers in [0,1]")
    point = None
    direction = None
    direction_kind = None
    point_kind = point_mode
    if shape.ShapeType == "Vertex":
        point = shape.Point
        point_kind = "vertex"
    elif point_mode == "bounds_center":
        point = shape.optimalBoundingBox(False, False).Center
    elif point_mode == "center_of_mass":
        point = shape.CenterOfMass
    elif point_mode == "auto":
        if shape.ShapeType == "Edge" and isinstance(
            shape.Curve, (Part.Circle, Part.Ellipse)
        ):
            point = shape.Curve.Center
            point_kind = "curve_center"
        else:
            point = shape.CenterOfMass
            point_kind = "center_of_mass"
    elif shape.ShapeType == "Edge":
        first, last = shape.ParameterRange
        point = shape.valueAt(first + (last - first) * parameters[0])
    elif shape.ShapeType == "Face":
        a, b, c, d = shape.ParameterRange
        u, v = a + (b - a) * parameters[0], c + (d - c) * parameters[1]
        if not shape.isPartOfDomain(u, v):
            raise ValueError("Selected UV point lies outside the trimmed face")
        point = shape.valueAt(u, v)
    else:
        raise ValueError("Parameter point requires an edge or face")
    if direction_mode != "none":
        mode = direction_mode
        if mode == "auto":
            if shape.ShapeType == "Face":
                mode = "normal" if isinstance(shape.Surface, Part.Plane) else "axis"
            elif shape.ShapeType == "Edge":
                mode = (
                    "axis"
                    if isinstance(shape.Curve, (Part.Circle, Part.Ellipse))
                    else "tangent"
                )
            else:
                mode = "none"
        if mode == "normal" and shape.ShapeType == "Face":
            a, b, c, d = shape.ParameterRange
            u, v = a + (b - a) * parameters[0], c + (d - c) * parameters[1]
            if not isinstance(shape.Surface, Part.Plane) and not shape.isPartOfDomain(
                u, v
            ):
                raise ValueError("Normal reference lies outside the trimmed face")
            du, dv = shape.derivative1At(u, v)
            if (
                du.Length * dv.Length <= 1e-20
                or du.cross(dv).Length <= 1e-12 * du.Length * dv.Length
            ):
                raise ValueError("Normal is undefined at a surface singularity")
            direction = shape.normalAt(u, v)
        elif mode == "tangent" and shape.ShapeType == "Edge":
            first, last = shape.ParameterRange
            direction = shape.tangentAt(first + (last - first) * parameters[0])
            if shape.Orientation == "Reversed":
                direction = -direction
        elif mode == "axis" and shape.ShapeType in ("Edge", "Face"):
            geometry = shape.Curve if shape.ShapeType == "Edge" else shape.Surface
            allowed = (
                (Part.Circle, Part.Ellipse)
                if shape.ShapeType == "Edge"
                else (Part.Cylinder, Part.Cone, Part.Toroid)
            )
            if not isinstance(geometry, allowed):
                raise ValueError(
                    "Geometry has no unique supported axis; select normal/tangent explicitly"
                )
            direction = geometry.Axis
        elif mode != "none":
            raise ValueError("Direction mode does not apply to the selected geometry")
        if direction is not None:
            if not math.isfinite(direction.Length) or direction.Length < 1e-14:
                raise ValueError("Reference direction is singular")
            direction = direction / direction.Length
            direction_kind = mode
    return {
        "object": obj.Name,
        "subelement": element,
        "shape_type": shape.ShapeType,
        "point": list(point),
        "point_kind": point_kind,
        "direction": list(direction) if direction is not None else None,
        "direction_kind": direction_kind,
        "parameters": parameters,
        "coordinate_space": "document",
        "length_unit": "mm",
    }


def align_setup(
    job_name,
    object_name,
    element,
    target_axis,
    direction_mode,
    parameters,
    pivot,
    model_names,
    stock_mode,
):
    job = operation_job(job_name)
    reference = setup_reference(
        job, object_name, element, "auto", direction_mode, parameters
    )
    if reference["direction"] is None:
        raise ValueError("Alignment requires a direction-bearing edge or face")
    target = setup_vector(target_axis, "target_axis")
    if target.Length < 1e-14:
        raise ValueError("target_axis cannot be zero")
    target = target / target.Length
    rotation = App.Rotation(App.Vector(*reference["direction"]), target)
    result = transform_setup(
        job_name,
        model_names,
        [0, 0, 0],
        list(rotation.Axis),
        math.degrees(rotation.Angle),
        pivot,
        stock_mode,
    )
    result.update(
        reference=reference,
        target_axis=list(target),
        alignment="shortest rotation; twist is retained when already aligned",
    )
    after = setup_reference(
        job, object_name, element, "auto", direction_mode, parameters
    )
    result["reference_after"] = after
    # A stock reference may remain fixed under keep/refit. A model reference
    # must actually participate in the selected setup transform.
    if object_name in result["transformed_models"]:
        if App.Vector(*after["direction"]).dot(target) < 1 - 1e-9:
            raise ValueError("Native setup did not retain the requested alignment")
    return result


def setup_axes(axes):
    if (
        not isinstance(axes, str)
        or not axes
        or len(set(axes)) != len(axes)
        or set(axes) - set("XYZ")
    ):
        raise ValueError("axes must select unique uppercase XYZ axes")
    return axes


def set_setup_origin(
    job_name,
    object_name,
    element,
    point,
    target,
    axes,
    point_mode,
    parameters,
    model_names,
    stock_mode,
):
    job = operation_job(job_name)
    axes = setup_axes(axes)
    reference = None
    if point is None:
        reference = setup_reference(
            job, object_name, element, point_mode, "none", parameters
        )
        origin = App.Vector(*reference["point"])
    else:
        if object_name or element:
            raise ValueError("Provide either an explicit point or a geometry reference")
        origin = setup_vector(point, "point")
    target = setup_vector(target, "target")
    delta = target - origin
    for axis in "XYZ":
        if axis not in axes:
            setattr(delta, axis.lower(), 0)
    result = transform_setup(
        job_name, model_names, list(delta), [0, 0, 1], 0, [0, 0, 0], stock_mode
    )
    return result | {
        "origin_point": list(origin),
        "target": list(target),
        "axes": axes,
        "reference": reference,
    }


def center_setup_in_stock(job_name, model_names, axes):
    job = operation_job(job_name)
    axes = setup_axes(axes)
    if job.Stock is None or stock_kind(job.Stock) == "FromBase":
        raise ValueError(
            "Centering requires explicit stock; FromBase follows the model bounds"
        )
    names = (
        model_names if model_names is not None else [o.Name for o in job.Model.Group]
    )
    if not names or any(
        job.Document.getObject(name) not in job.Model.Group for name in names
    ):
        raise ValueError("Select native Job model clones")
    center = setup_bounds([job.Document.getObject(name) for name in names]).Center
    target = setup_bounds([job.Stock]).Center
    return set_setup_origin(
        job_name,
        "",
        "",
        list(center),
        list(target),
        axes,
        "auto",
        [0.5, 0.5],
        names,
        "keep",
    )


def transform_setup(
    job_name, model_names, translation, axis, angle, center, stock_mode
):
    job = operation_job(job_name)
    if stock_mode not in ("auto", "keep", "refit", "transform"):
        raise ValueError("stock_mode must be auto, keep, refit or transform")
    translation = setup_vector(translation, "translation")
    axis = setup_vector(axis, "rotation_axis")
    center = setup_vector(center, "rotation_center")
    if (
        type(angle) not in (int, float)
        or not math.isfinite(angle)
        or axis.Length < 1e-14
    ):
        raise ValueError("Rotation requires finite degrees and a nonzero axis")
    names = (
        model_names
        if model_names is not None
        else [obj.Name for obj in job.Model.Group]
    )
    if not names or len(set(names)) != len(names):
        raise ValueError("Select unique Job model clone names")
    models = [job.Document.getObject(name) for name in names]
    if any(
        obj is None
        or obj not in job.Model.Group
        or not PathJob.isResourceClone(job, obj, "Model")
        for obj in models
    ):
        raise ValueError(
            "Each model_name must be a native model clone inside this Job; inspect setup for clone names"
        )
    for obj in models:
        if str(getattr(obj, "MapMode", "Deactivated")) != "Deactivated" or any(
            path == "Placement" or path.startswith("Placement.")
            for path, _ in obj.ExpressionEngine
        ):
            raise ValueError(
                "Clone placement is controlled by attachment/expression: " + obj.Name
            )
    rotation = App.Rotation(axis, angle)
    delta = App.Placement(translation + center - rotation.multVec(center), rotation)
    effective = stock_mode
    if effective == "auto":
        effective = (
            "refit"
            if job.Stock and stock_kind(job.Stock) == "FromBase"
            else "transform"
        )
    if effective != "keep" and job.Stock is None:
        raise ValueError("Job has no stock; select stock_mode=keep")
    if effective == "refit" and stock_kind(job.Stock) != "FromBase":
        raise ValueError("Stock refit requires FromBase stock")
    if (
        effective == "transform"
        and stock_kind(job.Stock) == "FromBase"
        and rotation.Angle > 1e-12
    ):
        raise ValueError(
            "FromBase dimensions use axis-aligned bounds; use refit for rotated models"
        )
    before = {obj.Name: obj.Placement.copy() for obj in models}
    sources = {job.Proxy.baseObject(job, obj) for obj in models}
    source_placements = {obj: obj.Placement.copy() for obj in sources}
    for obj in models:
        obj.Placement = delta * obj.Placement
    job.Document.recompute()
    if effective == "refit":
        refit_setup_stock(job)
    elif effective == "transform":
        job.Stock.Placement = delta * job.Stock.Placement
    regenerate_setup(job)
    for obj, placement in source_placements.items():
        if not obj.Placement.isSame(placement, 1e-10):
            raise ValueError(
                "Native setup unexpectedly moved design source: " + obj.Name
            )
    for obj in models:
        if not obj.Placement.isSame(delta * before[obj.Name], 1e-10):
            raise ValueError("Native clone did not retain requested transform")
    result = setup_data(job)
    result.update(
        transformed_models=names,
        delta=cam_json_value(delta),
        stock_mode=effective,
        source_placements_modified=False,
        recomputed=True,
    )
    return result


def add_setup_models(job_name, source_names, refit_stock):
    job = operation_job(job_name)
    if not source_names:
        raise ValueError("Provide source model names")
    if type(refit_stock) is not bool:
        raise TypeError("refit_stock requires a boolean")
    # Native Job allows multiple setup instances of one source.
    sources = [job_models(job.Document, [name])[0] for name in source_names]
    if refit_stock and stock_kind(job.Stock) != "FromBase":
        raise ValueError("refit_stock requires FromBase stock")
    clones = []
    for source in sources:
        clone = PathJob.createModelResourceClone(job, source)
        job.Model.addObject(clone)
        clones.append(clone)
    job.Document.recompute()
    if refit_stock:
        refit_setup_stock(job)
    regenerate_setup(job)
    return setup_data(job) | {"added_models": [obj.Name for obj in clones]}


def remove_setup_model(job_name, model_name, refit_stock):
    job = operation_job(job_name)
    if type(refit_stock) is not bool:
        raise TypeError("refit_stock requires a boolean")
    model = job.Document.getObject(model_name)
    if (
        model is None
        or model not in job.Model.Group
        or not PathJob.isResourceClone(job, model, "Model")
    ):
        raise ValueError("Native Job model clone required")
    if len(job.Model.Group) <= 1:
        raise ValueError("Job must retain at least one model")
    users = [obj.Name for obj in model.InList if obj is not job.Model]
    if users:
        raise ValueError(
            "Model clone is referenced; update dependent objects before removal: "
            + ", ".join(users)
        )
    if refit_stock and stock_kind(job.Stock) != "FromBase":
        raise ValueError("refit_stock requires FromBase stock")
    source = job.Proxy.baseObject(job, model)
    source_name = source.Name
    job.Proxy.removeBase(job, model, True)
    job.Document.recompute()
    if refit_stock:
        refit_setup_stock(job)
    regenerate_setup(job)
    if job.Document.getObject(source_name) is not source:
        raise ValueError("Native removal unexpectedly deleted design source")
    return setup_data(job) | {
        "removed_model": model_name,
        "retained_source": source_name,
    }
