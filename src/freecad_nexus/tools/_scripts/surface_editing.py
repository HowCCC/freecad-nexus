"""Selected-face spline editing with native UV boundary reconstruction."""

import math
from typing import TYPE_CHECKING

import FreeCAD as App
import Part

if TYPE_CHECKING:
    from .surface_geometry import surface_face, surface_output


def editable_surface(object_name, face_index, bspline=False):
    obj, face = surface_face(object_name, face_index)
    surface = face.Surface.copy()
    types = (
        (Part.BSplineSurface,) if bspline else (Part.BSplineSurface, Part.BezierSurface)
    )
    if not isinstance(surface, types):
        raise ValueError(
            "Selected face must have a "
            + ("BSpline" if bspline else "BSpline/Bezier")
            + " surface; convert explicitly with surface_to_nurbs"
        )
    return obj, face, surface


def spline_index(value, count, label):
    if type(value) is not int or not 1 <= value <= count:
        raise IndexError(label + " is one-based and must select an existing entry")
    return value


def finite_number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(label + " must be a finite number")
    return float(value)


def spline_direction(direction):
    direction = direction.strip().upper()
    if direction not in ("U", "V"):
        raise ValueError("direction must be U or V")
    return direction


def map_boundary_curve(curve, first, last, mapping):
    # Rational B-spline control-point affine transforms preserve the exact
    # pcurve, including conic holes, under unequal U/V scale and UV exchange.
    curve = curve.toBSpline(first, last)
    for index, point in enumerate(curve.getPoles(), 1):
        u, v = mapping(point.x, point.y)
        curve.setPole(index, App.Base.Vector2d(u, v))
    return curve, curve.FirstParameter, curve.LastParameter


def surface_seam_count(face):
    """Count repeated uses of shared seam edges, including split seams."""
    seen = []
    repeated = 0
    for wire in face.Wires:
        for edge in wire.OrderedEdges:
            if any(edge.isSame(previous) for previous in seen):
                repeated += 1
            else:
                seen.append(edge)
    return repeated


def restore_surface_seams(source, result):
    # Building each pcurve separately loses shared seam identity. OCC still
    # calls the face valid, but a sphere/torus shell becomes topologically open.
    # Sewing the single face joins coincident seam uses while retaining both
    # UV curves. Do this only for source seams, never for unrelated close edges.
    seams = surface_seam_count(source)
    if not seams:
        return result
    shell = Part.Shell([result])
    shell.sewShape()
    if len(shell.Faces) != 1:
        raise ValueError("Native seam reconstruction changed the face count")
    repaired = shell.Faces[0]
    if repaired.Orientation != result.Orientation:
        repaired.reverse()
    if surface_seam_count(repaired) != seams or (
        Part.Shell([source]).isClosed() and not Part.Shell([repaired]).isClosed()
    ):
        raise ValueError(
            "Surface edit opens or changes a shared seam; edit matching boundary "
            "poles together to preserve the source topology"
        )
    return repaired


def rebuild_surface_face(face, surface, mapping=None, reverse_wires=False):
    support = face.copy()
    if support.Orientation == "Reversed":
        support.reverse()
    wires = []
    ordered_wires = [support.OuterWire] + [
        wire for wire in support.Wires if not wire.isSame(support.OuterWire)
    ]
    for wire in ordered_wires:
        edges = []
        for edge in wire.OrderedEdges:
            curve_data = support.curveOnSurface(edge)
            if curve_data is None:
                raise ValueError("Native face boundary has no surface parameter curve")
            curve, first, last = curve_data
            if mapping:
                curve, first, last = map_boundary_curve(curve, first, last, mapping)
            new_edge = curve.toShape(surface, first, last)
            if edge.Orientation == "Reversed":
                new_edge.reverse()
            edges.append(new_edge)
        new_wire = Part.Wire(edges)
        if reverse_wires:
            new_wire.reverse()
        wires.append(new_wire)
    result = Part.Face(surface, wires[0])
    if len(wires) > 1:
        result.cutHoles(wires[1:])
    if face.Orientation == "Reversed":
        result.reverse()
    result = restore_surface_seams(face, result)
    if not result.isValid() or len(result.Wires) != len(face.Wires):
        raise ValueError("Native UV boundary reconstruction failed")
    return result


