"""Selected trimmed spline editing, periodicity and parameter transformations."""

from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    call = tools_for(root, "surface")
    doc = App.newDocument("TrimmedSplineEdits")
    # Use exact degree-1 plane control geometry, retaining a circular UV hole.
    call(
        "surface_create_bspline",
        name="Plane",
        poles=[[[0, 0, 0], [0, 20, 0]], [[10, 0, 0], [10, 20, 0]]],
        u_degree=1,
        v_degree=1,
    )
    hole = Part.Face(Part.Wire(Part.makeCircle(1, App.Vector(5, 10, 0))))
    trimmed = doc.Plane.Shape.cut(hole).Faces[0]
    trimmed.reverse()
    compound = doc.addObject("Part::Feature", "Compound")
    compound.Shape = Part.makeCompound([Part.makePlane(1, 1), trimmed])
    compound.Placement = App.Placement(
        App.Vector(100, 20, 30), App.Rotation(App.Vector(0, 0, 1), 30)
    )
    doc.recompute()
    face = compound.Shape.Faces[1]
    info = call("surface_surface_info", object_name="Compound", face_index=1)
    net = call("surface_control_net", object_name="Compound", face_index=1)
    assert (
        info["surface_type"] == "BSplineSurface" and info["orientation"] == "Reversed"
    )
    assert net["NbUPoles"] == 2
    pole = call(
        "surface_get_pole", object_name="Compound", face_index=1, u_index=1, v_index=1
    )
    assert (App.Vector(*pole["pole"]) - compound.Placement.Base).Length < 1e-8
    source = compound.Shape
    edited = call(
        "surface_edit_pole",
        object_name="Compound",
        face_index=1,
        u_index=1,
        v_index=1,
        x=100,
        y=20,
        z=32,
    )
    editface = doc.getObject(edited["name"]).Shape.Faces[0]
    assert len(editface.Wires) == 2 and editface.isValid()
    assert editface.Orientation == face.Orientation
    assert not editface.isPartOfDomain(0.5, 0.5)
    assert editface.Surface.getPole(1, 1).z == 32
    assert compound.Shape.isSame(source)
    weighted = call(
        "surface_edit_weight",
        object_name="Compound",
        face_index=1,
        u_index=1,
        v_index=1,
        weight=2,
    )
    assert len(doc.getObject(weighted["name"]).Shape.Wires) == 2
    assert doc.getObject(weighted["name"]).Shape.Faces[0].Surface.getWeight(1, 1) == 2
    # Elevation and affine knot rescaling preserve geometry and hole topology.
    raised = call(
        "surface_increase_degree",
        object_name="Compound",
        face_index=1,
        u_degree=3,
        v_degree=4,
    )
    assert len(doc.getObject(raised["name"]).Shape.Wires) == 2
    scaled = call(
        "surface_set_parameter_range",
        object_name="Compound",
        face_index=1,
        u_min=2,
        u_max=4,
        v_min=5,
        v_max=11,
    )
    sf = doc.getObject(scaled["name"]).Shape.Faces[0]
    assert len(sf.Wires) == 2 and sf.Orientation == face.Orientation
    assert max(abs(a - b) for a, b in zip(sf.ParameterRange, [2, 4, 5, 11])) < 1e-8
    for u, v in ((0.1, 0.2), (0.8, 0.3), (0.2, 0.9)):
        assert (sf.valueAt(2 + 2 * u, 5 + 6 * v) - face.valueAt(u, v)).Length < 1e-7
        assert sf.normalAt(2 + 2 * u, 5 + 6 * v).dot(face.normalAt(u, v)) > 0.99999
    assert not sf.isPartOfDomain(3, 8)
    assert abs(sf.Area - face.Area) < 1e-5
    exchanged = call("surface_exchange_uv", object_name="Compound", face_index=1)
    ef = doc.getObject(exchanged["name"]).Shape.Faces[0]
    assert len(ef.Wires) == 2 and ef.isValid()
    for u, v in ((0.1, 0.2), (0.8, 0.3), (0.2, 0.9)):
        assert (ef.valueAt(v, u) - face.valueAt(u, v)).Length < 1e-7
        assert ef.normalAt(v, u).dot(face.normalAt(u, v)) > 0.99999
    assert not ef.isPartOfDomain(0.5, 0.5)
    refit = call(
        "surface_reparameterize",
        object_name=scaled["name"],
        u_poles=4,
        v_poles=5,
        tolerance=1e-6,
    )
    rf = doc.getObject(refit["name"]).Shape.Faces[0]
    assert refit["sampled_max_error_mm"] < 1e-7 and len(rf.Wires) == 2
    for u, v in ((0.1, 0.2), (0.8, 0.3), (0.2, 0.9)):
        assert (rf.valueAt(u, v) - face.valueAt(u, v)).Length < 1e-7
    assert abs(rf.Area - face.Area) < 1e-5
    inserted = call(
        "surface_insert_knot",
        object_name="Compound",
        face_index=1,
        direction="U",
        parameter=0.3,
    )
    knotted = call(
        "surface_set_knot",
        object_name=inserted["name"],
        direction="U",
        index=2,
        value=0.4,
    )
    assert len(doc.getObject(knotted["name"]).Shape.Wires) == 2
    removed = call(
        "surface_remove_knot", object_name=inserted["name"], direction="U", index=2
    )
    assert len(doc.getObject(removed["name"]).Shape.Wires) == 2
    # Periodic cylinder with an origin shift then clamped/open conversion.
    cylinder = doc.addObject("Part::Feature", "Cylinder")
    cylinder.Shape = Part.makeCylinder(5, 10).Faces[0].toNurbs()
    doc.recompute()
    original = cylinder.Shape.Faces[0]
    periodic = call(
        "surface_set_periodic",
        object_name="Cylinder",
        direction="U",
        periodic=True,
        origin_index=2,
    )
    pf = doc.getObject(periodic["name"]).Shape.Faces[0]
    assert periodic["periodic"] and pf.isValid()
    for u, v in ((0, 0), (1, 3), (3, 5), (6, 10)):
        assert (pf.valueAt(u, v) - original.valueAt(u, v)).Length < 1e-7
    opened = call(
        "surface_set_periodic",
        object_name=periodic["name"],
        direction="U",
        periodic=False,
    )
    of = doc.getObject(opened["name"]).Shape.Faces[0]
    assert not opened["periodic"] and of.isValid()
    for u, v in ((0, 0), (1, 3), (3, 5), (6, 10)):
        assert (of.valueAt(u, v) - original.valueAt(u, v)).Length < 1e-7, (
            u,
            v,
            of.Surface.bounds(),
            of.ParameterRange,
            list(of.valueAt(u, v)),
            list(original.valueAt(u, v)),
        )
    # V-periodic and doubly periodic surfaces retain both coordinate directions.
    swapped = call("surface_exchange_uv", object_name=periodic["name"])
    vperiod = call(
        "surface_set_periodic",
        object_name=swapped["name"],
        direction="V",
        periodic=True,
        origin_index=2,
    )
    vsource = doc.getObject(vperiod["name"]).Shape.Faces[0]
    vopened = call(
        "surface_set_periodic",
        object_name=vperiod["name"],
        direction="V",
        periodic=False,
    )
    vface = doc.getObject(vopened["name"]).Shape.Faces[0]
    for u, v in ((0, 0), (3, 1), (5, 3), (10, 6)):
        assert (vsource.valueAt(u, v) - vface.valueAt(u, v)).Length < 1e-7
    torus = doc.addObject("Part::Feature", "Torus")
    torus.Shape = Part.makeTorus(10, 2).Faces[0].toNurbs()
    doc.recompute()
    tu = call("surface_set_periodic", object_name="Torus", direction="U", periodic=True)
    tv = call(
        "surface_set_periodic", object_name=tu["name"], direction="V", periodic=True
    )
    tf = doc.getObject(tv["name"]).Shape.Faces[0]
    torus_open = call(
        "surface_set_periodic", object_name=tv["name"], direction="U", periodic=False
    )
    tof = doc.getObject(torus_open["name"]).Shape.Faces[0]
    assert not tof.Surface.isUPeriodic() and tof.Surface.isVPeriodic(), (tv, torus_open)
    for u, v in ((0, 0), (0.7, 1.3), (3, 5), (6, 6)):
        assert (tf.valueAt(u, v) - tof.valueAt(u, v)).Length < 1e-7
    # Curved interpolation and a tolerance failure that leaves no output.
    samples = [
        [[float(i), float(j), float(i * j) / 4] for j in range(4)] for i in range(4)
    ]
    call("surface_create_bspline", name="Curved", poles=samples)
    fitted = call(
        "surface_reparameterize",
        object_name="Curved",
        u_poles=5,
        v_poles=6,
        tolerance=1e-6,
    )
    assert fitted["sampled_max_error_mm"] < 1e-7
    before_fit = {obj.Name for obj in doc.Objects}
    call(
        "surface_reparameterize",
        object_name="Cylinder",
        u_poles=3,
        v_poles=3,
        tolerance=1e-9,
        expect_success=False,
    )
    assert {obj.Name for obj in doc.Objects} == before_fit
    call("surface_create_bezier", name="Bezier", poles=samples)
    bezier = call(
        "surface_increase_degree", object_name="Bezier", u_degree=4, v_degree=5
    )
    assert (bezier["u_degree"], bezier["v_degree"]) == (4, 5)
    before = {obj.Name for obj in doc.Objects}
    for tool, args in [
        ("surface_set_periodic", dict(object_name="Plane", direction="U")),
        (
            "surface_set_periodic",
            dict(object_name=periodic["name"], direction="U", origin_index=1000),
        ),
        (
            "surface_get_pole",
            dict(object_name="Compound", face_index=0, u_index=1, v_index=1),
        ),
        (
            "surface_edit_pole",
            dict(
                object_name="Compound",
                face_index=50,
                u_index=1,
                v_index=1,
                x=0,
                y=0,
                z=0,
            ),
        ),
        (
            "surface_edit_weight",
            dict(object_name="Plane", u_index=1, v_index=1, weight=-1),
        ),
        ("surface_set_parameter_range", dict(object_name="Plane", u_min=2, u_max=1)),
        ("surface_insert_knot", dict(object_name="Plane", direction="U", parameter=20)),
    ]:
        call(tool, expect_success=False, **args)
        assert {obj.Name for obj in doc.Objects} == before
    file_path = str(Path(output) / "trimmed_splines.FCStd")
    doc.recompute()
    doc.saveAs(file_path)
    App.closeDocument(doc.Name)
    doc = App.openDocument(file_path)
    assert len(doc.getObject(scaled["name"]).Shape.Wires) == 2
    assert not doc.getObject(opened["name"]).Shape.Faces[0].Surface.isUPeriodic()
    return {
        "selected_face": 1,
        "trim_loops": 2,
        "parameter_range": [2, 4, 5, 11],
        "periodic_origin_open_preserved": True,
        "refit_sampled_error": refit["sampled_max_error_mm"],
    }
