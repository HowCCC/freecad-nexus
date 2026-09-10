"""Face selection and differential geometry, executed in FreeCAD's Python."""

import math

import FreeCAD as App
import Part


def surface_object(object_name):
    doc = App.ActiveDocument
    obj = doc.getObject(object_name) if doc else None
    if not obj or not hasattr(obj, "Shape") or obj.Shape.isNull():
        raise ValueError("Object has no shape: " + object_name)
    return obj


def surface_face(object_name, face_index=0):
    obj = surface_object(object_name)
    if type(face_index) is not int or not 0 <= face_index < len(obj.Shape.Faces):
        raise IndexError("face_index is zero-based and must select an existing face")
    return obj, obj.Shape.Faces[face_index]


def surface_output(shape, name):
    if shape.isNull() or not shape.isValid():
        raise ValueError("Native operation produced empty or invalid geometry")
    obj = App.ActiveDocument.addObject("Part::Feature", name)
    obj.Shape = shape
    App.ActiveDocument.recompute()
    return {
        "name": obj.Name,
        "shape_type": shape.ShapeType,
        "faces": len(shape.Faces),
        "edges": len(shape.Edges),
        "solids": len(shape.Solids),
        "area": shape.Area,
        "volume": shape.Volume,
        "is_valid": shape.isValid(),
    }


def surface_wire(object_name, wire_index=0):
    obj = surface_object(object_name)
    if obj.Shape.ShapeType == "Edge" and wire_index == 0:
        return Part.Wire(obj.Shape)
    if type(wire_index) is not int or not 0 <= wire_index < len(obj.Shape.Wires):
        raise IndexError("wire_index must select a wire (or 0 for a single edge)")
    return obj.Shape.Wires[wire_index]


def surface_parameter(value, lower, upper, parameter_space):
    if parameter_space not in ("native", "normalized"):
        raise ValueError("parameter_space must be native or normalized")
    if not math.isfinite(value):
        raise ValueError("Parameters must be finite")
    if parameter_space == "normalized":
        if not 0 <= value <= 1:
            raise ValueError("Normalized parameters must be between 0 and 1")
        value = lower + (upper - lower) * value
    if not math.isfinite(value):
        raise ValueError("Cannot normalize an unbounded parameter range")
    return value


def surface_uv(face, u, v, parameter_space):
    a, b, c, d = face.ParameterRange
    return (
        surface_parameter(u, a, b, parameter_space),
        surface_parameter(v, c, d, parameter_space),
    )


def surface_unit(vector):
    if not math.isfinite(vector.Length) or vector.Length <= 1e-14:
        raise ValueError("Singular surface differential: zero-length direction")
    return vector / vector.Length


def surface_second_form(face, u, v, x_axis, y_axis, normal):
    """Second fundamental form in a supplied orthonormal tangent basis.

    Unlike comparing principal curvature values alone, this also retains
    principal directions. The mixed derivative is essential for G2 checks.
    """
    s = face.Surface
    du, dv = face.derivative1At(u, v)
    e, f, g = du.dot(du), du.dot(dv), dv.dot(dv)
    determinant = e * g - f * f
    if e <= 0 or g <= 0 or determinant <= 1e-14 * e * g:
        raise ValueError("Singular surface differential: dependent U/V directions")
    l = normal.dot(s.getDN(u, v, 2, 0))
    m = normal.dot(s.getDN(u, v, 1, 1))
    n = normal.dot(s.getDN(u, v, 0, 2))

    def coordinates(direction):
        a, b = direction.dot(du), direction.dot(dv)
        return (g * a - f * b) / determinant, (e * b - f * a) / determinant

    def form(a, b):
        return l * a[0] * b[0] + m * (a[0] * b[1] + a[1] * b[0]) + n * a[1] * b[1]

    x, y = coordinates(x_axis), coordinates(y_axis)
    return form(x, x), form(x, y), form(y, y)


def surface_evaluation(
    object_name, face_index, u, v, parameter_space, kind, u_order=1, v_order=0
):
    obj, face = surface_face(object_name, face_index)
    u, v = surface_uv(face, u, v, parameter_space)
    result = {
        "name": obj.Name,
        "face_index": face_index,
        "u": u,
        "v": v,
        "parameter_space": "native",
        "input_parameter_space": parameter_space,
        "parameter_range": list(face.ParameterRange),
        "inside_face": bool(face.isPartOfDomain(u, v)),
        "orientation": face.Orientation,
    }
    if kind == "point":
        result["point"] = list(face.valueAt(u, v))
    elif kind == "normal":
        result["normal"] = list(surface_unit(face.normalAt(u, v)))
    elif kind == "tangent":
        du, dv = face.derivative1At(u, v)
        result.update(
            u_tangent=list(surface_unit(du)), v_tangent=list(surface_unit(dv))
        )
    elif kind == "derivative":
        if min(u_order, v_order) < 0 or u_order + v_order < 1:
            raise ValueError("Derivative orders must be nonnegative and nonzero")
        result.update(
            u_order=u_order,
            v_order=v_order,
            derivative=list(face.Surface.getDN(u, v, u_order, v_order)),
            derivative_parameter_space="native",
        )
    elif kind == "curvature":
        normal = surface_unit(face.normalAt(u, v))
        x_axis = surface_unit(face.derivative1At(u, v)[0])
        y_axis = surface_unit(normal.cross(x_axis))
        a, b, c = surface_second_form(face, u, v, x_axis, y_axis, normal)
        mean = (a + c) / 2
        radius = math.hypot((a - c) / 2, b)
        result["result"] = {
            "Min": mean - radius,
            "Max": mean + radius,
            "Mean": mean,
            "Gauss": a * c - b * b,
        }
        result["curvature_convention"] = (
            "second fundamental form using oriented face normal"
        )
        result["units"] = {
            "Min": "1/mm",
            "Max": "1/mm",
            "Mean": "1/mm",
            "Gauss": "1/mm^2",
        }
    else:
        raise ValueError("Unknown surface evaluation")
    return result


def surface_parameter_trim(object_name, face_index, bounds, parameter_space, name):
    """Intersect a UV rectangle with the actual trimmed face, retaining holes."""
    obj, face = surface_face(object_name, face_index)
    u1, v1 = surface_uv(face, bounds[0], bounds[2], parameter_space)
    u2, v2 = surface_uv(face, bounds[1], bounds[3], parameter_space)
    a, b, c, d = face.ParameterRange
    if not (a <= u1 < u2 <= b and c <= v1 < v2 <= d):
        raise ValueError(
            "Trim rectangle must have positive spans inside the selected face range"
        )
    rectangle = face.Surface.toShape(u1, u2, v1, v2)
    shape = face.common(rectangle)
    if shape.isNull() or not shape.Faces or not shape.isValid():
        raise ValueError("UV rectangle has no valid surface intersection")
    out = App.ActiveDocument.addObject("Part::Feature", name or obj.Name + "_trimmed")
    out.Shape = shape
    App.ActiveDocument.recompute()
    return {
        "name": out.Name,
        "source": obj.Name,
        "face_index": face_index,
        "parameter_range": [u1, u2, v1, v2],
        "bounds": [u1, u2, v1, v2],
        "parameter_space": "native",
        "faces": len(shape.Faces),
        "area": shape.Area,
        "is_valid": shape.isValid(),
    }
