"""Measured canned-cycle geometry, native operation output and stock removal."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    call = tools_for(root, "cam")
    doc = App.newDocument("CannedCycles")
    scope = {}
    scripts = Path(root) / "src/freecad_nexus/tools/_scripts"
    exec(
        "\n".join(
            (scripts / name).read_text() for name in ("cam_cycles.py", "cam_paths.py")
        ),
        scope,
    )

    def command(name, **parameters):
        return {"name": name, "parameters": parameters}

    def analyze(commands, expected_rapid=None, expected_cut=None):
        obj = call("cam_create_custom_path", name="Cycle", commands=commands)
        path = doc.getObject(obj["name"]).Path
        original = path.toGCode()
        stats = call("cam_simulate_toolpath", operation_name=obj["name"])
        assert doc.getObject(obj["name"]).Path.toGCode() == original
        segments, errors, _ = scope["path_segments"](path)
        if expected_rapid is not None:
            assert stats["statistics_complete"], stats
            assert math.isclose(stats["rapid_length"], expected_rapid, abs_tol=1e-8), (
                stats
            )
            assert math.isclose(stats["cutting_length"], expected_cut, abs_tol=1e-8), (
                stats
            )
        return obj["name"], stats, segments, errors

    # From Z10: traverse 5, descend to R2, feed 5 to Z-3, retract to Z10.
    first, stats, segments, _ = analyze(
        [command("G0", Z=10), command("G98"), command("G81", X=3, Y=4, Z=-3, R=2)],
        36,
        5,
    )
    assert list(segments[-1]["end"]) == [3, 4, 10]
    assert stats["expanded_cycle_command_count"] == 1
    # Below R, lift first; no diagonal shortcut through the stock.
    _, _, segments, _ = analyze([command("G81", X=3, Y=4, Z=-3, R=2)], 12, 5)
    assert list(segments[0]["end"]) == [0, 0, 2]
    assert list(segments[1]["end"]) == [3, 4, 2]
    # G99, sticky R/depth, absolute repeats at one hole, and seconds of dwell.
    _, stats, segments, _ = analyze(
        [
            command("G0", Z=10),
            command("G99"),
            command("G82", X=3, Y=4, Z=-3, R=2, P=0.25, L=2),
            command("G82", X=6),
        ],
        41,
        15,
    )
    assert stats["cycle_dwell_seconds"] == 0.75
    assert list(segments[-1]["end"]) == [6, 4, 2]
    # G85 feeds out to R, then rapids to the initial plane for G98.
    analyze([command("G0", Z=10), command("G85", X=3, Y=4, Z=-3, R=2)], 31, 10)
    # Published LinuxCNC incremental example: OLD_Z=3, R=4.8, depth=4.2.
    _, _, segments, _ = analyze(
        [
            command("G0", X=1, Y=2, Z=3),
            command("G91"),
            command("G81", X=4, Y=5, Z=-0.6, R=1.8, L=3),
        ],
        math.sqrt(14) + 1.8 + 3 * math.sqrt(41) + 1.8,
        1.8,
    )
    assert (segments[-1]["end"] - App.Vector(13, 17, 4.8)).Length < 1e-8
    # Inch R and incremental depth, L repeats; P dwell stays in seconds.
    _, stats, segments, _ = analyze(
        [
            command("G20"),
            command("G91"),
            command("G99"),
            command("G82", X=1, Z=-0.2, R=0.1, L=2, P=0.5),
        ],
        63.5,
        10.16,
    )
    assert stats["cycle_dwell_seconds"] == 1
    assert (segments[-1]["end"] - App.Vector(50.8, 0, 2.54)).Length < 1e-8
    # The drill axis follows all three planes; distances and order must agree.
    for plane, axes in (("G17", "XYZ"), ("G18", "ZXY"), ("G19", "YZX")):
        _, _, segments, _ = analyze(
            [
                command(plane),
                command("G0", **{axes[2]: 10}),
                command("G85", **{axes[0]: 3, axes[1]: 4, axes[2]: -3, "R": 2}),
            ],
            31,
            10,
        )
        end = dict(zip("XYZ", segments[-1]["end"]))
        assert [end[key] for key in axes] == [3, 4, 10]
    # Switching G98/G99 or cycle type retains the first initial plane. G0
    # abandons the series and captures a new initial plane on the next cycle.
    _, _, segments, errors = analyze(
        [
            command("G0", Z=10),
            command("G99"),
            command("G81", Z=-3, R=2),
            command("G98"),
            command("G85", X=1, Z=-3, R=2),
        ]
    )
    assert not errors and segments[-1]["end"].z == 10
    _, _, segments, errors = analyze(
        [
            command("G0", Z=10),
            command("G81", Z=-3, R=2),
            command("G0", Z=7),
            command("G81", X=1, Z=-3, R=2),
        ]
    )
    assert not errors and segments[-1]["end"].z == 7
    # Native Path.Command can carry axis-only modal continuation blocks.
    _, _, segments, errors = analyze(
        [
            command("G0", Z=10),
            command("G99"),
            command("G81", X=1, Z=-3, R=2),
            command("", X=4),
        ]
    )
    assert not errors and list(segments[-1]["end"]) == [4, 0, 2]
    for commands in (
        [command("G81", Z=-1)],
        [command("G81", R=2, X=1)],
        [command("G81", Z=3, R=2)],
        [command("G82", Z=-1, R=2)],
        [command("G82", Z=-1, R=2, P=-1)],
        [command("G81", Z=-1, R=2, L=0)],
        [command("G81", Z=-1, R=2, L=1.5)],
        [command("G81", Z=-1, R=2, A=90)],
        [command("G81", Z=-1, R=2), command("G80"), command("G81", X=2)],
        [command("G81", Z=-1, R=2), command("G91"), command("G81", X=2)],
        [command("G83", Z=-1, R=2, Q=1)],
        [command("G84", Z=-1, R=2)],
        [command("G93"), command("G81", Z=-1, R=2)],
    ):
        _, stats, _, errors = analyze(commands)
        assert (
            errors and not stats["statistics_complete"] and stats["path_length"] is None
        )

    # Real native Drilling produces all three supported cycle families.
    model = doc.addObject("Part::Feature", "Model")
    model.Shape = Part.makeBox(10, 10, 5).cut(
        Part.makeCylinder(1, 5, App.Vector(5, 5, 0))
    )
    hole = next(
        "Face" + str(i)
        for i, f in enumerate(model.Shape.Faces, 1)
        if type(f.Surface).__name__ == "Cylinder"
    )
    call("cam_create_job", name="Job", model_names=[model.Name])
    controller = call(
        "cam_add_tool_controller",
        job_name="Job",
        tool_number=2,
        tool_asset="drill.fcstd",
        create_new=True,
        tool={"Diameter": "2 mm"},
    )
    native = []
    for cycle, parameters in (
        ("G81", {}),
        ("G82", {"DwellEnabled": True, "DwellTime": 0.3}),
        ("G85", {"feedRetractEnabled": True}),
    ):
        operation = call(
            "cam_add_operation",
            job_name="Job",
            operation="Drilling",
            base_object=model.Name,
            base_subelements=[hole],
            tool_controller_name=controller["name"],
            parameters=parameters,
        )
        obj = doc.getObject(operation["name"])
        assert any(c.Name == cycle for c in obj.Path.Commands)
        stats = call("cam_simulate_toolpath", operation_name=obj.Name)
        assert stats["statistics_complete"] and stats["cutting_length"] > 5, stats
        native.append(obj.Name)
    if App.GuiUp:
        result = call("cam_simulate_stock", job_name="Job", resolution=0.25)
        assert 0 < result["remaining_mesh_volume"] < result["initial_mesh_volume"], (
            result
        )
        assert result["operation_count"] == 3
        native_op = doc.getObject(native[0])
        native_op.Placement.Base = App.Vector(0, 0, 10)
        failed = call(
            "cam_simulate_stock", job_name="Job", resolution=0.25, expect_success=False
        )
        assert "Placement" in failed["error"]
        native_op.Placement = App.Placement()
    filename = str(Path(output) / "canned.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    stats = call("cam_simulate_toolpath", operation_name=first)
    assert stats["statistics_complete"] and stats["cutting_length"] == 5
    for name in native:
        assert call("cam_simulate_toolpath", operation_name=name)["statistics_complete"]
    return {
        "planes": 3,
        "cycles": ["G81", "G82", "G85"],
        "native_drilling": True,
        "repeat_and_sticky": True,
        "return_modes": True,
        "saved_reopened": True,
    }
