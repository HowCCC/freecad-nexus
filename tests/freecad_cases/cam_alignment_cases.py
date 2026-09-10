"""Geometry-selected machining axes/origins and group centering in stock."""

from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    call = tools_for(root, "cam")
    doc = App.newDocument("SetupAlignment")
    source = doc.addObject("Part::Feature", "TiltedDesign")
    source.Shape = Part.makeBox(20, 10, 5)
    source.Placement = App.Placement(
        App.Vector(10, 20, 30), App.Rotation(App.Vector(1, 2, 3), 47)
    )
    original = source.Placement.copy()
    call("cam_create_job", name="Job", model_names=[source.Name])
    clone = doc.Job.Model.Group[0]
    # Local Face6 is the top face; world normal includes the source rotation.
    reference = call(
        "cam_inspect_setup_reference",
        job_name="Job",
        reference_object=clone.Name,
        subelement="Face6",
    )
    assert (
        App.Vector(*reference["direction"])
        - original.Rotation.multVec(App.Vector(0, 0, 1))
    ).Length < 1e-7, reference
    pivot = list(clone.Shape.Faces[5].CenterOfMass)
    aligned = call(
        "cam_align_setup",
        job_name="Job",
        reference_object=clone.Name,
        subelement="Face6",
        rotation_center=pivot,
    )
    assert aligned["reference_after"]["direction"][2] > 1 - 1e-9
    assert (clone.Shape.Faces[5].CenterOfMass - App.Vector(*pivot)).Length < 1e-7
    assert source.Placement.isSame(original, 1e-10)
    before = clone.Placement.copy()
    call(
        "cam_align_setup",
        job_name="Job",
        reference_object=clone.Name,
        subelement="Face6",
        rotation_center=pivot,
    )
    assert clone.Placement.isSame(before, 1e-9)
    # Antiparallel alignment is an explicit request, never an implicit toggle.
    call(
        "cam_align_setup",
        job_name="Job",
        reference_object=clone.Name,
        subelement="Face6",
        target_axis=[0, 0, -1],
        rotation_center=pivot,
    )
    assert clone.Shape.Faces[5].normalAt(0, 0).z < -0.999999
    call(
        "cam_align_setup",
        job_name="Job",
        reference_object=clone.Name,
        subelement="Face6",
        rotation_center=pivot,
    )
    # Put the selected top face at Z=0, then the chosen vertex at X=Y=0.
    origin = call(
        "cam_set_setup_origin",
        job_name="Job",
        reference_object=clone.Name,
        subelement="Face6",
        axes="Z",
    )
    assert abs(clone.Shape.Faces[5].CenterOfMass.z) < 1e-7
    point = clone.Shape.Vertexes[0].Point
    call(
        "cam_set_setup_origin",
        job_name="Job",
        reference_object=clone.Name,
        subelement="Vertex1",
        axes="XY",
    )
    after_point = clone.Shape.Vertexes[0].Point
    assert (
        abs(after_point.x) < 1e-7
        and abs(after_point.y) < 1e-7
        and abs(after_point.z - point.z) < 1e-7
    )
    profile = call("cam_add_operation", job_name="Job", operation="Profile")
    op = doc.getObject(profile["name"])
    assert profile["path_ready"], profile
    sourced = call(
        "cam_add_operation",
        job_name="Job",
        operation="Profile",
        base_object=source.Name,
        base_subelements=["Face6"],
    )
    sourced_op = doc.getObject(sourced["name"])
    assert sourced_op.Base[0][0] is clone and sourced["base_model"] == clone.Name
    source_path_before = sourced_op.Path.toGCode()
    before_code = op.Path.toGCode()
    moved = call(
        "cam_set_setup_origin", job_name="Job", point=[0, 0, 0], target=[40, 0, 2]
    )
    assert op.Path.toGCode() != before_code and moved["operations"][0]["path_ready"]
    assert (
        sourced_op.Path.toGCode() != source_path_before
        and sourced_op.Base[0][0] is clone
    )
    assert source.Placement.isSame(original, 1e-10)
    # Round references use their geometric center/axis, not a curve endpoint.
    ring = doc.addObject("Part::Feature", "RoundDesign")
    ring.Shape = Part.makeCylinder(3, 7)
    ring.Placement = App.Placement(
        App.Vector(3, 8, 12), App.Rotation(App.Vector(0, 1, 0), 35)
    )
    call("cam_create_job", name="RoundJob", model_names=[ring.Name])
    round_clone = doc.RoundJob.Model.Group[0]
    circle_index = next(
        i
        for i, e in enumerate(round_clone.Shape.Edges, 1)
        if isinstance(e.Curve, Part.Circle)
    )
    circle = "Edge" + str(circle_index)
    ref = call(
        "cam_inspect_setup_reference",
        job_name="RoundJob",
        reference_object=round_clone.Name,
        subelement=circle,
    )
    assert ref["point_kind"] == "curve_center" and ref["direction_kind"] == "axis"
    expected = round_clone.Shape.getElement(circle).Curve.Center
    assert (App.Vector(*ref["point"]) - expected).Length < 1e-8
    call(
        "cam_align_setup",
        job_name="RoundJob",
        reference_object=round_clone.Name,
        subelement="Face1",
        target_axis=[1, 0, 0],
    )
    ref = call(
        "cam_inspect_setup_reference",
        job_name="RoundJob",
        reference_object=round_clone.Name,
        subelement="Face1",
    )
    assert App.Vector(*ref["direction"]).dot(App.Vector(1, 0, 0)) > 0.999999
    call(
        "cam_set_setup_origin",
        job_name="RoundJob",
        reference_object=round_clone.Name,
        subelement=circle,
    )
    assert round_clone.Shape.getElement(circle).Curve.Center.Length < 1e-7
    # Edge tangent sign follows reversed topology.
    edge = round_clone.Shape.getElement(circle)
    ref = call(
        "cam_inspect_setup_reference",
        job_name="RoundJob",
        reference_object=round_clone.Name,
        subelement=circle,
        point_mode="parameter",
        direction_mode="tangent",
        parameters=[0.25, 0.5],
    )
    t = edge.FirstParameter + 0.25 * (edge.LastParameter - edge.FirstParameter)
    tangent = edge.tangentAt(t)
    if edge.Orientation == "Reversed":
        tangent = -tangent
    assert App.Vector(*ref["direction"]).dot(tangent) > 0.999999
    # A compound selection centers as a group while explicit stock stays fixed.
    added = call("cam_add_job_models", job_name="RoundJob", source_names=[ring.Name])
    second = doc.getObject(added["added_models"][0])
    call(
        "cam_transform_setup",
        job_name="RoundJob",
        model_names=[second.Name],
        translation=[20, 0, 0],
    )
    call(
        "cam_set_stock",
        job_name="RoundJob",
        stock_type="Box",
        dimensions={"Length": 60, "Width": 50, "Height": 40},
    )
    stock_placement = doc.RoundJob.Stock.Placement.copy()
    relative = round_clone.Placement.inverse() * second.Placement
    z_before = round_clone.Placement.Base.z
    centered = call("cam_center_setup_in_stock", job_name="RoundJob", axes="XY")
    bounds = App.BoundBox()
    for model in doc.RoundJob.Model.Group:
        bounds.add(model.Shape.optimalBoundingBox(False, False))
    stock_center = doc.RoundJob.Stock.Shape.optimalBoundingBox(False, False).Center
    assert (
        abs(bounds.Center.x - stock_center.x) < 1e-7
        and abs(bounds.Center.y - stock_center.y) < 1e-7
    )
    assert abs(round_clone.Placement.Base.z - z_before) < 1e-8
    assert (round_clone.Placement.inverse() * second.Placement).isSame(relative, 1e-8)
    assert doc.RoundJob.Stock.Placement.isSame(stock_placement, 1e-10)
    # Failures preserve complete placements, stock and object inventory.
    before = {
        obj.Name: obj.Placement.copy()
        for obj in doc.Objects
        if hasattr(obj, "Placement")
    }
    names = {obj.Name for obj in doc.Objects}
    for tool, args in [
        (
            "cam_align_setup",
            dict(
                reference_object=round_clone.Name,
                subelement=circle,
                target_axis=[0, 0, 0],
            ),
        ),
        (
            "cam_align_setup",
            dict(reference_object=round_clone.Name, subelement="Vertex1"),
        ),
        (
            "cam_inspect_setup_reference",
            dict(reference_object=ring.Name, subelement="Face1"),
        ),
        (
            "cam_inspect_setup_reference",
            dict(reference_object=round_clone.Name, subelement="Face999"),
        ),
        ("cam_set_setup_origin", dict(point=[0, 0, 0], axes="XX")),
        (
            "cam_set_setup_origin",
            dict(point=[0, 0, 0], reference_object=round_clone.Name),
        ),
    ]:
        call(tool, job_name="RoundJob", expect_success=False, **args)
        assert names == {obj.Name for obj in doc.Objects}
        assert all(
            doc.getObject(name).Placement.isSame(plc, 1e-9)
            for name, plc in before.items()
        )
    file_path = str(Path(output) / "aligned_setup.FCStd")
    # A plane with a central hole has a well-defined normal even though its
    # center/UV midpoint is outside the material. A selected point must be inside.
    perforated = doc.addObject("Part::Feature", "Perforated")
    perforated.Shape = Part.makeBox(10, 10, 3).cut(
        Part.makeCylinder(2, 3, App.Vector(5, 5, 0))
    )
    call("cam_create_job", name="HoleJob", model_names=[perforated.Name])
    perforated_clone = doc.HoleJob.Model.Group[0]
    top = next(
        i
        for i, face in enumerate(perforated_clone.Shape.Faces, 1)
        if isinstance(face.Surface, Part.Plane) and face.normalAt(0, 0).z > 0.99
    )
    element = "Face" + str(top)
    ref = call(
        "cam_inspect_setup_reference",
        job_name="HoleJob",
        reference_object=perforated_clone.Name,
        subelement=element,
    )
    assert ref["direction"][2] > 0.999999
    call(
        "cam_align_setup",
        job_name="HoleJob",
        reference_object=perforated_clone.Name,
        subelement=element,
    )
    call(
        "cam_set_setup_origin",
        job_name="HoleJob",
        reference_object=perforated_clone.Name,
        subelement=element,
        point_mode="parameter",
        parameters=[0.5, 0.5],
        expect_success=False,
    )
    call(
        "cam_set_setup_origin",
        job_name="HoleJob",
        reference_object=perforated_clone.Name,
        subelement=element,
        point_mode="parameter",
        parameters=[0.1, 0.1],
    )
    saved = clone.Placement.copy()
    doc.saveAs(file_path)
    App.closeDocument(doc.Name)
    doc = App.openDocument(file_path)
    doc.recompute()
    assert doc.Job.Model.Group[0].Placement.isSame(saved, 1e-8)
    assert doc.TiltedDesign.Placement.isSame(original, 1e-10)
    return {
        "aligned_plane_and_cylinder": True,
        "vertex_face_circle_origins": True,
        "group_centered": True,
        "source_preserved": True,
    }
