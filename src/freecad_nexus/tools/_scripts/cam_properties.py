"""Strict scalar CAM parameter application in FreeCAD's interpreter."""

import math

import FreeCAD as App
import Path.Base.Util as PathUtil


def check_parameters(obj, parameters):
    """Reject unknown, linked, internal, wrongly typed and nonfinite inputs."""
    for key, value in parameters.items():
        if key not in obj.PropertiesList:
            raise ValueError("Unknown native property: " + key)
        kind = obj.getTypeIdOfProperty(key)
        if (
            key
            in (
                "Proxy",
                "Path",
                "ExpressionEngine",
                "Placement",
                "Shape",
                "Base",
                "ToolController",
            )
            or "Link" in kind
            or kind == "App::PropertyPythonObject"
        ):
            raise ValueError("Property requires its dedicated tool: " + key)
        if "ReadOnly" in obj.getPropertyStatus(key):
            raise ValueError("Property is read-only: " + key)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Finite value required: " + key)
        if kind == "App::PropertyBool" and type(value) is not bool:
            raise TypeError("Boolean required: " + key)
        if kind.startswith("App::PropertyInteger") and type(value) is not int:
            raise TypeError("Integer required: " + key)
        if (
            kind == "App::PropertyEnumeration"
            and value not in obj.getEnumerationsOfProperty(key)
        ):
            raise ValueError("Invalid enumeration for " + key)
        if kind in ("App::PropertyVector", "App::PropertyVectorDistance"):
            if (
                not isinstance(value, (list, tuple))
                or len(value) != 3
                or any(
                    type(v) not in (int, float) or not math.isfinite(v) for v in value
                )
            ):
                raise ValueError("Expected three finite vector coordinates: " + key)


def apply_parameters(obj, parameters):
    """Explicit values replace expressions on those properties, atomically."""
    check_parameters(obj, parameters)
    for key, value in parameters.items():
        if obj.getTypeIdOfProperty(key) in (
            "App::PropertyVector",
            "App::PropertyVectorDistance",
        ):
            value = App.Vector(*value)
        # Otherwise recompute silently restores the SetupSheet expression and
        # discards the user-supplied value (especially Start/FinalDepth).
        expressions = [
            name
            for name, _ in obj.ExpressionEngine
            if name == key or name.startswith(key + ".")
        ]
        for name in expressions:
            obj.setExpression(name, None)
        PathUtil.setProperty(obj, key, value)
    return list(parameters)