def edited_surface_output(
    obj, face, surface, face_index, name, suffix, mapping=None, reverse_wires=False
):
    shape = rebuild_surface_face(face, surface, mapping, reverse_wires)
    result = surface_output(shape, name or obj.Name + suffix)
    result.update(
        source=obj.Name,
        face_index=face_index,
        parameter_range=list(shape.ParameterRange),
        bounds=list(surface.bounds()),
        pole_counts=[surface.NbUPoles, surface.NbVPoles],
        u_degree=surface.UDegree,
        v_degree=surface.VDegree,
        u_periodic=surface.isUPeriodic(),
        v_periodic=surface.isVPeriodic(),
        output_scope="selected face with mapped UV boundaries; source object unchanged",
        boundary_wires=len(shape.Wires),
        seam_edges=surface_seam_count(shape),
        closed_shell=Part.Shell([shape]).isClosed(),
    )
    return result


def spline_info(object_name, face_index, control_net=False):
    obj, face = surface_face(object_name, face_index)
    surface = face.Surface
    data = {
        "name": obj.Name,
        "face_index": face_index,
        "surface_type": type(surface).__name__,
        "type": type(surface).__name__,
        "parameter_range": list(face.ParameterRange),
        "bounds": list(surface.bounds()),
        "orientation": face.Orientation,
    }
    for key in (
        "UDegree",
        "VDegree",
        "MaxDegree",
        "Continuity",
        "NbUPoles",
        "NbVPoles",
        "NbUKnots",
        "NbVKnots",
        "isUClosed",
        "isVClosed",
        "isUPeriodic",
        "isVPeriodic",
    ):
        if hasattr(surface, key):
            value = getattr(surface, key)
            data[key] = value() if callable(value) else value
    if control_net:
        if not hasattr(surface, "getPoles"):
            raise ValueError("Selected surface has no control net")
        data["getPoles"] = [
            [list(point) for point in row] for row in surface.getPoles()
        ]
        for key in (
            "getWeights",
            "getUKnots",
            "getVKnots",
            "getUMultiplicities",
            "getVMultiplicities",
        ):
            if hasattr(surface, key):
                data[key] = getattr(surface, key)()
    return data


def read_pole(object_name, face_index, u, v):
    obj, face, surface = editable_surface(object_name, face_index)
    spline_index(u, surface.NbUPoles, "u_index")
    spline_index(v, surface.NbVPoles, "v_index")
    return {
        "name": obj.Name,
        "face_index": face_index,
        "u_index": u,
        "v_index": v,
        "pole": list(surface.getPole(u, v)),
        "weight": surface.getWeight(u, v),
        "coordinate_space": "document",
    }


def edit_control_net(object_name, face_index, edits, name):
    obj, face, surface = editable_surface(object_name, face_index)
    if not edits:
        raise ValueError("Provide at least one control-net edit")
    seen = set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) - {
            "u_index",
            "v_index",
            "pole",
            "weight",
        }:
            raise ValueError("Unknown control-net edit field")
        u = spline_index(edit.get("u_index"), surface.NbUPoles, "u_index")
        v = spline_index(edit.get("v_index"), surface.NbVPoles, "v_index")
        if (u, v) in seen:
            raise ValueError("Duplicate pole edit")
        seen.add((u, v))
        if "pole" not in edit and "weight" not in edit:
            raise ValueError("Edit requires pole or weight")
        if "pole" in edit:
            point = edit["pole"]
            if not isinstance(point, (tuple, list)) or len(point) != 3:
                raise ValueError("pole requires three document-space coordinates")
            surface.setPole(
                u, v, App.Vector(*[finite_number(x, "pole coordinate") for x in point])
            )
        if "weight" in edit:
            weight = finite_number(edit["weight"], "weight")
            if weight <= 0:
                raise ValueError("weight must be positive")
            surface.setWeight(u, v, weight)
    result = edited_surface_output(obj, face, surface, face_index, name, "_net_edit")
    result["edited_poles"] = len(seen)
    if len(edits) == 1:
        result.update(
            u_index=u,
            v_index=v,
            pole=list(surface.getPole(u, v)),
            weight=surface.getWeight(u, v),
        )
    return result


