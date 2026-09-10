"""Machining clone transforms must update actual stock and generated motion."""

from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    call = tools_for(root, "cam")
    doc = App.newDocument("MachiningSetup")
    source = doc.addObject("Part::Feature", "Design")
    source.Shape = Part.makeBox(20, 10, 5)
    source.Placement.Base = App.Vector(10, 20, 30)
    design_placement = source.Placement.copy()
    call("cam_create_job", name="Job", model_names=["Design"])
    profile = call("cam_add_operation", job_name="Job", operation="Profile")
    op = doc.getObject(profile["name"])
    initial = call("cam_inspect_setup", job_name="Job")
    clone = doc.getObject(initial["models"][0]["name"])
    assert initial["models"][0]["source"] == "Design"
    before = op.Path.toGCode()
    moved = call("cam_transform_setup", job_name="Job", translation=[7, 0, -30])
    assert moved["stock_mode"] == "refit" and moved["recomputed"], moved
    assert clone.Placement.Base == App.Vector(17, 20, 0)
    assert source.Placement.isSame(design_placement, 1e-10)
    assert op.Path.toGCode() != before
    stock = moved["stock"]["bounds"]
    assert stock["min"] == [16, 19, -1] and stock["max"] == [38, 31, 6], stock
    xs = [
        command.Parameters["X"]
        for command in op.Path.Commands
        if command.Name in ("G1", "G2", "G3") and "X" in command.Parameters
    ]
    assert min(xs) >= 14 and max(xs) > 37, (min(xs), max(xs))
    assert max(command.Parameters.get("Z", -1000) for command in op.Path.Commands) < 20
    turned = call(
        "cam_transform_setup",
        job_name="Job",
        rotation_angle=90,
        rotation_center=[17, 20, 0],
    )
    bounds = turned["models"][0]["bounds"]
    for actual, expected in zip(bounds["min"] + bounds["max"], [7, 20, 0, 17, 40, 5]):
        assert abs(actual - expected) < 1e-7, turned
    assert source.Placement.isSame(design_placement, 1e-10)
    # Add a second setup instance of the same design and remove it again.
    added = call("cam_add_job_models", job_name="Job", source_names=["Design"])
    second = added["added_models"][0]
    assert len(added["models"]) == 2 and second != clone.Name
    call(
        "cam_transform_setup",
        job_name="Job",
        model_names=[second],
        translation=[40, 0, -30],
    )
    assert doc.getObject(second).Placement.Base == App.Vector(50, 20, 0)
    # An explicit operation reference must prevent silently deleting its model.
    op.Base = [(doc.getObject(second), ["Face6"])]
    doc.recompute()
    failed = call(
        "cam_remove_job_model", job_name="Job", model_name=second, expect_success=False
    )
    assert "referenced" in failed["error"] and doc.getObject(second)
    op.Base = []
    doc.recompute()
    removed = call("cam_remove_job_model", job_name="Job", model_name=second)
    assert len(removed["models"]) == 1 and doc.Design and doc.getObject(second) is None
    call(
        "cam_remove_job_model",
        job_name="Job",
        model_name=clone.Name,
        expect_success=False,
    )
    # Explicit stock transforms compose once and don't touch design sources.
    call(
        "cam_set_stock",
        job_name="Job",
        stock_type="Box",
        dimensions={"Length": 50, "Width": 50, "Height": 10},
    )
    stock_before = doc.Job.Stock.Placement.copy()
    model_before = clone.Placement.copy()
    call(
        "cam_transform_setup",
        job_name="Job",
        translation=[3, 4, 5],
        rotation_angle=30,
        stock_mode="transform",
    )
    delta = App.Placement(App.Vector(3, 4, 5), App.Rotation(App.Vector(0, 0, 1), 30))
    assert doc.Job.Stock.Placement.isSame(delta * stock_before, 1e-8)
    assert clone.Placement.isSame(delta * model_before, 1e-8)
    assert source.Placement.isSame(design_placement, 1e-10)
    before = clone.Placement.copy()
    objects = {obj.Name for obj in doc.Objects}
    for args in [
        dict(model_names=["Design"]),
        dict(rotation_axis=[0, 0, 0]),
        dict(stock_mode="refit"),
        dict(model_names=[]),
    ]:
        call("cam_transform_setup", job_name="Job", expect_success=False, **args)
        assert clone.Placement.isSame(before, 1e-10)
        assert {obj.Name for obj in doc.Objects} == objects
    clone_name = clone.Name
    filename = str(Path(output) / "setup.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    doc.recompute()
    saved = call("cam_inspect_setup", job_name="Job")
    assert doc.getObject(clone_name).Placement.isSame(before, 1e-8)
    assert doc.Design.Placement.isSame(design_placement, 1e-10)
    return {
        "models": len(saved["models"]),
        "clone_translation_and_rotation": True,
        "stock_refit": True,
        "operation_motion_updated": True,
        "source_preserved": True,
    }
