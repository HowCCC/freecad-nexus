"""A source joint reorder must never change which instance a motion drives."""

from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import UtilsAssembly
    from workflows import tools_for

    call = tools_for(root, "assembly")
    doc = App.newDocument("MotionBinding")
    doc.addObject("Part::Box", "Shape")
    App.ParamGet("User parameter:BaseApp/Preferences/Mod/Assembly").SetBool(
        "SolveOnRecompute", False
    )
    call("assembly_create", name="Source")
    for name in ("Base", "A", "B"):
        call(
            "assembly_insert_component",
            assembly_name="Source",
            source_name="Shape",
            instance_name=name,
        )
    joints = [
        call(
            "assembly_add_constraint",
            assembly_name="Source",
            constraint_type="Slider",
            first="Base",
            second=n,
            solve=False,
        )["name"]
        for n in ("A", "B")
    ]
    call("assembly_create", name="Top")
    call(
        "assembly_insert_component",
        assembly_name="Top",
        source_name="Source",
        instance_name="One",
    )
    call("assembly_set_component_rigid", instance_name="One", rigid=False)
    info = call("assembly_inspect_component", instance_name="One")
    children = {
        r["source"]["object"]: r["name"] for r in info["components"] if r["source"]
    }
    call(
        "assembly_ground_component",
        assembly_name="Top",
        component_name=children["Base"],
    )
    call("assembly_solve", assembly_name="Top")
    sim = call("assembly_create_simulation", assembly_name="Top", end=1, step=0.1)[
        "name"
    ]
    copy = info["joint_copies"][1]["name"]
    motion = call(
        "assembly_add_motion",
        simulation_name=sim,
        joint_name=copy,
        motion_type="Linear",
        formula="4*time",
    )["name"]
    assert doc.getObject(motion).MCPSourceJoint.Name == joints[1]

    def target_status():
        data = call("assembly_inspect_simulation", simulation_name=sim)
        return next(r["binding"] for r in data["motions"] if r["name"] == motion)

    # Older/UI-created motions have no stable target metadata. Explicitly adopt
    # the intended current copy rather than guessing its history after a reorder.
    doc.getObject(motion).MCPInstanceMotion = False
    assert target_status()["status"] == "untracked_instance_target"
    call("assembly_run_simulation", simulation_name=sim, expect_success=False)
    call("assembly_edit_motion", motion_name=motion, joint_name=copy)

    # Repairing one untracked/invalid Motion must not depend on repairing every
    # other Motion in the same simulation first.
    second_motion = call(
        "assembly_add_motion",
        simulation_name=sim,
        joint_name=info["joint_copies"][0]["name"],
        motion_type="Linear",
        formula="2*time",
    )["name"]
    doc.getObject(motion).MCPInstanceMotion = False
    doc.getObject(second_motion).MCPInstanceMotion = False
    doc.getObject(second_motion).Formula = ""
    repaired = call("assembly_edit_motion", motion_name=motion, joint_name=copy)
    assert not repaired["configuration_valid"]
    assert all(issue.get("motion") == second_motion for issue in repaired["issues"])
    call("assembly_run_simulation", simulation_name=sim, expect_success=False)
    repaired = call(
        "assembly_edit_motion",
        motion_name=second_motion,
        joint_name=info["joint_copies"][0]["name"],
        formula="2*time",
    )
    assert repaired["configuration_valid"] and not repaired["issues"]
    # Reject duplicate coordinates even when the edited Motion precedes the
    # conflicting one in native Group order, retaining its original target.
    call(
        "assembly_edit_motion",
        motion_name=motion,
        joint_name=info["joint_copies"][0]["name"],
        expect_success=False,
    )
    assert doc.getObject(motion).Joint[0].Name == copy
    call("assembly_remove_motion", motion_name=second_motion)

    # A malformed native group must remain inspectable without AttributeError.
    foreign = doc.addObject("App::FeaturePython", "ForeignMember")
    doc.getObject(sim).Group = list(doc.getObject(sim).Group) + [foreign]
    inspected = call("assembly_inspect_simulation", simulation_name=sim)
    assert not inspected["configuration_valid"]
    assert any(issue["kind"] == "invalid_member" for issue in inspected["issues"])
    call("assembly_edit_simulation", simulation_name=sim, frames_per_second=45)
    before = {obj.Name for obj in doc.Objects}
    call("assembly_remove_simulation", simulation_name=sim, expect_success=False)
    assert {obj.Name for obj in doc.Objects} == before
    doc.getObject(sim).Group = [doc.getObject(motion)]
    doc.removeObject(foreign.Name)
    doc.recompute()
    doc.commitTransaction()
    App.closeActiveTransaction()

    def run_and_check(distance):
        for name in ("A", "B"):
            doc.getObject(children[name]).Placement = App.Placement()
        doc.commitTransaction()
        App.closeActiveTransaction()
        result = call("assembly_run_simulation", simulation_name=sim)
        call("assembly_get_frame", assembly_name="Top", frame=result["frames"] - 1)
        assert abs(doc.getObject(children["A"]).Placement.Base.z) < 1e-7
        assert abs(doc.getObject(children["B"]).Placement.Base.z - distance) < 1e-7
        assert doc.getObject(motion).Joint[0].Reference2[0].LinkedObject.Name == "B"
        assert target_status()["status"] == "current"

    group = UtilsAssembly.getJointGroup(doc.Source)
    group.Group = list(reversed(group.Group))
    doc.recompute()  # Native copy at old index now points to A, not B.
    assert doc.getObject(motion).Joint[0].Reference2[0].LinkedObject.Name == "A"
    assert target_status()["status"] == "rebind_required"
    run_and_check(4)
    # Suppressing the first source leaves its old copy slot reused by A. The
    # motion must detach from that slot instead of following its new semantics.
    doc.getObject(joints[1]).Suppressed = True
    doc.recompute()
    doc.commitTransaction()
    App.closeActiveTransaction()
    call("assembly_sync_instances", assembly_name="Top")
    assert target_status()["status"] == "source_suppressed"
    assert not doc.getObject(motion).Joint
    doc.getObject(joints[1]).Suppressed = False
    doc.recompute()
    doc.commitTransaction()
    App.closeActiveTransaction()
    run_and_check(4)
    # Delete the *other* source joint: the surviving copy shifts to another slot.
    doc.removeObject(joints[0])
    doc.recompute()
    doc.commitTransaction()
    App.closeActiveTransaction()
    run_and_check(4)
    # Source suppression retains the Motion and metadata, but removes its native
    # copy. Sync clears a reused Joint pointer instead of driving the wrong slot.
    doc.getObject(joints[1]).Suppressed = True
    doc.recompute()
    doc.commitTransaction()
    App.closeActiveTransaction()
    sync = call("assembly_sync_instances", assembly_name="Top")
    assert target_status()["status"] == "source_suppressed", sync
    assert not doc.getObject(motion).Joint
    failed = call("assembly_run_simulation", simulation_name=sim, expect_success=False)
    assert "source_suppressed" in failed["error"]
    assert not call("assembly_simulation_status", assembly_name="Top")["valid"]
    filename = str(Path(output) / "motion_binding.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    assert target_status()["status"] == "source_suppressed"
    doc.getObject(joints[1]).Suppressed = False
    doc.recompute()
    doc.commitTransaction()
    App.closeActiveTransaction()
    run_and_check(4)
    # Dedicated edits validate types/duplicates and invalidate previous frames.
    call("assembly_edit_motion", motion_name=motion, formula="7*time")
    assert not call("assembly_simulation_status", assembly_name="Top")["valid"]
    call(
        "assembly_edit_motion",
        motion_name=motion,
        motion_type="Angular",
        expect_success=False,
    )
    assert doc.getObject(motion).MotionType == "Linear"
    assert doc.getObject(motion).Formula == "7*time"
    run_and_check(7)
    call(
        "assembly_edit_simulation",
        simulation_name=sim,
        end=2,
        step=0.2,
        frames_per_second=60,
    )
    configured = call("assembly_inspect_simulation", simulation_name=sim)
    assert (
        configured["end"] == 2
        and configured["step"] == 0.2
        and configured["frames_per_second"] == 60
    )
    call("assembly_edit_simulation", simulation_name=sim, start=3, expect_success=False)
    assert doc.getObject(sim).aTimeStart.Value == 0
    run_and_check(14)
    # Metadata-only motion references are still owned by an instance when the
    # native copy disappears; rigid switching/removal must clean them up too.
    for operation in ("rigid", "remove"):
        call(
            "assembly_insert_component",
            assembly_name="Top",
            source_name="Source",
            instance_name="Temporary",
        )
        temp = call(
            "assembly_set_component_rigid", instance_name="Temporary", rigid=False
        )
        temp_sim = call(
            "assembly_create_simulation", assembly_name="Top", end=1, step=0.1
        )["name"]
        temp_motion = call(
            "assembly_add_motion",
            simulation_name=temp_sim,
            joint_name=temp["joint_copies"][0]["name"],
            motion_type="Linear",
            formula="time",
        )["name"]
        doc.getObject(joints[1]).Suppressed = True
        doc.recompute()
        doc.commitTransaction()
        App.closeActiveTransaction()
        call("assembly_sync_instances", assembly_name="Top")
        assert not doc.getObject(temp_motion).Joint
        if operation == "rigid":
            changed = call(
                "assembly_set_component_rigid", instance_name="Temporary", rigid=True
            )
        else:
            changed = call(
                "assembly_remove_component",
                assembly_name="Top",
                component_name="Temporary",
            )
        assert temp_motion in changed["removed_motions"]
        assert doc.getObject(motion) is not None
        call("assembly_remove_simulation", simulation_name=temp_sim)
        if operation == "rigid":
            call(
                "assembly_remove_component",
                assembly_name="Top",
                component_name="Temporary",
            )
        doc.getObject(joints[1]).Suppressed = False
        doc.recompute()
        doc.commitTransaction()
        App.closeActiveTransaction()
        call("assembly_sync_instances", assembly_name="Top")
    # Partial time edits retain expressions on omitted native properties.
    doc.getObject(sim).setExpression("bTimeEnd", "aTimeStart + 2 s")
    saved_expression = dict(doc.getObject(sim).ExpressionEngine)["bTimeEnd"]
    call("assembly_edit_simulation", simulation_name=sim, frames_per_second=30)
    assert dict(doc.getObject(sim).ExpressionEngine)["bTimeEnd"] == saved_expression
    # Invalid/duplicate additions must roll back their newly created Motion.
    before = {o.Name for o in doc.Objects}
    for formula in ("time", ""):
        call(
            "assembly_add_motion",
            simulation_name=sim,
            joint_name=doc.getObject(motion).Joint[0].Name,
            motion_type="Linear",
            formula=formula,
            expect_success=False,
        )
        assert {o.Name for o in doc.Objects} == before
    # Explicit native UI edits are not silently overwritten; MCP edit establishes
    # a new intended target before synchronization can proceed.
    standalone = call(
        "assembly_add_constraint",
        assembly_name="Top",
        constraint_type="Slider",
        first=children["Base"],
        second=children["A"],
        solve=False,
    )["name"]
    doc.getObject(motion).Joint = doc.getObject(standalone)
    assert target_status()["status"] == "manual_reference_change"
    call("assembly_run_simulation", simulation_name=sim, expect_success=False)
    call("assembly_edit_motion", motion_name=motion, joint_name=standalone)
    assert not doc.getObject(motion).MCPInstanceMotion
    # Retarget back to the instance; deleted source is distinct from suppression.
    copied = call("assembly_inspect_component", instance_name="One")["joint_copies"][0][
        "name"
    ]
    call("assembly_edit_motion", motion_name=motion, joint_name=copied)
    removed_source = call("assembly_remove_joint", joint_name=joints[1])
    assert removed_source["retained_instance_motions"] == [
        {"document": doc.Name, "motion": motion}
    ]
    assert not removed_source["removed_motions"]
    call("assembly_sync_instances", assembly_name="Top")
    assert target_status()["status"] == "source_deleted"
    call("assembly_run_simulation", simulation_name=sim, expect_success=False)
    removed = call("assembly_remove_motion", motion_name=motion)
    assert not removed["remaining_motions"] and doc.getObject(standalone)
    last = call(
        "assembly_add_motion",
        simulation_name=sim,
        joint_name=standalone,
        motion_type="Linear",
        formula="time",
    )
    removed = call("assembly_remove_simulation", simulation_name=sim)
    assert removed["removed_motions"] == [last["name"]]
    assert doc.getObject(sim) is None and doc.getObject(standalone)
    assert all(doc.getObject(name) for name in children.values())
    return {
        "source_reorder": True,
        "copy_slot_deleted": True,
        "suppression_reopen": True,
        "source_deletion_rejected": True,
        "explicit_rebinding": True,
        "incremental_motion_repair": True,
        "malformed_simulation_inspection": True,
        "motion_and_simulation_lifecycle": True,
    }
