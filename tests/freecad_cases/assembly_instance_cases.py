"""Two native flexible instances with isolated child poses and copied joints."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Assembly")
    prefs.SetBool("SolveOnRecompute", False)
    doc = App.newDocument("NestedSolver")
    call = tools_for(root, "assembly")
    call("assembly_create", name="SourceAsm")
    shape = doc.addObject("Part::Feature", "Shape")
    shape.Shape = Part.makeBox(2, 2, 2)
    for name in ("Base", "Moving"):
        call(
            "assembly_insert_component",
            assembly_name="SourceAsm",
            source_name="Shape",
            instance_name=name,
        )
    slider = call(
        "assembly_add_constraint",
        assembly_name="SourceAsm",
        constraint_type="Slider",
        first="Base",
        second="Moving",
        solve=False,
    )
    call("assembly_create", name="Top")
    for name, x in [("One", 10), ("Two", 30)]:
        call(
            "assembly_insert_component",
            assembly_name="Top",
            source_name="SourceAsm",
            instance_name=name,
        )
        doc.getObject(name).Placement.Base = App.Vector(x, 0, 0)

    def children(name):
        data = call("assembly_inspect_component", instance_name=name)
        return {
            row["source"]["object"]: doc.getObject(row["name"])
            for row in data["components"]
            if row["source"]
        }, data

    one, _ = children("One")
    two, _ = children("Two")
    source_poses = {
        name: doc.getObject(name).Placement.copy() for name in ("Base", "Moving")
    }
    for name in ("One", "Two"):
        result = call("assembly_set_component_rigid", instance_name=name, rigid=False)
        assert len(result["joint_copies"]) == 1 and result["placement"]["base"] == [
            0,
            0,
            0,
        ], result
    assert one["Base"].Placement.Base == App.Vector(10, 0, 0)
    assert two["Base"].Placement.Base == App.Vector(30, 0, 0)
    for mapping in (one, two):
        call(
            "assembly_ground_component",
            assembly_name="Top",
            component_name=mapping["Base"].Name,
        )
    one["Moving"].Placement.Base = App.Vector(14, 5, 7)
    two["Moving"].Placement.Base = App.Vector(33, -6, 11)
    result = call("assembly_solve", assembly_name="Top", store_previous=True)
    assert result["solved"] and len(result["joints"]) == 2, result
    for mapping, x, z in [(one, 10, 7), (two, 30, 11)]:
        p = mapping["Moving"].Placement.Base
        assert math.hypot(p.x - x, p.y) < 1e-7 and abs(p.z - z) < 1e-7, p
    assert all(
        doc.getObject(name).Placement.isSame(pose, 1e-10)
        for name, pose in source_poses.items()
    )
    # Only first mechanism changes; the second remains independently positioned.
    prior = two["Moving"].Placement.copy()
    one["Moving"].Placement.Base = App.Vector(20, 4, 15)
    same_mode = call(
        "assembly_set_component_rigid", instance_name="One", rigid=False, solve=True
    )
    assert not same_mode["changed"] and same_mode["solver"]["solved"], same_mode
    assert (one["Moving"].Placement.Base - App.Vector(10, 0, 15)).Length < 1e-7
    assert two["Moving"].Placement.isSame(prior, 1e-7)
    refs = call("assembly_validate_references", assembly_name="Top")
    assert refs["valid"] and refs["joint_count"] == 4, refs
    assert len(call("assembly_connection_graph", assembly_name="Top")["edges"]) == 4
    if App.GuiUp:
        simulation = call(
            "assembly_create_simulation", assembly_name="Top", end=1, step=0.1
        )
        one_data = call("assembly_inspect_component", instance_name="One")
        copied_joint = one_data["joint_copies"][0]["name"]
        call(
            "assembly_add_motion",
            simulation_name=simulation["name"],
            joint_name=copied_joint,
            motion_type="Linear",
            formula="3*time",
        )
        generated = call("assembly_run_simulation", simulation_name=simulation["name"])
        call("assembly_get_frame", assembly_name="Top", frame=0)
        first = one["Moving"].Placement.copy()
        call("assembly_get_frame", assembly_name="Top", frame=generated["frames"] - 1)
        assert (
            abs(one["Moving"].Placement.Base.z - one["Base"].Placement.Base.z - 3)
            < 1e-7
        ), {
            "first": str(first),
            "last": str(one["Moving"].Placement),
            "frames": generated,
        }
        assert two["Moving"].Placement.isSame(prior, 1e-7)
    rigid = call("assembly_set_component_rigid", instance_name="One", rigid=True)
    assert rigid["removed_grounding"] and not rigid["joint_copies"], rigid
    if App.GuiUp:
        assert (
            rigid["removed_motions"] and not doc.getObject(simulation["name"]).Group
        ), rigid
    assert doc.One.Rigid and one["Moving"].Placement.isSame(doc.Moving.Placement, 1e-7)
    assert two["Moving"].Placement.isSame(prior, 1e-7)
    # External connector migrates to/from the instance-owned child reference.
    call(
        "assembly_insert_component",
        assembly_name="Top",
        source_name="Shape",
        instance_name="External",
    )
    joint = call(
        "assembly_add_constraint",
        assembly_name="Top",
        constraint_type="Fixed",
        first="External",
        second="One",
        second_element=one["Base"].Name + ".",
        solve=False,
    )
    changed = call("assembly_set_component_rigid", instance_name="One", rigid=False)
    assert (
        changed["migrated_references"]
        and doc.getObject(joint["name"]).Reference2[0] is one["Base"]
    ), changed
    changed = call("assembly_set_component_rigid", instance_name="One", rigid=True)
    assert doc.getObject(joint["name"]).Reference2[0] is doc.One, changed
    assert (
        doc.getObject(joint["name"]).Reference2[1][0].startswith(one["Base"].Name + ".")
    )
    filename = str(Path(output) / "flexible.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    one_saved = call("assembly_inspect_component", instance_name="One")
    two_saved = call("assembly_inspect_component", instance_name="Two")
    assert (
        one_saved["rigid"]
        and not two_saved["rigid"]
        and len(two_saved["joint_copies"]) == 1
    )
    # Native copied joint parameters must follow source changes after insertion.
    source_joint = doc.getObject(slider["name"])
    source_joint.EnableLengthMax = True
    source_joint.LengthMax = 6
    doc.recompute()
    copied = doc.getObject(
        call("assembly_inspect_component", instance_name="Two")["joint_copies"][0][
            "name"
        ]
    )
    assert copied.EnableLengthMax and copied.LengthMax.Value == 6, (
        copied.EnableLengthMax,
        copied.LengthMax.Value,
    )
    source_joint.EnableLengthMax = False
    doc.recompute()
    # A second containing level uses generated child names at each level.
    call("assembly_create", name="Middle")
    call(
        "assembly_insert_component",
        assembly_name="Middle",
        source_name="SourceAsm",
        instance_name="Inner",
    )
    call("assembly_set_component_rigid", instance_name="Inner", rigid=False)
    call("assembly_create", name="DeepTop")
    call(
        "assembly_insert_component",
        assembly_name="DeepTop",
        source_name="Middle",
        instance_name="Outer",
    )
    call("assembly_set_component_rigid", instance_name="Outer", rigid=False)
    nested_link = next(
        obj for obj in doc.Outer.Group if obj.isDerivedFrom("Assembly::AssemblyLink")
    )
    nested = call("assembly_inspect_component", instance_name=nested_link.Name)
    assert nested["instance_path"] == "Outer." + nested_link.Name + ".", nested
    mapping = {
        row["source"]["object"]: doc.getObject(row["name"])
        for row in nested["components"]
        if row["source"]
    }
    call(
        "assembly_ground_component",
        assembly_name="DeepTop",
        component_name=mapping["Base"].Name,
    )
    mapping["Moving"].Placement.Base = App.Vector(6, 7, 12)
    exchange = call(
        "assembly_export_asmt",
        assembly_name="DeepTop",
        file_path=str(Path(output) / "nested.asmt"),
    )
    assert exchange["all_components_exported"] and exchange["promoted_nested_joints"], (
        exchange
    )
    assert doc.Name + "#" + nested["joint_copies"][0]["name"] in {
        row["name"] for row in exchange["content"]["joints"]
    }
    assert mapping["Moving"].Placement.Base == App.Vector(6, 7, 12)
    deep_solve = call("assembly_solve", assembly_name="DeepTop")
    assert deep_solve["solved"], deep_solve
    assert (
        deep_solve["promoted_nested_joints"] and not deep_solve["geometry_violations"]
    ), deep_solve
    assert (mapping["Moving"].Placement.Base - App.Vector(0, 0, 12)).Length < 1e-7, {
        "moving": str(mapping["Moving"].Placement),
        "base": str(mapping["Base"].Placement),
        "solve": deep_solve,
        "nested": nested,
    }
    if App.GuiUp:
        simulation = call(
            "assembly_create_simulation", assembly_name="DeepTop", end=1, step=0.1
        )
        joint_name = nested["joint_copies"][0]["name"]
        motion = call(
            "assembly_add_motion",
            simulation_name=simulation["name"],
            joint_name=joint_name,
            motion_type="Linear",
            formula="4*time",
        )
        before_names = {obj.Name for obj in doc.Objects}
        generated = call("assembly_run_simulation", simulation_name=simulation["name"])
        assert generated["promoted_nested_joints"] == [joint_name], generated
        assert {obj.Name for obj in doc.Objects} == before_names
        assert doc.getObject(motion["name"]).Joint[0].Name == joint_name
        call(
            "assembly_get_frame", assembly_name="DeepTop", frame=generated["frames"] - 1
        )
        assert (
            abs(
                mapping["Moving"].Placement.Base.z
                - mapping["Base"].Placement.Base.z
                - 4
            )
            < 1e-7
        )
    rigid_deep = call(
        "assembly_set_component_rigid", instance_name=nested_link.Name, rigid=True
    )
    assert rigid_deep["rigid"] and rigid_deep["removed_grounding"], rigid_deep
    flexible_deep = call(
        "assembly_set_component_rigid", instance_name=nested_link.Name, rigid=False
    )
    assert not flexible_deep["rigid"] and flexible_deep["joint_copies"], flexible_deep
    # Removing one flexible instance deletes its generated children and joints,
    # retaining the source, other instance and external referencing geometry.
    two_children = {
        row["name"]
        for row in call("assembly_inspect_component", instance_name="Two")["components"]
    }
    one_name = doc.One.Name
    if App.GuiUp:
        view = call(
            "assembly_create_exploded_view", assembly_name="Top", name="DeletionView"
        )
        kept = call(
            "assembly_add_exploded_step",
            view_name=view["name"],
            component_paths=["One.", "Two."],
            dx=5,
        )
        deleted = call(
            "assembly_add_exploded_step",
            view_name=view["name"],
            component_name="Two",
            dx=5,
        )
    removed = call(
        "assembly_remove_component", assembly_name="Top", component_name="Two"
    )
    assert all(doc.getObject(name) is None for name in two_children)
    assert (
        doc.getObject("Two") is None
        and doc.getObject(one_name)
        and doc.SourceAsm
        and doc.Base
        and doc.Moving
    )
    assert removed["removed_joints"]
    if App.GuiUp:
        assert (
            kept["step"] in removed["updated_exploded_steps"]
            and deleted["step"] in removed["removed_exploded_steps"]
        ), removed
        assert doc.getObject(kept["step"]).References[1] == ["One."]
    return {
        "two_independent_flexible_instances": True,
        "copied_joints_solved": 2,
        "source_poses_preserved": True,
        "parent_reference_migration": True,
    }
