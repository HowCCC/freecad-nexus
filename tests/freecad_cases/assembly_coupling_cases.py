"""Driven native couplings verify actual ratios, not just joint registration."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    call = tools_for(root, "assembly")
    prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Assembly")
    prefs.SetBool("SolveOnRecompute", False)
    evidence = {}
    for kind in ("Gears", "Belt", "Screw", "RackPinion"):
        doc = App.newDocument(kind)
        call("assembly_create", name="Asm")
        source = doc.addObject("Part::Feature", "Source")
        source.Shape = Part.makeBox(2, 2, 2)
        for n in ("Base", "Driver", "Follower"):
            call(
                "assembly_insert_component",
                assembly_name="Asm",
                source_name="Source",
                instance_name=n,
            )
        call("assembly_ground_component", assembly_name="Asm", component_name="Base")
        driver = call(
            "assembly_add_constraint",
            assembly_name="Asm",
            constraint_type="Revolute",
            first="Base",
            second="Driver",
        )
        support = call(
            "assembly_add_constraint",
            assembly_name="Asm",
            constraint_type="Revolute" if kind in ("Gears", "Belt") else "Slider",
            first="Base",
            second="Follower",
        )
        if kind in ("Gears", "Belt"):
            call(
                "assembly_set_joint_connector",
                joint_name=support["name"],
                connector=1,
                x=30,
            )
        elif kind == "RackPinion":
            for i in (1, 2):
                call(
                    "assembly_set_joint_connector",
                    joint_name=support["name"],
                    connector=i,
                    rx=0,
                    ry=1,
                    rz=0,
                    angle=90,
                )
        assert call("assembly_solve", assembly_name="Asm")["solved"]
        params = (
            {"value": 10, "distance2": 20}
            if kind in ("Gears", "Belt")
            else {"value": 4}
            if kind == "Screw"
            else {"value": 10}
        )
        coupling = call(
            "assembly_add_constraint",
            assembly_name="Asm",
            constraint_type=kind,
            first="Follower",
            second="Driver",
            solve=False,
            **params,
        )
        if kind == "RackPinion":
            call(
                "assembly_set_joint_connector",
                joint_name=coupling["name"],
                connector=1,
                rx=0,
                ry=1,
                rz=0,
                angle=90,
            )
        result = call("assembly_solve", assembly_name="Asm")
        assert result["solved"], result
        exported = call(
            "assembly_export_asmt",
            assembly_name="Asm",
            file_path=str(Path(output) / (kind + ".asmt")),
        )
        assert exported["all_components_exported"], exported
        record = next(
            row
            for row in exported["content"]["joints"]
            if row["name"] == doc.Name + "#" + coupling["name"]
        )
        assert record["type"] == (
            "GearJoint" if kind in ("Gears", "Belt") else kind + "Joint"
        )
        sim = call(
            "assembly_create_simulation", assembly_name="Asm", start=0, end=1, step=0.1
        )
        call(
            "assembly_add_motion",
            simulation_name=sim["name"],
            joint_name=driver["name"],
            motion_type="Angular",
            formula="pi/6*time",
        )
        result = call("assembly_run_simulation", simulation_name=sim["name"])
        call("assembly_get_frame", assembly_name="Asm", frame=0)
        start_driver = doc.Driver.Placement.copy()
        start_follower = doc.Follower.Placement.copy()
        call("assembly_get_frame", assembly_name="Asm", frame=result["frames"] - 1)
        end_driver = doc.Driver.Placement.copy()
        end_follower = doc.Follower.Placement.copy()
        driver_angle = (
            start_driver.Rotation.inverted() * end_driver.Rotation
        ).getYawPitchRoll()[0]
        follower_angle = (
            start_follower.Rotation.inverted() * end_follower.Rotation
        ).getYawPitchRoll()[0]
        delta = end_follower.Base - start_follower.Base
        evidence[kind] = {
            "driver_degrees": driver_angle,
            "follower_degrees": follower_angle,
            "translation": list(delta),
        }
        assert abs(driver_angle - 30) < 1e-6, evidence[kind]
        if kind in ("Gears", "Belt"):
            expected = -60 if kind == "Gears" else 60
            assert abs(follower_angle - expected) < 1e-6, evidence[kind]
        elif kind == "Screw":
            assert abs(abs(delta.z) - 4 / 12) < 1e-6, evidence[kind]
        else:
            assert abs(abs(delta.x) - 10 * math.pi / 6) < 1e-6, evidence[kind]
        filename = str(Path(output) / (kind + ".FCStd"))
        # Save at the initial frame, then rerun after restoring native proxies.
        call("assembly_get_frame", assembly_name="Asm", frame=0)
        doc.saveAs(filename)
        App.closeDocument(doc.Name)
        doc = App.openDocument(filename)
        rerun = call("assembly_run_simulation", simulation_name=sim["name"])
        assert rerun["frames"] == result["frames"]
        App.closeDocument(doc.Name)
    return evidence
