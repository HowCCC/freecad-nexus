"""Native path analysis, executed inside FreeCAD by the CAM MCP adapter.

This file is sent as Python source over the bridge. FreeCAD imports belong
here, so the MCP server does not need FreeCAD's embedded interpreter.
"""

import math
from typing import TYPE_CHECKING

import FreeCAD as App
import Part
import Path
import Path.Geom as PathGeom

if TYPE_CHECKING:
    from .cam_cycles import CannedCycleState


def path_arc(command, start, end, plane, scale, center_absolute):
    """Resolve RS274 planar/helical arcs to an oriented native OCC edge."""
    # Right-handed plane axes: ZX for G18, YZ for G19. The final axis is
    # perpendicular travel, allowing a helix in every supported arc plane.
    axes = {"G17": (0, 1, 2), "G18": (2, 0, 1), "G19": (1, 2, 0)}[plane]
    a, b, w = axes
    xyz_start, xyz_end = list(start), list(end)
    x0, y0, x1, y1 = xyz_start[a], xyz_start[b], xyz_end[a], xyz_end[b]
    params = dict(command.Parameters)
    if any(not math.isfinite(float(value)) for value in params.values()):
        raise ValueError("Arc parameters must be finite")
    if params.get("P", 1) != 1:
        raise ValueError("Multi-turn P arcs are not supported")
    clockwise = command.Name in PathGeom.CmdMoveCW

    def sweep_for(cx, cy):
        first = math.atan2(y0 - cy, x0 - cx)
        last = math.atan2(y1 - cy, x1 - cx)
        angle = ((first - last) if clockwise else (last - first)) % math.tau
        if math.hypot(x1 - x0, y1 - y0) <= 1e-10:
            angle = math.tau
        return first, -angle if clockwise else angle

    if "R" in params:
        if any(key in params for key in "IJK"):
            raise ValueError("Arc cannot mix radius R with IJK centers")
        radius_value = float(params["R"]) * scale
        radius = abs(radius_value)
        dx, dy = x1 - x0, y1 - y0
        chord = math.hypot(dx, dy)
        if chord <= 1e-10:
            raise ValueError("Full circle requires IJK center; R is ambiguous")
        if radius <= 0 or chord > 2 * radius + 1e-9:
            raise ValueError("Arc radius cannot reach the endpoint")
        height = math.sqrt(max(0.0, radius * radius - chord * chord / 4))
        candidates = [
            (
                (x0 + x1) / 2 - sign * dy * height / chord,
                (y0 + y1) / 2 + sign * dx * height / chord,
            )
            for sign in (1, -1)
        ]
        # Positive R selects <=180 degrees; negative R selects >=180 degrees.
        cx, cy = (
            min(candidates, key=lambda center: abs(sweep_for(*center)[1]))
            if radius_value > 0
            else max(candidates, key=lambda center: abs(sweep_for(*center)[1]))
        )
    else:
        center_keys = ("IJK"[a], "IJK"[b])
        if not any(key in params for key in center_keys):
            raise ValueError("Arc requires a center in the active plane or R")
        if center_absolute and not all(key in params for key in center_keys):
            raise ValueError(
                "Absolute arc center requires both active-plane coordinates"
            )
        cx, cy = (float(params.get(key, 0.0)) * scale for key in center_keys)
        if not center_absolute:
            cx, cy = cx + x0, cy + y0
        radius = math.hypot(x0 - cx, y0 - cy)
        end_radius = math.hypot(x1 - cx, y1 - cy)
        if radius <= 1e-10 or abs(radius - end_radius) > max(1e-7, radius * 1e-9):
            raise ValueError("Arc start/end radii differ or center equals start")
    angle, sweep = sweep_for(cx, cy)
    if abs(sweep) <= 1e-14:
        raise ValueError("Arc has zero angular travel")
    height = xyz_end[w] - xyz_start[w]
    cylinder = Part.Cylinder()
    cylinder.Radius = radius
    uv = Part.Geom2d.Line2dSegment(
        App.Base.Vector2d(angle, 0), App.Base.Vector2d(angle + sweep, height)
    )
    edge = uv.toShape(cylinder)
    columns = []
    for index in axes:
        column = [0, 0, 0]
        column[index] = 1
        columns.append(App.Vector(*column))
    origin = [0, 0, 0]
    origin[a], origin[b], origin[w] = cx, cy, xyz_start[w]
    edge.Placement = App.Placement(App.Vector(*origin), App.Rotation(*columns, "ZXY"))
    if not edge.isValid():
        raise ValueError("Native arc construction failed")
    return edge, math.hypot(radius * sweep, height)


