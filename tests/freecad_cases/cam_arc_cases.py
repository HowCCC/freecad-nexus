"""RS274 arc planes, signed radius, helical direction and invalid geometry."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    from workflows import tools_for

    call = tools_for(root, "cam")
    doc = App.newDocument("CamArcPlanes")
    scope = {}
    exec(
        compile(
            "\n".join(
                (
                    Path(root) / "src/freecad_nexus/tools/_scripts" / name
                ).read_text()
                for name in ("cam_cycles.py", "cam_paths.py")
            ),
            "<native path geometry>",
            "exec",
        ),
        scope,
    )
    tested = []

    def analyze(label, commands):
        result = call("cam_create_custom_path", name=label, commands=commands)
        path = doc.getObject(result["name"]).Path
        stats = call("cam_simulate_toolpath", operation_name=result["name"])
        return stats, scope["path_segments"](path)[0]

    # Right-handed plane coordinates: (X,Y,Z), (Z,X,Y), (Y,Z,X).
    for plane, axes, centers in (
        ("G17", "XYZ", "IJ"),
        ("G18", "ZXY", "KI"),
        ("G19", "YZX", "JK"),
    ):
        for clockwise in (False, True):
            for height in (0, 2, -2):
                for full in (False, True):
                    name = "G2" if clockwise else "G3"
                    start = {axes[0]: 3.0}
                    end = {
                        axes[0]: 3.0 if full else 0.0,
                        axes[1]: 0.0 if full else 3.0,
                        axes[2]: height,
                        centers[0]: -3.0,
                        centers[1]: 0.0,
                    }
                    stats, segments = analyze(
                        "Arc",
                        [
                            {"name": plane},
                            {"name": "G0", "parameters": start},
                            {"name": name, "parameters": end},
                        ],
                    )
                    sweep = (
                        math.tau
                        if full
                        else (1.5 * math.pi if clockwise else math.pi / 2)
                    )
                    assert stats["statistics_complete"], stats
                    assert (
                        abs(stats["cutting_length"] - math.hypot(3 * sweep, height))
                        < 1e-8
                    )
                    points = segments[-1]["edge"].discretize(Number=101)
                    expected_start = App.Vector(*[start.get(axis, 0) for axis in "XYZ"])
                    expected_end = App.Vector(*[end.get(axis, 0) for axis in "XYZ"])
                    assert (points[0] - expected_start).Length < 1e-7
                    assert (points[-1] - expected_end).Length < 1e-7
                    # Direction and helix handedness must agree with G2/G3.
                    middle = points[len(points) // 2]
                    components = dict(zip("XYZ", middle))
                    sign = -1 if clockwise else 1
                    assert (
                        abs(components[axes[0]] - 3 * math.cos(sign * sweep / 2)) < 1e-5
                    )
                    assert (
                        abs(components[axes[1]] - 3 * math.sin(sign * sweep / 2)) < 1e-5
                    )
                    assert abs(components[axes[2]] - height / 2) < 1e-6
                    tested.append(stats["name"])
        # R>0 is the minor arc, R<0 the major arc, for either direction.
        for radius in (3, -3):
            for command in ("G2", "G3"):
                stats, _ = analyze(
                    "Radius",
                    [
                        {"name": plane},
                        {"name": "G0", "parameters": {axes[0]: 3}},
                        {
                            "name": command,
                            "parameters": {axes[0]: 0, axes[1]: 3, "R": radius},
                        },
                    ],
                )
                expected = 3 * (math.pi / 2 if radius > 0 else 1.5 * math.pi)
                assert (
                    stats["statistics_complete"]
                    and abs(stats["cutting_length"] - expected) < 1e-8
                ), stats

    # Absolute center coordinates, incremental endpoints and inch units.
    stats, _ = analyze(
        "AbsoluteCenter",
        [
            {"name": "G20"},
            {"name": "G0", "parameters": {"X": 2, "Y": 1}},
            {"name": "G91"},
            {"name": "G90.1"},
            {"name": "G3", "parameters": {"X": -1, "Y": 1, "I": 1, "J": 1}},
        ],
    )
    assert (
        stats["statistics_complete"]
        and abs(stats["cutting_length"] - 25.4 * math.pi / 2) < 1e-8
    )
    for params in (
        {"X": 3, "Y": 0, "R": 3},
        {"X": 0, "Y": 3, "R": 1},
        {"X": 0, "Y": 3},
        {"X": 0, "Y": 3, "I": -2},
        {"X": 0, "Y": 3, "I": -3, "P": 2},
        {"X": 0, "Y": 3, "I": -3, "R": 3},
    ):
        stats, _ = analyze(
            "InvalidArc",
            [
                {"name": "G0", "parameters": {"X": 3}},
                {"name": "G3", "parameters": params},
            ],
        )
        assert not stats["statistics_complete"] and stats["path_length"] is None, stats
        assert stats["unsupported_commands"]
    filename = str(Path(output) / "cam_arc_planes.FCStd")
    doc.recompute()
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    result = call("cam_simulate_toolpath", operation_name=tested[-1])
    assert result["statistics_complete"]
    return {
        "directed_planar_helical_cases": len(tested),
        "radius_cases": 12,
        "invalid_cases": 6,
        "saved_reopened": True,
    }