def edit_knot(
    object_name,
    face_index,
    action,
    direction,
    index,
    value,
    multiplicity,
    tolerance,
    name,
):
    obj, face, surface = editable_surface(object_name, face_index, True)
    d = spline_direction(direction)
    old_bounds = surface.bounds()
    count = getattr(surface, "Nb" + d + "Knots")
    tolerance = finite_number(tolerance, "tolerance")
    if tolerance < 0 or action == "remove" and tolerance == 0:
        raise ValueError("Invalid knot tolerance")
    if multiplicity is not None and (
        type(multiplicity) is not int or multiplicity < (0 if action == "remove" else 1)
    ):
        raise ValueError("Invalid knot multiplicity")
    if action in ("set", "remove"):
        spline_index(index, count, "index")
    if action == "remove":
        if not getattr(surface, "is" + d + "Periodic")() and index in (1, count):
            raise ValueError("Cannot remove a nonperiodic boundary knot")
        if multiplicity >= getattr(surface, "get" + d + "Multiplicity")(index):
            raise ValueError(
                "multiplicity must be smaller than the current multiplicity"
            )
        success = getattr(surface, "remove" + d + "Knot")(
            index, multiplicity, tolerance
        )
        if not success:
            raise ValueError(
                "Native knot removal cannot meet tolerance; source unchanged"
            )
    elif action == "insert":
        value = finite_number(value, "parameter")
        limits = old_bounds[:2] if d == "U" else old_bounds[2:]
        if not limits[0] <= value <= limits[1]:
            raise ValueError("Knot insertion parameter is outside the surface bounds")
        getattr(surface, "insert" + d + "Knot")(value, multiplicity, tolerance)
    elif action == "set":
        value = finite_number(value, "value")
        args = [index, value] + ([] if multiplicity is None else [multiplicity])
        getattr(surface, "set" + d + "Knot")(*args)
    else:
        raise ValueError("Unknown knot action")
    new_bounds = surface.bounds()
    mapping = (
        parameter_mapping(old_bounds, new_bounds) if old_bounds != new_bounds else None
    )
    result = edited_surface_output(
        obj, face, surface, face_index, name, "_" + action + "_knot", mapping
    )
    result.update(
        direction=d,
        index=index,
        value=value,
        parameter=value,
        multiplicity=multiplicity,
        tolerance=tolerance,
        knots=list(getattr(surface, "get" + d + "Knots")()),
        native_result=True,
    )
    return result


def parameter_mapping(old, new):
    a, b, c, d = old
    e, f, g, h = new
    return lambda u, v: (
        e + (u - a) * (f - e) / (b - a),
        g + (v - c) * (h - g) / (d - c),
    )


def increase_surface_degree(object_name, face_index, u_degree, v_degree, name):
    obj, face, surface = editable_surface(object_name, face_index)
    old = surface.UDegree, surface.VDegree
    if (
        type(u_degree) is not int
        or type(v_degree) is not int
        or u_degree < old[0]
        or v_degree < old[1]
    ):
        raise ValueError("Degree elevation cannot lower either degree")
    if isinstance(surface, Part.BezierSurface):
        surface.increase(u_degree, v_degree)
    else:
        surface.increaseDegree(u_degree, v_degree)
    result = edited_surface_output(obj, face, surface, face_index, name, "_degree")
    result["old_degree"] = list(old)
    return result


