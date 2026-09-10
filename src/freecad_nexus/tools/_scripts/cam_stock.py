"""Native Job model selection and stock lifecycle, with explicit dimensions."""

import math
from typing import TYPE_CHECKING

import FreeCAD as App
import Part
import Path.Base.Util as PathUtil
import Path.Main.Job as PathJob
import Path.Main.Stock as PathStock

if TYPE_CHECKING:
    from .cam_operations import operation_job
    from .cam_properties import apply_parameters
    from .cam_jobs import cam_json_value


def job_models(doc, names):
    if doc is None:
        raise ValueError("Open a document containing CAM model geometry first")
    if len(names) != len(set(names)):
        raise ValueError("Model names must be unique")

    def valid(obj):
        return (
            obj is not None
            and not hasattr(obj, "PathResource")
            and not isinstance(getattr(obj, "Proxy", None), PathStock.Stock)
            and PathUtil.isValidBaseObject(obj)
            and Part.getShape(obj).isValid()
        )

    models = (
        [doc.getObject(name) for name in names]
        if names
        else [obj for obj in doc.Objects if valid(obj)]
    )
    if not models or any(not valid(obj) for obj in models):
        raise ValueError(
            "Each CAM model must be existing, valid, top-level geometry (not stock or tooling)"
        )
    return models


def stock_kind(stock):
    if stock is None:
        return None
    if isinstance(stock.Proxy, PathStock.StockFromBase):
        return "FromBase"
    if isinstance(stock.Proxy, PathStock.StockCreateBox):
        return "Box"
    if isinstance(stock.Proxy, PathStock.StockCreateCylinder):
        return "Cylinder"
    return "Existing"


def stock_placement(value):
    if not isinstance(value, dict) or set(value) - {"base", "rotation"}:
        raise ValueError(
            "placement requires base [x,y,z] and rotation quaternion [x,y,z,w]"
        )
    base, rotation = value.get("base", [0, 0, 0]), value.get("rotation", [0, 0, 0, 1])
    if (
        len(base) != 3
        or len(rotation) != 4
        or any(
            type(x) not in (int, float) or not math.isfinite(x)
            for x in list(base) + list(rotation)
        )
    ):
        raise ValueError("Placement coordinates and quaternion must be finite")
    if sum(v * v for v in rotation) < 1e-24:
        raise ValueError("Rotation quaternion must be nonzero")
    return App.Placement(App.Vector(*base), App.Rotation(*rotation))


def stock_data(job):
    stock = job.Stock
    box = stock.Shape.optimalBoundingBox(False, False)
    kind = stock_kind(stock)
    keys = {
        "FromBase": ["Ext" + axis + side for axis in "XYZ" for side in ("neg", "pos")],
        "Box": ["Length", "Width", "Height"],
        "Cylinder": ["Radius", "Height"],
        "Existing": [],
    }[kind]
    return {
        "job": job.Name,
        "stock": stock.Name,
        "type": stock.TypeId,
        "stock_type": kind,
        "dimensions": {key: cam_json_value(getattr(stock, key)) for key in keys},
        "placement": cam_json_value(stock.Placement),
        "volume": stock.Shape.Volume,
        "bounds": {
            "min": [box.XMin, box.YMin, box.ZMin],
            "max": [box.XMax, box.YMax, box.ZMax],
        },
        "source_objects": [obj.Name for obj in getattr(stock, "Objects", [])],
    }


def configure_stock(job, kind, dimensions, source_name="", placement=None):
    aliases = {
        "automatic": "Automatic",
        "frombase": "FromBase",
        "from_base": "FromBase",
        "box": "Box",
        "createbox": "Box",
        "cylinder": "Cylinder",
        "createcylinder": "Cylinder",
        "existing": "Existing",
        "fromexisting": "Existing",
    }
    if kind.lower() not in aliases:
        raise ValueError(
            "stock_type must be Automatic, FromBase, Box, Cylinder or Existing"
        )
    kind = aliases[kind.lower()]
    old = job.Stock
    if kind == "Automatic":
        kind = stock_kind(old) or "FromBase"
    keys = {
        "FromBase": ["Ext" + axis + side for axis in "XYZ" for side in ("neg", "pos")],
        "Box": ["Length", "Width", "Height"],
        "Cylinder": ["Radius", "Height"],
        "Existing": [],
    }[kind]
    values = {}
    for key, value in dimensions.items():
        if key not in keys:
            raise ValueError("Unsupported " + kind + " stock dimension: " + key)
        if type(value) not in (int, float, str):
            raise TypeError("Stock dimensions must be mm numbers or length quantities")
        quantity = App.Units.Quantity(
            str(value) + " mm" if type(value) in (int, float) else value
        )
        number = quantity.getValueAs("mm").Value
        if not math.isfinite(number) or (kind != "FromBase" and number < 0.001):
            raise ValueError(
                "Stock dimensions must be finite and at least the native 0.001 mm minimum"
            )
        values[key] = number
    native_placement = stock_placement(placement) if placement is not None else None
    if source_name and kind != "Existing":
        raise ValueError("stock_object_name applies only to Existing stock")
    source = job.Document.getObject(source_name) if source_name else None
    if kind == "Existing" and (
        source is None
        or source is old
        or hasattr(source, "PathResource")
        or not PathUtil.isSolid(source)
    ):
        raise ValueError("Existing stock requires an independent closed solid source")
    if kind == "FromBase":
        box = job.Proxy.modelBoundBox(job)
        for axis, span in zip("XYZ", (box.XLength, box.YLength, box.ZLength)):
            extent = span + sum(
                values.get(
                    "Ext" + axis + side,
                    getattr(old, "Ext" + axis + side).Value
                    if stock_kind(old) == kind
                    else 1,
                )
                for side in ("neg", "pos")
            )
            if extent <= 0:
                raise ValueError(
                    "Stock allowances produce a nonpositive " + axis + " extent"
                )
    if old is not None and stock_kind(old) == kind and kind != "Existing":
        stock = old
    elif kind == "FromBase":
        stock = PathStock.CreateFromBase(job)
    elif kind == "Box":
        stock = PathStock.CreateBox(job)
    elif kind == "Cylinder":
        stock = PathStock.CreateCylinder(job)
    else:
        stock = PathJob.createResourceClone(job, source, "Stock", "Stock")
        PathStock.SetupStockObject(stock, PathStock.StockType.Unknown)
    apply_parameters(stock, values)
    if native_placement is not None:
        stock.Placement = native_placement
    job.Stock = stock
    job.Document.recompute()
    if (
        not stock.isValid()
        or stock.Shape.isNull()
        or not stock.Shape.isValid()
        or stock.Shape.Volume <= 0
    ):
        raise ValueError("Native stock recompute produced invalid or empty geometry")
    for key, value in values.items():
        if not math.isclose(
            getattr(stock, key).Value, value, rel_tol=1e-10, abs_tol=1e-10
        ):
            raise ValueError("Native stock changed requested dimension: " + key)
    removed, retained = None, None
    if old is not None and old is not stock:
        if not old.InList:
            removed = old.Name
            job.Document.removeObject(old.Name)
        else:
            retained = old.Name
    result = stock_data(job)
    result.update(removed_stock=removed, retained_referenced_stock=retained)
    return result


def create_job(name, names, kind, dimensions, source_name, placement):
    doc = App.ActiveDocument
    models = job_models(doc, names)
    job = PathJob.Create(name, models)
    job.Label = name
    result = configure_stock(job, kind, dimensions, source_name, placement)
    result.update(name=job.Name, type=job.TypeId, model_count=len(models))
    return result
