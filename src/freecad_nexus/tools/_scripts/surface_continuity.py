"""Sampled inter-face G0/G1/G2 checks; not a mathematical continuity proof."""

import math
from typing import TYPE_CHECKING

import FreeCAD as App
import Part

if TYPE_CHECKING:
    from .surface_geometry import surface_face, surface_second_form, surface_unit


def surface_edge_samples(edge, count):
    if edge.Length <= 1e-12:
        raise ValueError("Cannot assess continuity on a degenerate edge")
    return [
        edge.valueAt(edge.getParameterByLength(edge.Length * i / (count - 1)))
        for i in range(count)
    ]


def surface_boundary_matches(first, second, count):
    """Bidirectional closest boundary samples avoid declaring partial overlap G0."""
    matches = []
    for reverse, source, target in ((False, first, second), (True, second, first)):
        for index, point in enumerate(surface_edge_samples(source, count)):
            distance, points, _ = Part.Vertex(point).distToShape(target)
            if not points:
                raise ValueError("Native closest-boundary projection failed")
            near = points[0][1]
            matches.append(
                (near, point, distance, reverse, index)
                if reverse
                else (point, near, distance, reverse, index)
            )
    return matches


def surface_point_uv(face, point):
    u, v = face.Surface.parameter(point)
    # Periodic projections may return a representative outside a trimmed face.
    bounds = face.ParameterRange
    values = [u, v]
    for i, direction in enumerate(("U", "V")):
        periodic = getattr(face.Surface, "is" + direction + "Periodic")()
        if periodic:
            period = getattr(face.Surface, direction + "Period")()
            low, high = bounds[2 * i : 2 * i + 2]
            values[i] += math.ceil((low - values[i] - 1e-10) / period) * period
            if values[i] > high + 1e-8:
                raise ValueError(
                    "Closest boundary projection lies outside face UV range"
                )
    if not face.isPartOfDomain(*values):
        # Kernel classification can exclude a boundary by a few ULPs; verify
        # the projected point against the actual trimmed face in model units.
        p = face.valueAt(*values)
        if Part.Vertex(p).distToShape(face)[0] > 1e-7:
            raise ValueError("Projection is outside the trimmed face")
    return values