def exchange_surface_uv(object_name, face_index, name):
    obj, face, surface = editable_surface(object_name, face_index)
    surface.exchangeUV()
    # UV exchange reverses the parameter normal. Reverse source orientation
    # for boundary construction to preserve the physical oriented normal.
    source = face.copy()
    source.reverse()
    return edited_surface_output(
        obj, source, surface, face_index, name, "_uv", lambda u, v: (v, u), True
    )


def set_parameter_range(object_name, face_index, bounds, name):
    obj, face, surface = editable_surface(object_name, face_index, True)
    if len(bounds) != 4:
        raise ValueError("bounds must be [u_min,u_max,v_min,v_max]")
    bounds = [finite_number(x, "bound") for x in bounds]
    if bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
        raise ValueError("Parameter ranges must increase")
    old = surface.bounds()
    surface.scaleKnotsToBounds(*bounds)
    return edited_surface_output(
        obj,
        face,
        surface,
        face_index,
        name,
        "_parameters",
        parameter_mapping(old, bounds),
    )


def set_surface_periodic(
    object_name, face_index, direction, periodic, origin_index, name
):
    obj, face, surface = editable_surface(object_name, face_index, True)
    d = spline_direction(direction)
    if type(periodic) is not bool:
        raise TypeError("periodic must be boolean")
    if origin_index is not None and not periodic:
        raise ValueError("origin_index requires periodic=True")
    if periodic:
        if not getattr(surface, "is" + d + "Closed")():
            raise ValueError("Surface must be closed before becoming periodic")
        getattr(surface, "set" + d + "Periodic")()
        if origin_index is not None:
            spline_index(
                origin_index, getattr(surface, "Nb" + d + "Knots"), "origin_index"
            )
            getattr(surface, "set" + d + "Origin")(origin_index)
    elif getattr(surface, "is" + d + "Periodic")():
        # Clamp only the requested direction using native isoparametric
        # control curves. Surface.segment clamps BOTH directions, which would
        # silently remove the other periodic direction on a torus.
        other = "V" if d == "U" else "U"
        curves = []
        first, last = face.ParameterRange[:2] if d == "U" else face.ParameterRange[2:]
        for j in range(1, getattr(surface, "Nb" + other + "Poles") + 1):
            indexes = [
                (i, j) if d == "U" else (j, i)
                for i in range(1, getattr(surface, "Nb" + d + "Poles") + 1)
            ]
            curve = Part.BSplineCurve()
            curve.buildFromPolesMultsKnots(
                [surface.getPole(i, k) for i, k in indexes],
                getattr(surface, "get" + d + "Multiplicities")(),
                getattr(surface, "get" + d + "Knots")(),
                True,
                getattr(surface, d + "Degree"),
                [surface.getWeight(i, k) for i, k in indexes],
            )
            curve.segment(first, last)
            curve.scaleKnotsToBounds(first, last)
            curves.append(curve)
        template = curves[0]
        poles = [
            [curve.getPole(i) for curve in curves]
            for i in range(1, template.NbPoles + 1)
        ]
        weights = [
            [curve.getWeight(i) for curve in curves]
            for i in range(1, template.NbPoles + 1)
        ]
        if d == "V":
            poles = [list(row) for row in zip(*poles)]
            weights = [list(row) for row in zip(*weights)]
        um = template.getMultiplicities() if d == "U" else surface.getUMultiplicities()
        vm = template.getMultiplicities() if d == "V" else surface.getVMultiplicities()
        uk = template.getKnots() if d == "U" else surface.getUKnots()
        vk = template.getKnots() if d == "V" else surface.getVKnots()
        up = False if d == "U" else surface.isUPeriodic()
        vp = False if d == "V" else surface.isVPeriodic()
        surface.buildFromPolesMultsKnots(
            poles, um, vm, uk, vk, up, vp, surface.UDegree, surface.VDegree, weights
        )
    result = edited_surface_output(obj, face, surface, face_index, name, "_periodic")
    result.update(
        direction=d,
        periodic=getattr(surface, "is" + d + "Periodic")(),
        origin_index=origin_index,
    )
    return result


