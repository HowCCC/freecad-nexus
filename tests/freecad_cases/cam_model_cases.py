"""Native CAM source replacement with explicit operation topology migration."""

from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    call = tools_for(root, "cam")
    doc = App.newDocument("ModelReplacement")
    old = doc.addObject("Part::Feature", "OldDesign")
    old.Shape = Part.makeBox(12, 10, 5)
    old.Placement.Base = App.Vector(10, 20, 30)
    new = doc.addObject("Part::Feature", "NewDesign")
    new.Shape = Part.makeBox(18, 15, 8)
    new.Placement = App.Placement(
        App.Vector(30, 50, 60), App.Rotation(App.Vector(0, 0, 1), 20)
    )
    old_pose, new_pose = old.Placement.copy(), new.Placement.copy()
    call("cam_create_job", name="Job", model_names=[old.Name])
    clone = doc.Job.Model.Group[0]
    call("cam_transform_setup", job_name="Job", translation=[-10, -20, -30])
    profile = call(
        "cam_add_operation",
        job_name="Job",
        operation="Profile",
        base_object=clone.Name,
        base_subelements=["Face6"],
    )
    op = doc.getObject(profile["name"])
    assert profile["path_ready"]
    code = op.Path.toGCode()
    refs = call("cam_inspect_model_references", job_name="Job", model_name=clone.Name)
    assert (
        refs["required_subelements"] == ["Face6"] and not refs["blocked_dependencies"]
    ), refs
    before = {obj.Name for obj in doc.Objects}
    for mapping in ({}, {"Face6": "Face99"}, {"Face6": "Edge1"}):
        call(
            "cam_replace_job_model",
            job_name="Job",
            model_name=clone.Name,
            source_name=new.Name,
            subelement_map=mapping,
            expect_success=False,
        )
        assert clone.Objects[0] is old and {obj.Name for obj in doc.Objects} == before
        assert op.Path.toGCode() == code
    result = call(
        "cam_replace_job_model",
        job_name="Job",
        model_name=clone.Name,
        source_name=new.Name,
        subelement_map={"Face6": "Face6"},
    )
    assert clone.Objects[0] is new and clone.Placement.isSame(App.Placement(), 1e-9), (
        result
    )
    assert op.Base[0][0] is clone and list(op.Base[0][1]) == ["Face6"]
    assert op.Path.toGCode() != code and result["operations"][0]["path_ready"]
    bb = clone.Shape.optimalBoundingBox(False, False)
    assert abs(bb.XLength - 18) < 1e-7 and abs(bb.ZLength - 8) < 1e-7, str(bb)
    assert old.Placement.isSame(old_pose, 1e-10) and new.Placement.isSame(
        new_pose, 1e-10
    )
    assert result["updated_properties"] == [{"object": op.Name, "property": "Base"}]
    # Preserve the old-source-to-setup delta when replacing back.
    saved = clone.Placement.copy()
    expected = saved * new_pose.inverse() * old_pose
    result = call(
        "cam_replace_job_model",
        job_name="Job",
        model_name=clone.Name,
        source_name=old.Name,
        subelement_map={"Face6": "Face6"},
        placement_mode="preserve_transform",
    )
    assert clone.Objects[0] is old and clone.Placement.isSame(expected, 1e-8)
    assert result["operations"][0]["path_ready"]
    # Explicitly rebind the operation to a different valid face.
    result = call(
        "cam_replace_job_model",
        job_name="Job",
        model_name=clone.Name,
        source_name=new.Name,
        subelement_map={"Face6": "Face5"},
    )
    assert list(op.Base[0][1]) == ["Face5"] and clone.Objects[0] is new
    # Unrelated document users cannot be silently redirected to a new design.
    link = doc.addObject("App::Link", "ExternalConsumer")
    link.LinkedObject = clone
    doc.recompute()
    info = call("cam_inspect_model_references", job_name="Job", model_name=clone.Name)
    assert info["blocked_dependencies"], info
    call(
        "cam_replace_job_model",
        job_name="Job",
        model_name=clone.Name,
        source_name=old.Name,
        subelement_map={"Face5": "Face6"},
        expect_success=False,
    )
    assert clone.Objects[0] is new and link.LinkedObject is clone
    doc.removeObject(link.Name)
    # Automatic Base selection also updates; there is no explicit topology map.
    op.Base = []
    doc.recompute()
    call(
        "cam_replace_job_model",
        job_name="Job",
        model_name=clone.Name,
        source_name=old.Name,
    )
    assert clone.Objects[0] is old and op.Path.Commands
    # Force a post-mutation failure: a vertical face has no XY profile area.
    sphere = doc.addObject("Part::Feature", "UnsupportedDesign")
    sphere.Shape = Part.makePlane(3, 3, App.Vector(), App.Vector(1, 0, 0))
    doc.recompute()
    op.Base = [(clone, ["Face6"])]
    doc.recompute()
    previous_path = op.Path.toGCode()
    previous_placement = clone.Placement.copy()
    before = {obj.Name for obj in doc.Objects}
    call(
        "cam_replace_job_model",
        job_name="Job",
        model_name=clone.Name,
        source_name=sphere.Name,
        subelement_map={"Face6": "Face1"},
        expect_success=False,
    )
    assert clone.Objects[0] is old and clone.Placement.isSame(previous_placement, 1e-9)
    assert list(op.Base[0][1]) == ["Face6"] and op.Path.toGCode() == previous_path
    assert op.isValid(), op.getStatusString()
    assert {obj.Name for obj in doc.Objects} == before
    # A hidden dressed base retains its reference and regenerates with its Job.
    dress = call(
        "cam_add_dressup",
        dressup_name="BoundaryDress",
        dressup="Boundary",
        base_operation=op.Name,
    )
    dressed = doc.getObject(dress["name"])
    old_dress_code = dressed.Path.toGCode()
    call(
        "cam_replace_job_model",
        job_name="Job",
        model_name=clone.Name,
        source_name=new.Name,
        subelement_map={"Face6": "Face6"},
    )
    assert clone.Objects[0] is new and dressed.Base is op and op.Base[0][0] is clone
    assert dressed.Path.toGCode() != old_dress_code and dressed.isValid()
    call(
        "cam_replace_job_model",
        job_name="Job",
        model_name=clone.Name,
        source_name=old.Name,
        subelement_map={"Face6": "Face6"},
    )
    second = call("cam_add_job_models", job_name="Job", source_names=[new.Name])[
        "added_models"
    ][0]
    call(
        "cam_transform_setup",
        job_name="Job",
        model_names=[second],
        translation=[0, 0, -60],
    )
    assigned = call(
        "cam_set_operation_bases",
        operation_name=op.Name,
        bases=[
            {"object": old.Name, "subelements": ["Face6"]},
            {"object": second, "subelements": ["Face6"]},
        ],
    )
    assert assigned["path_ready"] and len(op.Base) == 2 and dressed.isValid()
    prior_bases = list(op.Base)
    for bases in (
        [{"object": second, "subelements": ["Face999"]}],
        [{"object": old.Name}, {"object": clone.Name}],
    ):
        call(
            "cam_set_operation_bases",
            operation_name=op.Name,
            bases=bases,
            expect_success=False,
        )
        assert list(op.Base) == prior_bases and op.isValid()
    call("cam_set_operation_base", operation_name=op.Name)
    assert not op.Base
    call(
        "cam_set_operation_base",
        operation_name=op.Name,
        base_object=old.Name,
        subelements=["Face6"],
    )
    assert op.Base[0][0] is clone and list(op.Base[0][1]) == ["Face6"]
    call("cam_remove_job_model", job_name="Job", model_name=second)
    filename = str(Path(output) / "replaced_model.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    doc.recompute()
    info = call(
        "cam_inspect_model_references",
        job_name="Job",
        model_name=doc.Job.Model.Group[0].Name,
    )
    assert info["source"] == "OldDesign" and info["required_subelements"] == ["Face6"]
    return {
        "clone_identity_retained": True,
        "explicit_base_remapped": True,
        "sources_preserved": True,
        "rollback_after_recompute": True,
    }
