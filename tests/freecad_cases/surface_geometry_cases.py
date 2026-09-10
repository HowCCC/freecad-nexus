"""Real differential geometry and trimmed-face workflows through MCP adapters."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    doc = App.newDocument("SurfaceGeometry")
    call = tools_for(root, "surface")

    def feature(name, shape):
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = shape
        return obj

    def close(actual, expected, tolerance=1e-7):
        assert abs(actual - expected) < tolerance, (actual, expected)

    def vector_close(actual, expected):
        assert (App.Vector(*actual) - App.Vector(*expected)).Length < 1e-7, (
            actual,
            expected,
        )

    def join(first, second, expected, **kwargs):
        result = call(
            "surface_check_continuity",
            object_name=first.Name,
            other_object_name=second.Name,
            other_face_index=0,
            samples=9,
            **kwargs,
        )
        assert result["classification"] == expected, result
        assert result["proof"] is False
        return result

    def parabola(name, xs, zs):
        poles = [[[x, y, z] for y in (0, 10)] for x, z in zip(xs, zs)]
        result = call("surface_create_bezier", name=name, poles=poles)
        return doc.getObject(result["name"])

    plane = feature("Left", Part.makePlane(10, 10, App.Vector(-10, 0, 0)))
    right = feature("Right", Part.makePlane(10, 10))
    planar = join(plane, right, "G2")
    close(planar["max_curvature_difference_per_mm"], 0)
    reversed_face = feature("Reversed", right.Shape.reversed())
    reverse_result = join(plane, reversed_face, "G2")
    assert reverse_result["orientation_consistent"] is False
    close(reverse_result["max_oriented_normal_angle_deg"], 180)
    angled = feature("Angled", right.Shape.copy())
    angled.Placement = App.Placement(
        App.Vector(), App.Rotation(App.Vector(0, 1, 0), 30)
    )
    angular = join(plane, angled, "G0")
    close(angular["max_normal_angle_deg"], 30)
    gapped = feature("Gapped", Part.makePlane(10, 10, App.Vector(0.01, 0, 0)))
    gap = join(plane, gapped, "disconnected")
    close(gap["max_gap_mm"], 0.01)
    # Short boundary sharing only half the join must not pass by one-way projection.
    partial = feature("Partial", Part.makePlane(10, 5))
    partial_result = join(
        plane,
        partial,
        "disconnected",
        edge_index=planar["first"]["edge_index"],
        other_edge_index=planar["second"]["edge_index"],
    )
    close(partial_result["max_gap_mm"], 5)
    curved = parabola("ParabolicRight", [0, 5, 10], [0, 0, 10])
    tangent = join(plane, curved, "G1")
    close(tangent["max_curvature_difference_per_mm"], 0.2)
    left_curved = parabola("ParabolicLeft", [-10, -5, 0], [10, 0, 0])
    curved_match = join(left_curved, curved, "G2")
    close(curved_match["max_curvature_difference_per_mm"], 0)
    # Common placement must not change classification or curvature.
    placement = App.Placement(
        App.Vector(100, -20, 3), App.Rotation(App.Vector(1, 2, 3), 37)
    )
    left_curved.Placement = placement
    curved.Placement = placement
    join(left_curved, curved, "G2")
    compound = feature("Pair", Part.makeCompound([plane.Shape, right.Shape]))
    combined = call("surface_check_continuity", object_name=compound.Name)
    assert combined["classification"] == "G2", combined
    single = call("surface_check_continuity", object_name=plane.Name)
    assert single["classification"] == "not_applicable" and single["G2"] is None
    invalid = call(
        "surface_check_continuity",
        object_name=plane.Name,
        other_object_name=right.Name,
        other_face_index=0,
        edge_index=200,
        expect_success=False,
    )
    assert "edge_index" in invalid["error"]

    # Analytic periodic faces; signed normal curvature and singular poles.
    cylinder_surface = Part.makeCylinder(5, 10).Faces[0].Surface
    cylinder1 = feature("CylinderOne", cylinder_surface.toShape(0, math.pi, 0, 10))
    cylinder2 = feature(
        "CylinderTwo", cylinder_surface.toShape(math.pi, 2 * math.pi, 0, 10)
    )
    join(cylinder1, cylinder2, "G2")
    cv = call("surface_curvature", object_name=cylinder1.Name)["result"]
    close(cv["Min"], -0.2)
    close(cv["Max"], 0)
    reversed_cylinder = feature("ReversedCylinder", cylinder1.Shape.reversed())
    cv_reverse = call("surface_curvature", object_name=reversed_cylinder.Name)["result"]
    close(cv_reverse["Min"], 0)
    close(cv_reverse["Max"], 0.2)
    sphere = Part.makeSphere(5).Faces[0].Surface
    sphere1 = feature(
        "SphereOne", sphere.toShape(0, math.pi, -math.pi / 2, math.pi / 2)
    )
    sphere2 = feature(
        "SphereTwo", sphere.toShape(math.pi, 2 * math.pi, -math.pi / 2, math.pi / 2)
    )
    singular = join(sphere1, sphere2, "inconclusive")
    assert singular["differential_errors"] and singular["G0"] is True

    # Explicit UV values inside [0,1] must remain native when requested.
    wide = feature("Wide", Part.makePlane(10, 20, App.Vector(0, 0, 3)))
    normalized = call("surface_evaluate", object_name=wide.Name, u=0.2, v=0.3)
    native = call(
        "surface_evaluate",
        object_name=wide.Name,
        u=0.2,
        v=0.3,
        parameter_space="native",
    )
    vector_close(normalized["point"], [2, 6, 3])
    vector_close(native["point"], [0.2, 0.3, 3])
    selected = call("surface_evaluate", object_name=compound.Name, face_index=1)
    vector_close(selected["point"], [5, 5, 0])
    call(
        "surface_evaluate",
        object_name=compound.Name,
        face_index=-1,
        expect_success=False,
    )
    call(
        "surface_evaluate",
        object_name=wide.Name,
        parameter_space="auto",
        expect_success=False,
    )
    call("surface_evaluate", object_name=wide.Name, u=2, expect_success=False)
    outside = call(
        "surface_evaluate", object_name=wide.Name, u=30, parameter_space="native"
    )
    assert outside["inside_face"] is False
    wide.Placement = placement
    target_point = wide.Shape.Faces[0].valueAt(0.2, 0.3)
    placed = call(
        "surface_evaluate",
        object_name=wide.Name,
        u=0.2,
        v=0.3,
        parameter_space="native",
    )
    vector_close(placed["point"], list(target_point))
    normal = call("surface_normal", object_name=wide.Name, parameter_space="native")
    vector_close(normal["normal"], list(wide.Shape.Faces[0].normalAt(0.5, 0.5)))
    reverse_normal = call("surface_normal", object_name=reversed_face.Name)
    vector_close(reverse_normal["normal"], [0, 0, -1])
    derivative = call(
        "surface_derivative", object_name=wide.Name, parameter_space="native"
    )
    vector_close(
        derivative["derivative"], list(placement.Rotation.multVec(App.Vector(1, 0, 0)))
    )
    tangent_vectors = call("surface_tangent", object_name=wide.Name)
    vector_close(tangent_vectors["u_tangent"], derivative["derivative"])

    # Retain trimming holes instead of recreating a filled underlying surface.
    perforated_shape = Part.makePlane(10, 10).cut(
        Part.makeCylinder(1, 2, App.Vector(5, 5, -1))
    )
    perforated = feature("Perforated", perforated_shape)
    hole_projection = call(
        "surface_project_point", object_name=perforated.Name, x=5, y=5, z=3
    )
    assert hole_projection["inside_face"] is False
    close(hole_projection["distance_mm"], 3)
    hole_evaluation = call("surface_evaluate", object_name=perforated.Name)
    assert hole_evaluation["inside_face"] is False
    trim = call(
        "surface_trim_parameters",
        object_name=perforated.Name,
        u_min=2,
        u_max=8,
        v_min=2,
        v_max=8,
    )
    close(trim["area"], 36 - math.pi)
    iso = call("surface_extract_isocurve", object_name=perforated.Name)
    assert iso["edges"] == 2
    close(iso["length"], 8)
    iso_native = call(
        "surface_extract_isocurve",
        object_name=perforated.Name,
        parameter=0.5,
        parameter_space="native",
    )
    close(iso_native["length"], 10)
    native_trim = call(
        "surface_segment",
        object_name=right.Name,
        u_min=0.2,
        u_max=0.8,
        v_min=0.2,
        v_max=0.8,
    )
    close(native_trim["area"], 0.36)
    normalized_trim = call(
        "surface_segment",
        object_name=right.Name,
        u_min=0.2,
        u_max=0.8,
        v_min=0.2,
        v_max=0.8,
        parameter_space="normalized",
    )
    close(normalized_trim["area"], 36)
    before = [obj.Name for obj in doc.Objects]
    call(
        "surface_trim_parameters",
        object_name=right.Name,
        u_min=-1,
        u_max=8,
        v_min=2,
        v_max=8,
        expect_success=False,
    )
    assert before == [obj.Name for obj in doc.Objects]
    doc.recompute()
    filename = str(Path(output) / "surface_geometry.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    persisted = call("surface_check_continuity", object_name="Pair")
    assert persisted["classification"] == "G2"
    close(doc.getObject(trim["name"]).Shape.Area, 36 - math.pi)
    return {
        "planar": planar["classification"],
        "angular": angular["classification"],
        "tangent_curved": tangent["classification"],
        "matching_curved": curved_match["classification"],
        "gap_mm": gap["max_gap_mm"],
        "singular": singular["classification"],
        "trimmed_area": trim["area"],
        "isocurve_segments": iso["edges"],
    }