def interpolate_surface_rows(rows, parameters):
    """Interpolate rows on one basis, retaining collapsed sphere/cone poles."""
    curves = []
    template = None
    for row in rows:
        if all((point - row[0]).Length <= 1e-12 for point in row):
            curves.append(None)
            continue
        curve = Part.BSplineCurve()
        curve.interpolate(Points=row, Parameters=parameters)
        curves.append(curve)
        template = curve
    if template is None:
        raise ValueError("Surface sample grid collapses to a curve or point")
    for index, curve in enumerate(curves):
        if curve is None:
            # Native interpolate rejects coincident points. A constant curve
            # represented on the adjacent rows' basis has exactly the needed
            # poles without perturbing the singularity or the parameter map.
            curve = Part.BSplineCurve()
            curve.buildFromPolesMultsKnots(
                [rows[index][0]] * template.NbPoles,
                template.getMultiplicities(),
                template.getKnots(),
                False,
                template.Degree,
            )
            curves[index] = curve
        if (
            curve.Degree != template.Degree
            or curve.getKnots() != template.getKnots()
            or curve.getMultiplicities() != template.getMultiplicities()
        ):
            raise ValueError("Native interpolation produced incompatible row bases")
    return curves


def refit_surface(object_name, face_index, u_samples, v_samples, tolerance, name):
    obj, face = surface_face(object_name, face_index)
    if (
        type(u_samples) is not int
        or type(v_samples) is not int
        or min(u_samples, v_samples) < 2
    ):
        raise ValueError("At least two samples in each direction are required")
    tolerance = finite_number(tolerance, "tolerance")
    if tolerance <= 0:
        raise ValueError("tolerance must be positive")
    a, b, c, d = face.ParameterRange
    surface = Part.BSplineSurface()
    points = [
        [
            face.valueAt(
                a + (b - a) * i / (u_samples - 1), c + (d - c) * j / (v_samples - 1)
            )
            for j in range(v_samples)
        ]
        for i in range(u_samples)
    ]
    uparams = [i / (u_samples - 1) for i in range(u_samples)]
    vparams = [j / (v_samples - 1) for j in range(v_samples)]
    # The installed Surface.interpolate API cannot accept parameter vectors.
    # Tensor-product curve interpolation keeps a known uniform UV mapping.
    ucurves = interpolate_surface_rows(
        [[points[i][j] for i in range(u_samples)] for j in range(v_samples)], uparams
    )
    vcurves = interpolate_surface_rows(
        [[u.getPole(i) for u in ucurves] for i in range(1, ucurves[0].NbPoles + 1)],
        vparams,
    )
    ucurve, vcurve = ucurves[0], vcurves[0]
    surface.buildFromPolesMultsKnots(
        [curve.getPoles() for curve in vcurves],
        ucurve.getMultiplicities(),
        vcurve.getMultiplicities(),
        ucurve.getKnots(),
        vcurve.getKnots(),
        False,
        False,
        ucurve.Degree,
        vcurve.Degree,
    )
    # Independent midpoint checks detect a refit that does not meet the
    # caller's requested accuracy. Sampling is reported, never called proof.
    maximum = 0.0
    for i in range(2 * u_samples - 1):
        u = i / (2 * u_samples - 2)
        for j in range(2 * v_samples - 1):
            v = j / (2 * v_samples - 2)
            maximum = max(
                maximum,
                (
                    surface.value(u, v) - face.valueAt(a + (b - a) * u, c + (d - c) * v)
                ).Length,
            )
    if maximum > tolerance:
        raise ValueError(
            "Surface refit exceeds sampled tolerance: " + str(maximum) + " mm"
        )
    result = edited_surface_output(
        obj,
        face,
        surface,
        face_index,
        name,
        "_refit",
        parameter_mapping(face.ParameterRange, surface.bounds()),
    )
    result.update(
        u_samples=u_samples,
        v_samples=v_samples,
        sampled_max_error_mm=maximum,
        tolerance=tolerance,
        geometry_preserved_exactly=False,
        error_bound_proven=False,
    )
    return result