def path_segments(path, initial=None):
    """Resolve metric XYZ moves and return native OCC edges with diagnostics.

    Handle absolute/incremental endpoints, inch/mm units, three arc planes,
    absolute/incremental IJK centers, signed radius arcs and helices. Other modes are incomplete;
    callers must not present those results as complete machining statistics.
    """
    position = App.Vector() if initial is None else App.Vector(initial)
    absolute, scale, plane, center_absolute = True, 1.0, "G17", False
    unsupported = []
    segments = []
    non_motion = 0
    coordinate_unknown = False
    cycles = CannedCycleState()
    for index, command in enumerate(path.Commands):
        name, params = command.Name, dict(command.Parameters)
        if name in ("G90", "G91"):
            absolute = name == "G90"
        elif name in ("G20", "G21"):
            scale = 25.4 if name == "G20" else 1.0
        elif name in ("G17", "G18", "G19"):
            plane = name
        elif name in ("G90.1", "G91.1"):
            center_absolute = name == "G90.1"
        elif name in ("G98", "G99"):
            cycles.return_initial = name == "G98"
            non_motion += 1
        elif name == "G80":
            cycles.reset()
            if any(axis in params for axis in "XYZ"):
                unsupported.append(
                    {
                        "index": index,
                        "command": name,
                        "reason": "Axis words are not allowed with G80",
                    }
                )
                coordinate_unknown = True
            non_motion += 1
        elif name in cycles.supported or (
            name == "" and cycles.code and any(axis in params for axis in "XYZ")
        ):
            try:
                if coordinate_unknown:
                    raise ValueError("Unknown position after unsupported motion")
                source = name or cycles.code
                commands, endpoint = cycles.expand(
                    source, params, position, plane, absolute, scale
                )
                for expanded in commands:
                    if expanded.Name == "G4":
                        segments.append(
                            {
                                "index": index,
                                "command": expanded,
                                "edge": None,
                                "length_mm": 0.0,
                                "end": App.Vector(position),
                                "rapid": False,
                                "dwell_seconds": expanded.Parameters["P"],
                                "cycle": source,
                            }
                        )
                        continue
                    end = App.Vector(*(expanded.Parameters[key] for key in "XYZ"))
                    edge = PathGeom.edgeForCmd(expanded, position)
                    segments.append(
                        {
                            "index": index,
                            "command": expanded,
                            "edge": edge,
                            "length_mm": edge.Length if edge else 0.0,
                            "end": end,
                            "rapid": expanded.Name == "G0",
                            "cycle": source,
                        }
                    )
                    position = end
                position = endpoint
            except (ValueError, RuntimeError) as exc:
                unsupported.append(
                    {"index": index, "command": name, "reason": str(exc)}
                )
                coordinate_unknown = True
        elif (
            name
            in PathGeom.CmdMoveRapid + PathGeom.CmdMoveStraight + PathGeom.CmdMoveArc
        ):
            cycles.reset()
            if coordinate_unknown:
                unsupported.append(
                    {
                        "index": index,
                        "command": name,
                        "reason": "Unknown position after unsupported motion",
                    }
                )
                continue
            if any(k in params for k in ("A", "B", "C", "U", "V", "W")):
                unsupported.append(
                    {
                        "index": index,
                        "command": name,
                        "reason": "Rotary/auxiliary axes require machine kinematics",
                    }
                )
                coordinate_unknown = True
                continue
            endpoint = App.Vector(position)
            for axis in ("X", "Y", "Z"):
                if axis in params:
                    value = float(params[axis]) * scale
                    setattr(
                        endpoint,
                        axis.lower(),
                        value if absolute else getattr(position, axis.lower()) + value,
                    )
            canonical = {"X": endpoint.x, "Y": endpoint.y, "Z": endpoint.z}
            native = Path.Command(name, canonical)
            try:
                if not all(math.isfinite(value) for value in endpoint):
                    raise ValueError("Motion coordinates must be finite")
                if name in PathGeom.CmdMoveArc:
                    edge, length = path_arc(
                        command, position, endpoint, plane, scale, center_absolute
                    )
                else:
                    edge = PathGeom.edgeForCmd(native, position)
                    length = edge.Length if edge is not None else 0.0
            except Exception as exc:
                unsupported.append(
                    {"index": index, "command": name, "reason": str(exc)}
                )
                position = endpoint
                continue
            segments.append(
                {
                    "index": index,
                    "command": native,
                    "edge": edge,
                    "length_mm": length,
                    "end": endpoint,
                    "rapid": name in PathGeom.CmdMoveRapid,
                }
            )
            position = endpoint
        elif (
            name in ("G4", "G04", "G40", "G49", "G94")
            or name.startswith("M")
            or name.startswith("(")
            or name in ("", "F", "S", "T")
        ):
            non_motion += 1
        else:
            unsupported.append(
                {
                    "index": index,
                    "command": name,
                    "reason": "Unsupported cycle, coordinate offset or compensation",
                }
            )
            coordinate_unknown = True
    return segments, unsupported, non_motion


