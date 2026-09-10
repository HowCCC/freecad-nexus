"""Native flexible joint values must follow source edits before solving."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import UtilsAssembly
    from workflows import tools_for

    call = tools_for(root, "assembly")
    doc = App.newDocument("InstanceParameterSync")
    prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Assembly")
    prefs.SetBool("SolveOnRecompute", False)
    doc.addObject("Part::Box", "Geometry")
    call("assembly_create", name="Source")
    for name in ("Base", "Moving"):
        call(
            "assembly_insert_component",
            assembly_name="Source",
            source_name="Geometry",
            instance_name=name,
        )
    joint = call(
        "assembly_add_constraint",
        assembly_name="Source",
        constraint_type="Angle",
        first="Base",
        second="Moving",
        value=35,
        solve=False,
    )
    original = doc.getObject(joint["name"])
    call("assembly_create", name="Top")
    for name in ("One", "Two"):
        call(
            "assembly_insert_component",
            assembly_name="Top",
            source_name="Source",
            instance_name=name,
        )
        call("assembly_set_component_rigid", instance_name=name, rigid=False)
        info = call("assembly_inspect_component", instance_name=name)
        base = next(
            row["name"]
            for row in info["components"]
            if row["source"]["object"] == "Base"
        )
        call("assembly_ground_component", assembly_name="Top", component_name=base)
    call("assembly_set_joint_state", joint_name=original.Name, angle=65, solve=False)
    status = call("assembly_instance_sync_status", assembly_name="Top")
    assert not status["current"] and len(status["joints"]) == 2, status
    assert all(
        any(d["property"] == "Angle" for d in row["differences"])
        for row in status["joints"]
    )
    poses = {
        obj.Name: obj.Placement.copy()
        for obj in doc.Objects
        if hasattr(obj, "Placement")
    }
    sync = call("assembly_sync_instances", assembly_name="Top")
    assert sync["current"] and len(sync["corrected"]) == 2, sync
    assert all(
        doc.getObject(name).Placement.isSame(plc, 1e-9) for name, plc in poses.items()
    )
    result = call("assembly_solve", assembly_name="Top")
    assert result["solved"], result
    for row in sync["joints"]:
        copied = doc.getObject(row["copy_joint"])
        one = UtilsAssembly.getJcsGlobalPlc(copied.Placement1, copied.Reference1)
        two = UtilsAssembly.getJcsGlobalPlc(copied.Placement2, copied.Reference2)
        axis = (one.inverse() * two).Rotation.multVec(App.Vector(0, 0, 1))
        assert abs(axis.z - math.cos(math.radians(65))) < 1e-7
    # Subsequent source edits are synchronized automatically by MCP solve.
    call("assembly_set_joint_state", joint_name=original.Name, angle=50, solve=False)
    result = call("assembly_solve", assembly_name="Top")
    assert (
        result["solved"] and len(result["instance_synchronization"]["corrected"]) == 2
    ), result
    assert call("assembly_instance_sync_status", assembly_name="Top")["current"]
    # Detached placements are omitted by native synchronization too.
    call(
        "assembly_set_joint_connector",
        joint_name=original.Name,
        connector=1,
        detach=True,
        x=2,
        y=3,
        z=4,
        angle=10,
    )
    status = call("assembly_instance_sync_status", assembly_name="Top")
    assert not status["current"], status
    sync = call("assembly_sync_instances", assembly_name="Top")
    assert sync["current"] and any(
        row["property"] == "Placement1" for row in sync["corrected"]
    )
    for row in sync["joints"]:
        assert doc.getObject(row["copy_joint"]).Placement1.isSame(
            original.Placement1, 1e-8
        )
    # A new source joint changes copy count; explicit sync must use native order.
    call(
        "assembly_set_joint_state",
        joint_name=original.Name,
        suppressed=True,
        solve=False,
    )
    sync = call("assembly_sync_instances", assembly_name="Top")
    assert sync["current"] and not sync["joints"], sync
    call(
        "assembly_set_joint_state",
        joint_name=original.Name,
        suppressed=False,
        solve=False,
    )
    sync = call("assembly_sync_instances", assembly_name="Top")
    assert sync["current"] and len(sync["joints"]) == 2
    filename = str(Path(output) / "instance_sync.FCStd")
    doc.recompute()
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    sync = call("assembly_sync_instances", assembly_name="Top")
    assert sync["current"] and len(sync["joints"]) == 2
    return {
        "angle_propagated": True,
        "detached_connector_propagated": True,
        "two_independent_instances": True,
        "suppression_lifecycle": True,
    }
