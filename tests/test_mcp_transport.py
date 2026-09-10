"""Opt-in stdio MCP -> real bridge -> isolated GUI FreeCAD workflows."""

import asyncio
import contextlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).parents[1]
pytestmark = [
    pytest.mark.freecad,
    pytest.mark.skipif(
        not os.environ.get("FREECAD_TRANSPORT_TESTS"),
        reason="set FREECAD_TRANSPORT_TESTS=1 and FREECAD_BRIDGE_SOURCE for network integration",
    ),
]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize("mode", ["socket", "xmlrpc"])
def test_stdio_mcp_with_native_bridge(tmp_path, mode):
    pytest.importorskip("mcp")
    binary = os.environ["FREECAD_BIN"]
    addon = Path(os.environ["FREECAD_BRIDGE_SOURCE"]).resolve()
    assert (addon / "freecad_mcp_bridge/server.py").is_file()
    ready, stop = tmp_path / "ready.json", tmp_path / "stop"
    socket_port, xmlrpc_port = free_port(), free_port()
    while xmlrpc_port == socket_port:
        xmlrpc_port = free_port()
    macro = tmp_path / "bridge.FCMacro"
    macro.write_text(f"""import sys, json, traceback, os
import FreeCAD as App, FreeCADGui as Gui
from PySide import QtCore
try:
    sys.path.insert(0, {str(addon)!r})
    from freecad_mcp_bridge import FreecadMCPPlugin
    plugin = FreecadMCPPlugin(host='127.0.0.1', port={socket_port}, xmlrpc_port={xmlrpc_port})
    plugin.start()
    def check_stop():
        if os.path.exists({str(stop)!r}):
            timer.stop()
            plugin.stop()
            for name in list(App.listDocuments()):
                App.closeDocument(name)
            Gui.getMainWindow().close()
    timer = QtCore.QTimer()
    timer.timeout.connect(check_stop)
    timer.start(50)
    result = {{'success': True}}
except BaseException:
    result = {{'success': False, 'traceback': traceback.format_exc()}}
with open({str(ready)!r}, 'w') as stream:
    json.dump(result, stream)
""")
    # Raw FreeCAD launcher output is never included in assertions.
    with open(os.devnull, "wb") as sink:
        process = subprocess.Popen(
            [
                binary,
                "-u",
                str(tmp_path / "user.cfg"),
                "-s",
                str(tmp_path / "system.cfg"),
                str(macro),
            ],
            stdout=sink,
            stderr=sink,
        )
        try:
            deadline = time.monotonic() + 20
            while (
                not ready.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            assert ready.exists(), "Isolated FreeCAD bridge did not start"
            status = json.loads(ready.read_text())
            assert status["success"], status.get("traceback")
            asyncio.run(
                asyncio.wait_for(
                    client_workflow(
                        mode, socket_port if mode == "socket" else xmlrpc_port, tmp_path
                    ),
                    timeout=45,
                )
            )
        finally:
            stop.touch()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=3)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)