def surface_continuity(
    object_name,
    tolerance,
    other_object_name,
    face_index,
    other_face_index,
    edge_index,
    other_edge_index,
    samples,
    angular_tolerance_deg,
    curvature_tolerance,
):
    obj, face = surface_face(object_name, face_index)
    for value in (tolerance, angular_tolerance_deg, curvature_tolerance):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Continuity tolerances must be finite and positive")
    if angular_tolerance_deg >= 90:
        raise ValueError("Angular tolerance must be less than 90 degrees")
    if type(samples) is not int or not 3 <= samples <= 201:
        raise ValueError("samples must be an integer between 3 and 201")
    result = {
        "name": obj.Name,
        "is_valid": obj.Shape.isValid(),
        "is_closed": obj.Shape.isClosed(),
        "edge_count": len(obj.Shape.Edges),
        "degenerate_edges": [
            {"edge": i + 1, "length": e.Length}
            for i, e in enumerate(obj.Shape.Edges)
            if e.Length <= tolerance
        ],
        "tolerance": tolerance,
        "angular_tolerance_deg": angular_tolerance_deg,
        "curvature_tolerance_per_mm": curvature_tolerance,
        "method": "bidirectional boundary sampling and second fundamental forms",
        "proof": False,
        "scope": "selected face boundary pair",
    }
    if not other_object_name and len(obj.Shape.Faces) == 1 and other_face_index == 1:
        result.update(
            evaluated=False,
            classification="not_applicable",
            G0=None,
            G1=None,
            G2=None,
            reason="Select another face/object to test inter-face continuity",
        )
        return result
    other_obj, other = surface_face(other_object_name or object_name, other_face_index)
    if obj.Name == other_obj.Name and face_index == other_face_index:
        raise ValueError("Select two distinct faces")

    def edges(selected_face, index):
        if index is not None:
            if type(index) is not int or not 0 <= index < len(selected_face.Edges):
                raise IndexError("edge_index is zero-based within the selected face")
            return [(index, selected_face.Edges[index])]
        return [(i, e) for i, e in enumerate(selected_face.Edges) if e.Length > 1e-12]

    # For unsewn faces, choose the closest complete pair by sampled symmetric
    # distance. Never equate merely meeting at one endpoint with G0 continuity.
    candidates = []
    for i, first_edge in edges(face, edge_index):
        for j, second_edge in edges(other, other_edge_index):
            matches = surface_boundary_matches(first_edge, second_edge, samples)
            candidates.append((max(m[2] for m in matches), i, j, matches))
    if not candidates:
        raise ValueError("Faces have no nondegenerate boundary edges")
    gap, i, j, matches = min(candidates, key=lambda item: item[:3])
    result.update(
        evaluated=True,
        first={"object": obj.Name, "face_index": face_index, "edge_index": i},
        second={
            "object": other_obj.Name,
            "face_index": other_face_index,
            "edge_index": j,
        },
        candidate_pairs=len(candidates),
        samples_per_direction=samples,
        sample_count=len(matches),
        max_gap_mm=gap,
        G0=gap <= tolerance,
        G1=None,
        G2=None,
        max_normal_angle_deg=None,
        max_curvature_difference_per_mm=None,
    )
    if not result["G0"]:
        result.update(
            G1=False, G2=False, classification="disconnected", differential_errors=[]
        )
        return result
    angles, oriented_angles, differences, errors = [], [], [], []
    for point1, point2, _, reverse, sample in matches:
        try:
            u1, v1 = surface_point_uv(face, point1)
            u2, v2 = surface_point_uv(other, point2)
            n1 = surface_unit(face.normalAt(u1, v1))
            n2 = surface_unit(other.normalAt(u2, v2))
            dot = max(-1.0, min(1.0, n1.dot(n2)))
            oriented_angles.append(math.degrees(math.acos(dot)))
            angles.append(math.degrees(math.acos(abs(dot))))
            if dot < 0:
                n2 = -n2  # G continuity concerns tangent planes, not face winding.
            x1 = surface_unit(face.derivative1At(u1, v1)[0])
            y1 = surface_unit(n1.cross(x1))
            # Align the tangent planes by their minimal normal rotation.
            rotation = App.Rotation(n1, n2)
            a1, b1, c1 = surface_second_form(face, u1, v1, x1, y1, n1)
            a2, b2, c2 = surface_second_form(
                other, u2, v2, rotation.multVec(x1), rotation.multVec(y1), n2
            )
            # Spectral norm: maximum normal-curvature difference over all
            # tangent directions, not just two principal curvature scalars.
            a, b, c = a1 - a2, b1 - b2, c1 - c2
            differences.append(abs((a + c) / 2) + math.hypot((a - c) / 2, b))
        except Exception as exc:
            errors.append(
                {
                    "direction": "second_to_first" if reverse else "first_to_second",
                    "sample": sample,
                    "error": str(exc),
                }
            )
    result["differential_errors"] = errors
    if angles:
        result["max_normal_angle_deg"] = max(angles)
        result["max_oriented_normal_angle_deg"] = max(oriented_angles)
        result["orientation_consistent"] = max(oriented_angles) < 90
    if differences:
        result["max_curvature_difference_per_mm"] = max(differences)
    result["G1"] = (
        False
        if any(a > angular_tolerance_deg for a in angles)
        else (True if len(angles) == len(matches) else None)
    )
    result["G2"] = (
        False
        if result["G1"] is False
        else (
            False
            if any(d > curvature_tolerance for d in differences)
            else True
            if result["G1"] is True and len(differences) == len(matches)
            else None
        )
    )
    result["classification"] = (
        "inconclusive"
        if result["G1"] is None or result["G2"] is None
        else "G2"
        if result["G2"]
        else "G1"
        if result["G1"]
        else "G0"
    )
    return result
