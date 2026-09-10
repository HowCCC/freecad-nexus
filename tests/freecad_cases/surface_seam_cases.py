"""Closed NURBS seams, collapsed pole boundaries and seam-crossing trims."""

from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    call = tools_for(root, "surface")
    doc = App.newDocument("SurfaceSeams")

    def seam_count(face):
        # A seam is the same topological edge used twice by the boundary.
        return sum(len(wire.OrderedEdges) for wire in face.Wires) - len(face.Edges)

    def check(source, result, mapping):
        face = doc.getObject(result["name"]).Shape.Faces[0]
        assert face.isValid()
        assert seam_count(face) == seam_count(source), result
        assert result["seam_edges"] == seam_count(source)
        assert len(face.Wires) == len(source.Wires)
        assert Part.Shell([face]).isClosed() == Part.Shell([source]).isClosed()
        # OCC's default NURBS mass integration varies with degree, so verify
        # geometry directly instead of treating Shape.Area as an exact oracle.
        a, b, c, d = source.ParameterRange
        for s in (0, 0.13, 0.37, 0.73, 1):
            for t in (0, 0.19, 0.61, 0.83, 1):
                u, v = a + (b - a) * s, c + (d - c) * t
                p, q = mapping(u, v)
                assert (source.valueAt(u, v) - face.valueAt(p, q)).Length < 1e-7
                if 0 < t < 1:
                    assert source.normalAt(u, v).dot(face.normalAt(p, q)) > 0.999999
                assert source.isPartOfDomain(u, v) == face.isPartOfDomain(p, q)
        assert sum(edge.Length < 1e-7 for edge in face.Edges) == sum(
            edge.Length < 1e-7 for edge in source.Edges
        )
        return face

    persisted = []
    opened_cylinder = None
    for name, shape in (
        ("Sphere", Part.makeSphere(5)),
        ("Cone", Part.makeCone(5, 0, 10)),
        ("Cylinder", Part.makeCylinder(5, 10)),
        ("Torus", Part.makeTorus(8, 2)),
    ):
        source = next(
            face for face in shape.Faces if not isinstance(face.Surface, Part.Plane)
        )
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = source.toNurbs()
        obj.Placement = App.Placement(
            App.Vector(10, 20, 30), App.Rotation(App.Vector(1, 2, 3), 35)
        )
        if name == "Sphere":
            reversed_shape = obj.Shape.copy()
            reversed_shape.reverse()
            obj.Shape = reversed_shape
        doc.recompute()
        original = obj.Shape.Faces[0]
        a, b, c, d = original.Surface.bounds()
        commands = [
            (
                "surface_increase_degree",
                dict(
                    u_degree=original.Surface.UDegree + 1,
                    v_degree=original.Surface.VDegree + 1,
                ),
                lambda u, v: (u, v),
            ),
            (
                "surface_set_parameter_range",
                dict(u_min=2, u_max=5, v_min=7, v_max=11),
                lambda u, v: (2 + 3 * (u - a) / (b - a), 7 + 4 * (v - c) / (d - c)),
            ),
            ("surface_exchange_uv", {}, lambda u, v: (v, u)),
            (
                "surface_set_periodic",
                dict(direction="U", periodic=False),
                lambda u, v: (u, v),
            ),
        ]
        for tool, kwargs, mapping in commands:
            result = call(tool, object_name=name, **kwargs)
            edited = check(original, result, mapping)
            persisted.append(
                (result["name"], seam_count(edited), Part.Shell([edited]).isClosed())
            )
            if name == "Cylinder" and tool == "surface_set_periodic":
                opened_cylinder = result["name"]
            # The repaired seam must retain both pcurves for a subsequent edit.
            again = call("surface_exchange_uv", object_name=result["name"])
            check(edited, again, lambda u, v: (v, u))
        fitted = call(
            "surface_reparameterize",
            object_name=name,
            u_poles=17,
            v_poles=13,
            tolerance=0.05,
        )
        fit = doc.getObject(fitted["name"]).Shape.Faces[0]
        assert fit.isValid() and seam_count(fit) == seam_count(original)
        assert Part.Shell([fit]).isClosed() == Part.Shell([original]).isClosed()
        assert sum(e.Length < 1e-7 for e in fit.Edges) == sum(
            e.Length < 1e-7 for e in original.Edges
        )
        # Independent off-grid comparisons supplement the adapter's samples.
        for s in (0.037, 0.173, 0.419, 0.793, 0.953):
            for t in (0, 0.123, 0.431, 0.719, 1):
                assert (
                    fit.valueAt(s, t)
                    - original.valueAt(a + (b - a) * s, c + (d - c) * t)
                ).Length < 0.05
        persisted.append(
            (fitted["name"], seam_count(fit), Part.Shell([fit]).isClosed())
        )
        if name in ("Sphere", "Cone"):
            swapped = call("surface_exchange_uv", object_name=name)
            swapped_fit = call(
                "surface_reparameterize",
                object_name=swapped["name"],
                u_poles=13,
                v_poles=17,
                tolerance=0.05,
            )
            sf = doc.getObject(swapped_fit["name"]).Shape.Faces[0]
            assert sf.isValid() and seam_count(sf) == seam_count(original)
            assert Part.Shell([sf]).isClosed() == Part.Shell([original]).isClosed()
        assert obj.Shape.Faces[0].isSame(original)

    # Cut across the cylinder's seam and cut another hole opposite it.
    side = Part.makeCylinder(5, 10).Faces[0]
    trimmed = side.cut(Part.makeBox(3, 2, 2, App.Vector(4, -1, 4)))
    trimmed = trimmed.cut(Part.makeBox(3, 1, 2, App.Vector(-6, -0.5, 4)))
    assert len(trimmed.Faces) == 1
    obj = doc.addObject("Part::Feature", "TrimmedCylinder")
    obj.Shape = trimmed.toNurbs()
    doc.recompute()
    original = obj.Shape.Faces[0]
    assert len(original.Wires) == 2 and seam_count(original) == 2
    a, b, c, d = original.Surface.bounds()
    result = call(
        "surface_set_parameter_range",
        object_name=obj.Name,
        u_min=2,
        u_max=5,
        v_min=7,
        v_max=11,
    )
    check(
        original,
        result,
        lambda u, v: (2 + 3 * (u - a) / (b - a), 7 + 4 * (v - c) / (d - c)),
    )
    mapped = doc.getObject(result["name"]).Shape.Faces[0]
    # Check the removed regions explicitly, including the seam intersection.
    for u in (0.01, 3.14159, 6.27):
        assert not original.isPartOfDomain(u, 5)
        assert not mapped.isPartOfDomain(2 + 3 * (u - a) / (b - a), 9)
    persisted.append((result["name"], seam_count(mapped), False))
    # Opening only one side of a shared seam must fail, leaving no new object.
    cylinder = doc.getObject(opened_cylinder).Shape.Faces[0].Surface
    point = cylinder.getPole(1, 1)
    before = {obj.Name for obj in doc.Objects}
    call(
        "surface_edit_pole",
        object_name=opened_cylinder,
        u_index=1,
        v_index=1,
        x=point.x + 2,
        y=point.y,
        z=point.z,
        expect_success=False,
    )
    assert {obj.Name for obj in doc.Objects} == before
    filename = str(Path(output) / "surface_seams.FCStd")
    doc.recompute()
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    for name, seams, closed in persisted:
        face = doc.getObject(name).Shape.Faces[0]
        assert face.isValid() and seam_count(face) == seams
        assert Part.Shell([face]).isClosed() == closed
    return {
        "closed_sphere_and_torus": True,
        "seam_crossing_trim": True,
        "saved_outputs": len(persisted),
    }