async def client_workflow(mode, port, output):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "freecad_nexus.server",
            "--mode",
            mode,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
    )
    with open(os.devnull, "w") as sink:
        async with stdio_client(parameters, errlog=sink) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                names = {tool.name for tool in tools}
                assert {
                    "assembly_create",
                    "cam_create_job",
                    "surface_create_bspline",
                } <= names

                async def call(name, arguments):
                    response = await session.call_tool(name, arguments)
                    assert not response.isError, response.content
                    result = response.structuredContent
                    if result is None:
                        result = json.loads(
                            next(
                                item.text
                                for item in response.content
                                if item.type == "text"
                            )
                        )
                    assert result["success"], str(result)
                    return result.get("result")

                # The legacy socket bridge has a 64 KiB incoming line limit;
                # large specialized scripts and mesh/text results exceed it.
                large = await call("execute_python", {"code": "# native runtime padding\n" * 5000 + "_result_={'text':'曲面'*50000,'count':50000}"})
                assert large["count"] == 50000 and large["text"] == "曲面" * 50000
                await call(
                    "execute_python",
                    {
                        "code": "import Part\nApp.newDocument('Network')\no=App.ActiveDocument.addObject('Part::Feature','Model'); o.Shape=Part.makeBox(10,10,5)\n_result_=True"
                    },
                )
                await call("assembly_create", {"name": "Asm"})
                for name in ("LA", "LB"):
                    await call(
                        "assembly_insert_component",
                        {
                            "assembly_name": "Asm",
                            "source_name": "Model",
                            "instance_name": name,
                        },
                    )
                joint = await call(
                    "assembly_add_constraint",
                    {
                        "assembly_name": "Asm",
                        "constraint_type": "Distance",
                        "first": "LA",
                        "second": "LB",
                        "value": 5,
                    },
                )
                assert joint["distance"] == 5
                inspected = await call(
                    "assembly_inspect_joint", {"joint_name": joint["name"]}
                )
                assert inspected["properties"]["Distance"]["value"] == 5
                bom = await call("assembly_create_bom", {"assembly_name": "Asm", "name": "NetworkBom"})
                assert bom["rows"] == 1 and bom["data"][0]["values"]["Quantity"] == 2
                assert bom["structure_validation"]["valid"]
                await call("assembly_edit_bom_cells", {"bom_name": "NetworkBom", "edits": [
                    {"row": 2, "column": "Description", "value": '网络 BOM, "checked"'}
                ]})
                bom_path = output / 'network-bom.csv'
                await call("assembly_export_bom_csv", {"bom_name": "NetworkBom", "file_path": str(bom_path)})
                import csv
                with bom_path.open(newline='', encoding='utf-8') as stream:
                    bom_rows = list(csv.DictReader(stream))
                assert bom_rows[0]['Description'] == '网络 BOM, "checked"'
                parts = await call("assembly_parts_list", {"assembly_name": "Asm", "layout": "flat"})
                assert parts['parts'][0]['quantity'] == 2
                await call("assembly_create_exploded_view", {"assembly_name": "Asm", "name": "NetworkView"})
                await call("assembly_add_exploded_step", {
                    "view_name": "NetworkView", "component_name": "LB", "dx": 10, "dy": 0, "dz": 0,
                    "rotation_angle": 90, "rotation_center": [5, 0, 0],
                })
                frames = await call("assembly_exploded_frames", {"view_name": "NetworkView", "frames_per_step": 2})
                assert frames['frame_count'] == 3 and frames['placements_modified'] is False
                await call("assembly_remove_joint", {"joint_name": joint["name"]})
                await call("assembly_ground_component", {"assembly_name": "Asm", "component_name": "LA"})
                slider = await call("assembly_add_constraint", {
                    "assembly_name": "Asm", "constraint_type": "Slider", "first": "LA", "second": "LB",
                })
                sim = await call("assembly_create_simulation", {"assembly_name": "Asm", "end": 1, "step": .1})
                motion = await call("assembly_add_motion", {
                    "simulation_name": sim["name"], "joint_name": slider["name"], "motion_type": "Linear", "formula": "time",
                })
                await call("assembly_edit_motion", {"motion_name": motion["name"], "formula": "7*time"})
                await call("assembly_edit_simulation", {"simulation_name": sim["name"], "frames_per_second": 60})
                sim_info = await call("assembly_inspect_simulation", {"simulation_name": sim["name"]})
                assert sim_info["frames_per_second"] == 60 and sim_info["motions"][0]["formula"] == "7*time"
                generated = await call("assembly_run_simulation", {"simulation_name": sim["name"]})
                await call("assembly_get_frame", {"assembly_name": "Asm", "frame": generated["frames"] - 1})
                placement = await call("assembly_component_status", {"assembly_name": "Asm", "component_name": "LB"})
                assert abs(placement["placement"]["z"] - 7) < 1e-7
                removed = await call("assembly_remove_simulation", {"simulation_name": sim["name"]})
                assert removed["removed_motions"] == [motion["name"]]
                poles = [[[i, j, 0] for j in range(4)] for i in range(4)]
                surface = await call(
                    "surface_create_bspline", {"name": "Surface", "poles": poles}
                )
                assert surface["pole_counts"] == [4, 4]
                evaluated = await call("surface_evaluate", {
                    "object_name": "Surface", "u": .2, "v": .3,
                    "parameter_space": "native", "face_index": 0,
                })
                assert abs(evaluated["point"][0] - .6) < 1e-8
                assert evaluated["inside_face"] is True
                await call("execute_python", {"code": "import Part\na=App.ActiveDocument.addObject('Part::Feature','LeftFace'); a.Shape=Part.makePlane(10,10)\nb=App.ActiveDocument.addObject('Part::Feature','RightFace'); b.Shape=Part.makePlane(10,10,App.Vector(10,0,0))\n_result_=True"})
                continuity = await call("surface_check_continuity", {
                    "object_name": "LeftFace", "other_object_name": "RightFace",
                    "other_face_index": 0,
                })
                assert continuity["classification"] == "G2" and continuity["proof"] is False
                scaled = await call("surface_set_parameter_range", {
                    "object_name": "Surface", "u_min": 2, "u_max": 4,
                    "v_min": 5, "v_max": 11,
                })
                scaled_point = await call("surface_evaluate", {
                    "object_name": scaled["name"], "u": 2.4, "v": 6.8,
                    "parameter_space": "native",
                })
                assert abs(scaled_point["point"][0] - .6) < 1e-8
                edited = await call("surface_edit_weight", {
                    "object_name": "Surface", "u_index": 2, "v_index": 2,
                    "weight": 1.5, "face_index": 0,
                })
                pole = await call("surface_get_pole", {
                    "object_name": edited["name"], "u_index": 2, "v_index": 2,
                })
                assert pole["weight"] == 1.5
                meshed = await call("surface_tessellate", {
                    "object_name": "Surface", "include_mesh": True,
                })
                assert meshed["triangles"] > 0 and meshed["mesh"]["vertices"]
                await call("cam_create_job", {"name": "Job", "model_names": ["Model"]})
                first = await call("cam_add_tool_controller", {
                    "job_name": "Job", "horizontal_feed": 300, "vertical_feed": 100,
                    "spindle_speed": 12000, "tool": {"Diameter": "2 mm"},
                })
                second = await call("cam_add_tool_controller", {
                    "job_name": "Job", "tool_number": 2, "create_new": True,
                    "horizontal_feed": 600, "tool": {"Diameter": "3 mm"},
                })
                assert abs(first["horizontal_feed"] - 300) < 1e-8
                assert abs(second["horizontal_feed"] - 600) < 1e-8
                assert second["tool"]["Diameter"] == "3.0 mm"
                profile = await call(
                    "cam_add_operation", {"job_name": "Job", "operation": "Profile",
                                          "tool_controller_name": second["name"]}
                )
                assert profile["path_ready"] and profile["tool_controller"] == second["name"]
                path = await call(
                    "cam_get_toolpath", {"operation_name": profile["name"]}
                )
                assert path["length"] > 0 and path["commands"]
                stats = await call(
                    "cam_simulate_toolpath", {"operation_name": profile["name"]}
                )
                assert (
                    stats["rapid_command_count"] > 0
                    and stats["cutting_command_count"] > 0
                )
                setup = await call('cam_inspect_setup', {'job_name': 'Job'})
                moved = await call('cam_transform_setup', {'job_name': 'Job', 'translation': [5, 0, 0]})
                assert moved['source_placements_modified'] is False
                assert moved['models'][0]['bounds']['min'][0] == setup['models'][0]['bounds']['min'][0] + 5
                assert moved['models'][0]['source_placement'] == setup['models'][0]['source_placement']
                moved_path = await call('cam_get_toolpath', {'operation_name': profile['name']})
                assert moved_path['commands'] != path['commands']
                clone_name = setup['models'][0]['name']
                reference = await call('cam_inspect_setup_reference', {'job_name': 'Job', 'reference_object': clone_name, 'subelement': 'Face6'})
                assert reference['direction'][2] > 0.999999
                await call('cam_set_setup_origin', {'job_name': 'Job', 'reference_object': clone_name, 'subelement': 'Face6', 'axes': 'Z'})
                await call('cam_set_operation_base', {'operation_name': profile['name'], 'base_object': clone_name, 'subelements': ['Face6']})
                await call('execute_python', {'code': "import Part\no=App.ActiveDocument.addObject('Part::Feature','Replacement'); o.Shape=Part.makeBox(15,10,7)\n_result_=True"})
                refs = await call('cam_inspect_model_references', {'job_name': 'Job', 'model_name': clone_name})
                assert refs['required_subelements'] == ['Face6']
                replaced = await call('cam_replace_job_model', {'job_name': 'Job', 'model_name': clone_name, 'source_name': 'Replacement', 'subelement_map': {'Face6': 'Face6'}})
                assert replaced['models'][0]['source'] == 'Replacement' and replaced['operations'][0]['path_ready']
                cycle = await call('cam_create_custom_path', {'name': 'Canned', 'commands': [
                    {'name': 'G0', 'parameters': {'Z': 10}}, {'name': 'G99'},
                    {'name': 'G82', 'parameters': {'X': 3, 'Y': 4, 'Z': -3, 'R': 2, 'P': .5}},
                ]})
                cycle_stats = await call('cam_simulate_toolpath', {'operation_name': cycle['name']})
                assert cycle_stats['statistics_complete'] and cycle_stats['cutting_length'] == 5
                assert cycle_stats['rapid_length'] == 28 and cycle_stats['cycle_dwell_seconds'] == .5
                nurbs_box = await call('surface_to_nurbs', {'object_name': 'Replacement'})
                rebuilt = await call('surface_replace_faces', {'object_name': 'Replacement',
                    'replacements': [{'face_index': 0, 'object': nurbs_box['name']}], 'name': 'RebuiltBox'})
                assert rebuilt['is_valid'] and rebuilt['solids'] == 1 and abs(rebuilt['volume'] - 1050) < 1e-6
                (output / "transport-result.json").write_text(
                    json.dumps(
                        {
                            "mode": mode,
                            "registered_tools": len(names),
                            "joint_distance": joint["distance"],
                            "surface_poles": surface["pole_counts"],
                            "surface_continuity": continuity["classification"],
                            "profile_commands": path["command_count"],
                        }
                    )
                )