def path_statistics(path):
    segments, unsupported, non_motion = path_segments(path)
    dwells = [s for s in segments if "dwell_seconds" in s]
    cycle_count = len({s["index"] for s in segments if "cycle" in s})
    segments = [s for s in segments if "dwell_seconds" not in s]
    rapid = sum(s["length_mm"] for s in segments if s["rapid"])
    cutting = sum(s["length_mm"] for s in segments if not s["rapid"])
    complete = not unsupported
    return {
        "command_count": len(path.Commands),
        "path_length": rapid + cutting if complete else None,
        "native_path_length": float(path.Length),
        "rapid_command_count": sum(s["rapid"] for s in segments),
        "cutting_command_count": sum(not s["rapid"] for s in segments),
        "non_motion_command_count": non_motion,
        "length_unit": "mm",
        "rapid_length": rapid if complete else None,
        "cutting_length": cutting if complete else None,
        "measured_rapid_length": rapid,
        "measured_cutting_length": cutting,
        "statistics_complete": complete,
        "unsupported_commands": unsupported,
        "initial_position": [0.0, 0.0, 0.0],
        "expanded_cycle_command_count": cycle_count,
        "cycle_dwell_seconds": sum(s["dwell_seconds"] for s in dwells),
        "cycle_semantics": "LinuxCNC RS274 G81/G82/G85",
    }


def tool_controller(operation):
    """Follow actual dressup links, independent of user-assigned object names."""
    visited = set()
    while operation is not None and operation.Name not in visited:
        visited.add(operation.Name)
        controller = getattr(operation, "ToolController", None)
        if controller is not None:
            return controller
        base = getattr(operation, "Base", None)
        operation = base if hasattr(base, "TypeId") else None
    raise ValueError("Operation has no ToolController")


def simulate_stock(job, resolution, include_mesh):
    """Execute native voxel stock removal and return actual mesh statistics."""
    if not App.GuiUp:
        return {
            "job": job.Name,
            "available": False,
            "reason": "gui_required",
            "backend": "PathSimulator",
            "hint": "Run FreeCAD with GUI for the native voxel simulator",
        }
    import PathSimulator
    import Path.Base.Util as PathUtil
    from PathScripts import PathUtils

    stock = getattr(job, "Stock", None)
    if stock is None or stock.Shape.isNull() or not stock.Shape.isValid():
        raise ValueError("Job requires valid stock geometry")
    initial = App.Vector(0, 0, stock.Shape.BoundBox.ZMax)
    operations = []
    # Validate all operations before starting the native simulator.
    for operation in job.Operations.Group:
        if not PathUtil.activeForOp(operation):
            continue
        controller = tool_controller(operation)
        tool = controller.Tool
        if tool is None or tool.Shape.isNull() or not tool.Shape.isValid():
            raise ValueError("Invalid tool shape for " + operation.Name)
        if (
            any(c.Name in CannedCycleState.supported for c in operation.Path.Commands)
            and hasattr(operation, "Placement")
            and not operation.Placement.isIdentity()
        ):
            # Installed applyPlacementToPath transforms XYZ but not cycle R,
            # and omits G85. Do not simulate mixed transformed/native heights.
            raise ValueError(
                "Canned-cycle operation Placement must be identity; regenerate the operation from the transformed Job setup"
            )
        path = PathUtils.getPathWithPlacement(operation)
        segments, unsupported, _ = path_segments(path, initial)
        if unsupported:
            raise ValueError(
                "Unsupported simulation commands in "
                + operation.Name
                + ": "
                + str(unsupported)
            )
        if not segments:
            raise ValueError("Active operation has no motion: " + operation.Name)
        operations.append((operation, tool, segments))
    if not operations:
        raise ValueError("Job has no active operations")
    simulator = PathSimulator.PathSim()
    simulator.BeginSimulation(stock.Shape, resolution)
    initial_mesh, _ = simulator.GetResultMesh()
    initial_volume = abs(initial_mesh.Volume)
    command_count = 0
    for operation, tool, segments in operations:
        simulator.SetToolShape(tool.Shape, min(0.05, resolution / 10.0))
        placement = App.Placement(initial, App.Rotation())
        for segment in segments:
            if "dwell_seconds" in segment:
                continue  # Dwell does not remove additional voxel material.
            command = segment["command"]
            if command.Name in PathGeom.CmdMoveArc and segment["edge"] is not None:
                # The voxel backend receives linearized arcs, as in the
                # upstream simulator. Tessellation also handles full circles.
                points = segment["edge"].discretize(Deflection=resolution / 4.0)
                for point in points[1:]:
                    placement = simulator.ApplyCommand(
                        placement,
                        Path.Command("G1", {"X": point.x, "Y": point.y, "Z": point.z}),
                    )
            else:
                placement = simulator.ApplyCommand(placement, command)
            command_count += 1
    mesh, internal_mesh = simulator.GetResultMesh()
    volume = abs(mesh.Volume)
    result = {
        "job": job.Name,
        "available": True,
        "backend": "PathSimulator",
        "resolution": resolution,
        "length_unit": "mm",
        "operation_count": len(operations),
        "motion_command_count": command_count,
        "stock_volume": stock.Shape.Volume,
        "initial_mesh_volume": initial_volume,
        "remaining_mesh_volume": volume,
        "removed_mesh_volume": initial_volume - volume,
        "volume_unit": "mm^3",
        "facets": mesh.CountFacets,
        "internal_facets": internal_mesh.CountFacets,
        "approximate": True,
    }
    if include_mesh:
        vertices, triangles = mesh.Topology
        result["mesh"] = {
            "vertices": [list(v) for v in vertices],
            "triangles": [list(t) for t in triangles],
        }
    return result
