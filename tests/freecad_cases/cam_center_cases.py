"""Native rotation metadata, recompute/reopen staleness and dressed bases."""

from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    doc = App.newDocument("RotationCenter")
    source = doc.addObject("Part::Feature", "PartSource")
    source.Shape = Part.makeBox(20, 10, 5)
    call = tools_for(root, "cam")
    call("cam_create_job", name="Job", model_names=["PartSource"])
    profile = call("cam_add_operation", job_name="Job", operation="Profile")
    base = doc.getObject(profile["name"])
    dressed = call(
        "cam_add_dressup",
        dressup_name="Boundary",
        dressup="Boundary",
        base_operation=base.Name,
    )
    outer = doc.getObject(dressed["name"])
    original = {obj.Name: obj.Path.toGCode() for obj in [doc.Job, base, outer]}
    model_placements = {
        obj.Name: obj.Placement.copy() for obj in [source] + list(doc.Job.Model.Group)
    }
    assert (
        call("cam_inspect_rotation_center", job_name="Job")["requested_center"] is None
    )
    result = call("cam_set_center_of_rotation", job_name="Job", x=4, y=5, z=-6)
    assert result["consistent"] and result["commands_modified"] is False, result
    assert {row["name"] for row in result["paths"]} == {
        doc.Job.Name,
        base.Name,
        outer.Name,
    }, result
    assert all(row["center"] == [4, 5, -6] for row in result["paths"]), result
    for obj in [doc.Job, base, outer]:
        assert obj.Path.toGCode() == original[obj.Name]
    for name, placement in model_placements.items():
        assert doc.getObject(name).Placement.isSame(placement, 1e-12)
    base.touch()
    doc.recompute()
    stale = call("cam_inspect_rotation_center", job_name="Job")
    assert not stale["consistent"] and stale["requested_center"] == [4, 5, -6], stale
    assert call("cam_set_center_of_rotation", job_name="Job", x=4, y=5, z=-6)[
        "consistent"
    ]
    filename = str(Path(output) / "centers.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    reopened = call("cam_inspect_rotation_center", job_name="Job")
    assert reopened["requested_center"] == [4, 5, -6], reopened
    restored = call("cam_set_center_of_rotation", job_name="Job", x=4, y=5, z=-6)
    assert restored["consistent"], restored
    if App.GuiUp:
        op = call("cam_add_operation", job_name="Job", operation="Profile")
        mapped = call(
            "cam_add_dressup",
            dressup_name="Rotary",
            dressup="AxisMap",
            base_operation=op["name"],
            parameters={"Radius": 10.0, "AxisMap": "Y->A"},
        )
        assert mapped["command_count"] > 0
        call(
            "cam_set_center_of_rotation",
            job_name="Job",
            x=0,
            y=0,
            z=-5,
            expect_success=False,
        )
        matched = call("cam_set_center_of_rotation", job_name="Job", x=0, y=0, z=-10)
        assert matched["consistent"] and all(
            row["center"] == [0, 0, -10] for row in matched["paths"]
        ), matched
    return {
        "hidden_base_included": True,
        "regeneration_staleness_detected": True,
        "reopened_requested_center": reopened["requested_center"],
        "models_and_commands_preserved": True,
    }
