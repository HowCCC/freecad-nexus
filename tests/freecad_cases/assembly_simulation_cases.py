"""Cylindrical dual motion and stale/failed simulation lifecycle checks."""

from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    call = tools_for(root, "assembly")
    doc = App.newDocument("CylindricalMotion")
    call("assembly_create", name="Asm")
    source = doc.addObject("Part::Feature", "Source")
    source.Shape = Part.makeBox(2, 2, 2)
    for name in ("Base", "Moving"):
        call(
            "assembly_insert_component",
            assembly_name="Asm",
            source_name="Source",
            instance_name=name,
        )
    call("assembly_ground_component", assembly_name="Asm", component_name="Base")
    joint = call(
        "assembly_add_constraint",
        assembly_name="Asm",
        constraint_type="Cylindrical",
        first="Base",
        second="Moving",
    )
    sim = call("assembly_create_simulation", assembly_name="Asm", end=1, step=0.1)
    angular = call(
        "assembly_add_motion",
        simulation_name=sim["name"],
        joint_name=joint["name"],
        motion_type="Angular",
        formula="pi/6*time",
    )
    linear = call(
        "assembly_add_motion",
        simulation_name=sim["name"],
        joint_name=joint["name"],
        motion_type="Linear",
        formula="5*time",
    )
    result = call("assembly_run_simulation", simulation_name=sim["name"])
    assert call("assembly_simulation_status", assembly_name="Asm")["valid"]
    source.ViewObject.ShapeColor = (0.2, 0.4, 0.6)
    import FreeCADGui as Gui

    Gui.updateGui()
    assert call("assembly_simulation_status", assembly_name="Asm")["valid"]
    call("assembly_get_frame", assembly_name="Asm", frame=0)
    start = doc.Moving.Placement.copy()
    call("assembly_get_frame", assembly_name="Asm", frame=result["frames"] - 1)
    end = doc.Moving.Placement.copy()
    assert abs(end.Base.z - start.Base.z - 5) < 1e-6, (start, end)
    angle = (start.Rotation.inverted() * end.Rotation).getYawPitchRoll()[0]
    assert abs(angle - 30) < 1e-6, angle
    call("assembly_get_frame", assembly_name="Asm", frame=-1, expect_success=False)
    call(
        "assembly_get_frame",
        assembly_name="Asm",
        frame=result["frames"],
        expect_success=False,
    )
    assert call("assembly_simulation_status", assembly_name="Asm")["valid"]
    # User formula edit leaves native frames present but they must be rejected.
    doc.getObject(linear["name"]).Formula = "6*time"
    stale = call("assembly_simulation_status", assembly_name="Asm")
    assert stale["status"] == "stale"
    assert any(
        row["object"].endswith("#" + linear["name"]) and "Formula" in row["properties"]
        for row in stale["changed_inputs"]
    ), stale
    call("assembly_get_frame", assembly_name="Asm", frame=0, expect_success=False)
    call("assembly_run_simulation", simulation_name=sim["name"])
    call("assembly_get_frame", assembly_name="Asm", frame=0)
    # Same bounding box, area and volume, but different underlying geometry.
    # A rigid displacement of source geometry must invalidate frame inputs too.
    source.Shape = Part.makeBox(2, 2, 2, App.Vector(1, 0, 0))
    assert not call("assembly_simulation_status", assembly_name="Asm")["valid"]
    call("assembly_run_simulation", simulation_name=sim["name"])
    doc.getObject(linear["name"]).Formula = ""
    failed = call(
        "assembly_run_simulation", simulation_name=sim["name"], expect_success=False
    )
    assert "empty" in failed["error"]
    assert (
        call("assembly_simulation_status", assembly_name="Asm")["status"]
        == "not_generated"
    )
    call("assembly_get_frame", assembly_name="Asm", frame=0, expect_success=False)
    doc.getObject(linear["name"]).Formula = "6*time"
    call("assembly_run_simulation", simulation_name=sim["name"])
    # Native parser failure invalidates existing frames as well.
    doc.getObject(linear["name"]).Formula = "sin("
    call("assembly_run_simulation", simulation_name=sim["name"], expect_success=False)
    assert not call("assembly_simulation_status", assembly_name="Asm")["valid"]
    doc.getObject(linear["name"]).Formula = "6*time"
    call("assembly_run_simulation", simulation_name=sim["name"])
    filename = str(Path(output) / "cylindrical.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    assert not call("assembly_simulation_status", assembly_name="Asm")["valid"]
    call("assembly_run_simulation", simulation_name=sim["name"])
    dependent = doc.addObject("App::FeaturePython", "MotionUser")
    dependent.addProperty("App::PropertyLink", "Target")
    dependent.Target = doc.getObject(linear["name"])
    before = {obj.Name for obj in doc.Objects}
    failed = call(
        "assembly_remove_joint", joint_name=joint["name"], expect_success=False
    )
    assert "dependent" in failed["error"]
    assert {obj.Name for obj in doc.Objects} == before
    assert dependent.Target.Name == linear["name"]
    doc.removeObject(dependent.Name)
    removed = call("assembly_remove_joint", joint_name=joint["name"])
    assert set(removed["removed_motions"]) == {angular["name"], linear["name"]}
    assert not doc.getObject(sim["name"]).Group
    return {
        "linear_mm": 5,
        "angular_degrees": angle,
        "stale_formula_rejected": True,
        "failed_generation_invalidated": True,
        "dependent_motions_removed": True,
    }
