"""Registered CAM operation families on native pocket, hole and face geometry."""

import math
from pathlib import Path


def operation_families(root, output):
    import FreeCAD as App, Part
    from PathScripts import PathUtils
    from workflows import tools_for

    call = tools_for(root, "cam")
    doc = App.newDocument("Machining")
    model = doc.addObject("Part::Feature", "Model")
    model.Shape = (
        Part.makeBox(40, 30, 10)
        .cut(Part.makeBox(16, 12, 5, App.Vector(3, 3, 6)))
        .cut(Part.makeCylinder(3, 10, App.Vector(30, 15, 0)))
    )
    faces = [
        {
            "name": "Face" + str(i),
            "type": type(f.Surface).__name__,
            "z": f.CenterOfMass.z,
            "area": f.Area,
        }
        for i, f in enumerate(model.Shape.Faces, 1)
    ]
    floor = next(x["name"] for x in faces if abs(x["z"] - 6) < 1e-7)
    top = next(x["name"] for x in faces if abs(x["z"] - 10) < 1e-7)
    hole = next(x["name"] for x in faces if x["type"] == "Cylinder")
    call("cam_create_job", name="Job", model_names=[model.Name])
    j = doc.Job
    configured = call(
        "cam_add_tool_controller",
        job_name=j.Name,
        tool_number=1,
        spindle_speed=12000,
        horizontal_feed=300,
        vertical_feed=100,
        tool={"Diameter": "2 mm"},
    )
    endmill = doc.getObject(configured["name"])
    assert math.isclose(endmill.HorizFeed.getValueAs("mm/min").Value, 300)
    assert configured["feed_unit"] == "mm/min" and math.isclose(
        configured["horizontal_feed"], 300
    )
    # All ToolControllers are created through actual registered MCP adapters.
    controllers = {"endmill": endmill}
    for number, shape in enumerate(
        ["drill", "probe", "v-bit", "thread-mill", "tap", "ballend"], 2
    ):
        result = call(
            "cam_add_tool_controller",
            job_name=j.Name,
            tool_number=number,
            spindle_speed=12000,
            horizontal_feed=300,
            vertical_feed=100,
            tool_asset=shape + ".fcstd",
            create_new=True,
            tool={"Pitch": "1 mm"} if shape == "tap" else {},
        )
        controllers[shape] = doc.getObject(result["name"])
        assert math.isclose(result["horizontal_feed"], 300)
        assert math.isclose(result["vertical_feed"], 100)
    exported = call(
        "cam_export_tool_controller", tool_controller_name=controllers["tap"].Name
    )
    assert exported["tool"]["Pitch"] == "1.0 mm" and exported["toolbit"]
    before = {o.Name for o in doc.Objects}
    call(
        "cam_add_tool_controller",
        expect_success=False,
        job_name=j.Name,
        tool_number=1,
        create_new=True,
    )
    call(
        "cam_add_tool_controller",
        expect_success=False,
        job_name=j.Name,
        tool_number=99,
        create_new=True,
        tool={"DoesNotExist": 1},
    )
    call(
        "cam_add_tool_controller",
        expect_success=False,
        job_name=j.Name,
        tool_number=99,
        create_new=True,
        tool={"Diameter": "not a quantity"},
    )
    assert {o.Name for o in doc.Objects} == before, {
        "added": {o.Name for o in doc.Objects} - before,
        "removed": before - {o.Name for o in doc.Objects},
    }
    assert not doc.HasPendingTransaction and not App.getActiveTransaction()
    call(
        "cam_set_tool_controller",
        expect_success=False,
        tool_controller_name=endmill.Name,
        horizontal_feed=900,
        tool={"DoesNotExist": 1},
    )
    assert math.isclose(endmill.HorizFeed.getValueAs("mm/min").Value, 300)
    updated = call(
        "cam_set_tool_controller",
        tool_controller_name=endmill.Name,
        horizontal_feed=360,
    )
    assert math.isclose(updated["horizontal_feed"], 360)
    configs = [
        ("Profile", [], {}, "endmill"),
        ("Pocket", [floor], {}, "endmill"),
        ("PocketShape", [floor], {}, "endmill"),
        ("Adaptive", [floor], {}, "endmill"),
        ("MillFace", [top], {}, "endmill"),
        ("Drilling", [hole], {}, "drill"),
        ("Helix", [hole], {}, "endmill"),
        ("Tapping", [hole], {"DwellEnabled": False}, "tap"),
        ("ThreadMilling", [hole], {}, "thread-mill"),
        (
            "Probe",
            [],
            {"PointCountX": 3, "PointCountY": 3, "FinalDepth": "0 mm"},
            "probe",
        ),
        ("Surface", [], {}, "ballend"),
        (
            "Waterline",
            [],
            {
                "LayerMode": "Multi-pass",
                "StepDown": "2 mm",
                "StartDepth": "10 mm",
                "FinalDepth": "5 mm",
            },
            "ballend",
        ),
        (
            "Slot",
            [],
            {
                "CustomPoint1": [5, 9, 10],
                "CustomPoint2": [17, 9, 10],
                "StartDepth": "10 mm",
                "FinalDepth": "8 mm",
            },
            "endmill",
        ),
        ("Deburr", [top], {}, "v-bit"),
        ("Engrave", [top], {"FinalDepth": "9.5 mm"}, "v-bit"),
        ("Vcarve", [top], {}, "v-bit"),
        ("Custom", [], {"Gcode": ["G0 X0 Y0 Z15", "G1 X10 Y0 Z10 F100"]}, "endmill"),
    ]
    original_chooser = PathUtils.findToolController
    original_tools = [o.Name for o in j.Tools.Group]
    before = {o.Name for o in doc.Objects}
    schema = call("cam_get_operation_schema", operation="Probe", job_name=j.Name)
    assert any(p["name"] == "PointCountX" for p in schema["properties"])
    assert {o.Name for o in doc.Objects} == before and not j.Operations.Group
    assert PathUtils.findToolController is original_chooser
    call(
        "cam_add_operation",
        expect_success=False,
        job_name=j.Name,
        operation="Probe",
        tool_controller_name=endmill.Name,
        parameters={"PointCountX": 3, "PointCountY": 3},
    )
    assert {o.Name for o in doc.Objects} == before and not j.Operations.Group
    assert PathUtils.findToolController is original_chooser
    results = {}
    for kind, sub, params, tool in configs:
        result = call(
            "cam_add_operation",
            job_name=j.Name,
            operation=kind,
            base_object=model.Name if kind not in ("Probe", "Slot", "Custom") else "",
            base_subelements=sub,
            parameters=params,
            tool_controller_name=controllers[tool].Name,
        )
        obj = doc.getObject(result["name"])
        assert obj.isValid() and obj.Path.Commands, (kind, obj.State)
        assert result["path_ready"] and result["machining_motion_command_count"] > 0, (
            result
        )
        assert obj.ToolController is controllers[tool]
        assert (
            obj in j.Operations.Group
            and obj.Proxy.__class__.__module__ == "Path.Op." + kind
        )
        assert [o.Name for o in j.Tools.Group] == original_tools
        assert PathUtils.findToolController is original_chooser
        for command in obj.Path.Commands:
            assert all(math.isfinite(v) for v in command.Parameters.values()), (
                kind,
                command.toGCode(),
            )
        if kind in ("Pocket", "PocketShape", "Adaptive"):
            cuts = [
                c.Parameters
                for c in obj.Path.Commands
                if c.Name == "G1" and "X" in c.Parameters and "Y" in c.Parameters
            ]
            assert cuts and any(abs(c.get("Z", 0) - 6) < 1e-5 for c in cuts)
            assert all(3 <= c["X"] <= 19 and 3 <= c["Y"] <= 15 for c in cuts)
        if kind == "Drilling":
            cycle = next(c for c in obj.Path.Commands if c.Name == "G81")
            assert [cycle.Parameters[k] for k in ("X", "Y", "Z")] == [30, 15, 0]
        if kind == "Tapping":
            cycle = next(c for c in obj.Path.Commands if c.Name in ("G74", "G84"))
            assert cycle.Parameters["Z"] == 0 and cycle.Parameters["S"] == 12000
        if kind == "Probe":
            probes = [c for c in obj.Path.Commands if c.Name == "G38.2"]
            assert len(probes) == 9 and all(c.Parameters["Z"] == 0 for c in probes)
        if kind in ("Helix", "ThreadMilling"):
            arcs = [
                c
                for c in obj.Path.Commands
                if c.Name in ("G2", "G3") and "Z" in c.Parameters
            ]
            assert arcs and max(c.Parameters["Z"] for c in arcs) > min(
                c.Parameters["Z"] for c in arcs
            )
        if kind == "Engrave":
            assert obj.FinalDepth.Value == 9.5
            assert any(c.Parameters.get("Z") == 9.5 for c in obj.Path.Commands)
        if kind == "Slot":
            # Axis coordinates are modal; the final XY feed inherits Z=8.
            position = {}
            cuts = []
            for command in obj.Path.Commands:
                position.update(
                    {k: v for k, v in command.Parameters.items() if k in "XYZ"}
                )
                if command.Name == "G1":
                    cuts.append(dict(position))
            assert any(c == {"X": 17, "Y": 9, "Z": 8} for c in cuts), cuts
        results[kind] = {
            "name": obj.Name,
            "commands": len(obj.Path.Commands),
            "machining_commands": result["machining_motion_command_count"],
        }
    # Path comments/rapid positioning alone are explicitly reported as unready.
    empty = call(
        "cam_add_operation",
        job_name=j.Name,
        operation="Pocket",
        tool_controller_name=endmill.Name,
    )
    assert not empty["path_ready"] and empty["machining_motion_command_count"] == 0
    call("cam_remove_operation", operation_name=empty["name"])
    engraved = doc.getObject(results["Engrave"]["name"])
    # Retain an unrelated expression, and verify a valid explicit depth edit.
    old_expressions = list(engraved.ExpressionEngine)
    old_depth = engraved.FinalDepth.Value
    call(
        "cam_set_operation_parameters",
        expect_success=False,
        operation_name=engraved.Name,
        parameters={"FinalDepth": "9 mm", "StartDepth": "not a quantity"},
    )
    assert (
        engraved.FinalDepth.Value == old_depth
        and engraved.ExpressionEngine == old_expressions
    )
    call(
        "cam_set_operation_parameters",
        operation_name=engraved.Name,
        parameters={"FinalDepth": "9 mm"},
    )
    doc.recompute()
    assert engraved.FinalDepth.Value == 9
    assert any(
        c.Parameters.get("Z") == 9 for c in engraved.Path.Commands if c.Name == "G1"
    )
    deferred = call(
        "cam_set_operation_parameters",
        operation_name=engraved.Name,
        parameters={"FinalDepth": "9.25 mm"},
        recompute=False,
    )
    assert not deferred["path_ready"] and deferred["path_status"] == "recompute_pending"
    regenerated = call("cam_recompute_operation", operation_name=engraved.Name)
    assert regenerated["path_ready"] and engraved.FinalDepth.Value == 9.25
    assert any(
        c.Parameters.get("Z") == 9.25 for c in engraved.Path.Commands if c.Name == "G1"
    )
    # Native face-pocket rejects an incompatible depth; stale commands must
    # not be returned as a successfully recomputed path.
    pocket = doc.getObject(results["Pocket"]["name"])
    old_expressions = list(pocket.ExpressionEngine)
    old_path = pocket.Path.toGCode()
    call(
        "cam_set_operation_parameters",
        expect_success=False,
        operation_name=pocket.Name,
        parameters={"FinalDepth": "7 mm"},
    )
    assert pocket.FinalDepth.Value == 6 and pocket.ExpressionEngine == old_expressions
    assert pocket.Path.toGCode() == old_path
    before = {o.Name for o in doc.Objects}
    for kwargs in [
        dict(operation="Profile", parameters={"UnknownParameter": 1}),
        dict(operation="Profile", base_object=model.Name, base_subelements=["Face999"]),
        dict(operation="Slot", base_object=model.Name, base_subelements=[top]),
        dict(operation="Probe", tool_controller_name=endmill.Name),
    ]:
        call("cam_add_operation", expect_success=False, job_name=j.Name, **kwargs)
        assert {o.Name for o in doc.Objects} == before
        assert [o.Name for o in j.Tools.Group] == original_tools
        assert PathUtils.findToolController is original_chooser
    filename = str(Path(output) / "operations.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    doc.recompute()
    for kind, record in results.items():
        obj = doc.getObject(record["name"])
        assert obj.isValid() and obj.Path.Commands and obj.ToolController, kind
        assert obj in doc.Job.Operations.Group
    assert doc.getObject(results["Engrave"]["name"]).FinalDepth.Value == 9.25
    return {
        "families": results,
        "persistence": True,
        "multi_tool_selection": True,
        "failure_rollback": True,
        "explicit_depth_overrides_expression": True,
    }
