"""Fast unit checks for Nexus tool modules without FreeCAD or MCP runtime."""
import ast
import asyncio
from pathlib import Path

ROOT = Path(__file__).parents[1] / "src" / "freecad_nexus"


def _functions(path: Path):
    tree = ast.parse(path.read_text())
    return {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_specialized_modules_have_expected_tools():
    assembly = _functions(ROOT / "tools" / "assembly.py")
    cam = _functions(ROOT / "tools" / "cam.py")
    surface = _functions(ROOT / "tools" / "surface.py")
    assert {"assembly_create", "assembly_add_constraint", "assembly_solve", "assembly_run_simulation", "assembly_solver_diagnostics", "assembly_import_asmt", "assembly_get_global_placement", "assembly_set_property", "assembly_list_subobjects", "assembly_set_element_visibility", "assembly_get_joint_connector", "assembly_set_joint_connector", "assembly_get_subobject_placement"} <= assembly
    assert {"cam_create_job", "cam_add_tool_controller", "cam_add_operation", "cam_generate_gcode", "cam_simulate_stock", "cam_set_fixture", "cam_remove_operation", "cam_remove_tool_controller", "cam_set_tool_controller_property", "cam_reorder_operations", "cam_set_center_of_rotation", "cam_create_job_from_template", "cam_export_job_template", "cam_import_toolbit", "cam_create_tool_library", "cam_create_machine"} <= cam
    assert {"surface_create_bspline", "surface_create_bezier", "surface_loft", "surface_sew", "surface_to_solid", "surface_trim_parameters", "surface_control_net", "surface_edit_pole", "surface_edit_weight", "surface_normal", "surface_project_point", "surface_exchange_uv", "surface_reparameterize", "surface_tangent", "surface_derivative", "surface_get_pole", "surface_set_knot", "surface_set_periodic", "surface_insert_knot", "surface_remove_knot", "surface_increase_degree"} <= surface


def test_all_specialized_modules_compile():
    for name in ("assembly.py", "cam.py", "surface.py"):
        ast.parse((ROOT / "tools" / name).read_text())


def test_specialized_tools_generate_valid_bridge_code():
    """Catch formatting regressions before code reaches a FreeCAD bridge."""
    import importlib.util

    def load(name):
        spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    assembly, cam, surface = (load(n) for n in ("assembly", "cam", "surface"))

    class Result:
        success = True
        result = None
        stdout = stderr = error_traceback = ""

    class Bridge:
        def __init__(self):
            self.codes = []

        async def execute_python(self, code):
            self.codes.append(code)
            compile(code, "<generated>", "exec")
            return Result()

    async def exercise(module, function_name, kwargs):
        bridge = Bridge()

        async def get_bridge():
            return bridge

        funcs = {}

        class MCP:
            def tool(self):
                def decorate(fn):
                    funcs[fn.__name__] = fn
                    return fn

                return decorate

        register = {
            assembly: assembly.register_assembly_tools,
            cam: cam.register_cam_tools,
            surface: surface.register_surface_tools,
        }[module]
        register(MCP(), get_bridge)
        await funcs[function_name](**kwargs)
        assert bridge.codes and "openTransaction" in bridge.codes[-1]
        return bridge.codes[-1]

    asyncio.run(exercise(assembly, "assembly_insert_external_component", {
        "assembly_name": "Asm", "file_path": "/tmp/a.FCStd",
    }))
    asyncio.run(exercise(assembly, "assembly_set_joint_state", {
        "joint_name": "Joint", "suppressed": True,
    }))
    asyncio.run(exercise(assembly, "assembly_set_joint_state", {
        "joint_name": "Joint", "suppressed": False, "angle": 15.0, "distance": 2.0,
    }))
    global_code = asyncio.run(exercise(assembly, "assembly_get_global_placement", {
        "assembly_name": "Asm", "object_name": "Part",
    }))
    assert "getGlobalPlacement" in global_code
    asyncio.run(exercise(assembly, "assembly_list_subobjects", {"assembly_name": "Asm"}))
    asyncio.run(exercise(assembly, "assembly_set_element_visibility", {"assembly_name": "Asm", "element": "Part", "visible": True}))
    asyncio.run(exercise(assembly, "assembly_get_joint_connector", {"joint_name": "Joint", "connector": 1}))
    asyncio.run(exercise(assembly, "assembly_set_joint_connector", {"joint_name": "Joint", "connector": 1, "detach": True}))
    asyncio.run(exercise(assembly, "assembly_get_subobject_placement", {"assembly_name": "Asm", "subname": "Part"}))
    cam_code = asyncio.run(exercise(cam, "cam_add_operation", {
        "job_name": "Job", "operation": "Profile", "parameters": {"Depth": -1},
    }))
    asyncio.run(exercise(cam, "cam_reorder_operations", {"job_name": "Job", "operation_names": ["Op"]}))
    asyncio.run(exercise(cam, "cam_set_center_of_rotation", {"job_name": "Job", "x": 0.0, "y": 0.0, "z": 0.0}))
    asyncio.run(exercise(cam, "cam_create_job_from_template", {"name": "Job", "template_file": "/on-freecad-host/job.json"}))
    asyncio.run(exercise(cam, "cam_export_job_template", {"job_name": "Job", "file_path": "/on-freecad-host/job.json"}))
    asyncio.run(exercise(cam, "cam_import_toolbit", {"tool_file": "/on-freecad-host/tool.fctb"}))
    asyncio.run(exercise(cam, "cam_export_toolbit", {"tool_name": "Tool", "file_path": "/on-freecad-host/tool.fctb"}))
    poles = [[[float(i),float(j),0.0] for j in range(4)] for i in range(4)]
    asyncio.run(exercise(surface, "surface_create_bspline", {"name":"S","poles":poles}))
    asyncio.run(exercise(surface, "surface_create_bezier", {"name":"B","poles":poles}))
    asyncio.run(exercise(surface, "surface_interpolate_points", {"name":"I","points":poles}))
    asyncio.run(exercise(surface, "surface_edit_control_net", {"object_name":"S","edits":[{"u_index":1,"v_index":1,"weight":2.0}]}))
    asyncio.run(exercise(surface, "surface_fill_boundary", {"name":"F","boundary_names":["A","B"],"method":"coons"}))
    asyncio.run(exercise(surface, "surface_create_filling", {"name":"F","boundaries":[{"object":"A","edge":"Edge1"}]}))
    asyncio.run(exercise(surface, "surface_create_sections", {"name":"F","sections":[{"object":"A"},{"object":"B"}]}))
    asyncio.run(exercise(surface, "surface_extend", {"object_name":"F","u_min":.1}))
    asyncio.run(exercise(surface, "surface_sew", {"name":"Shell","object_names":["A","B"]}))
    asyncio.run(exercise(surface, "surface_blend_curve", {"name":"Blend","start_object":"A","end_object":"B"}))
    asyncio.run(exercise(surface, "surface_capabilities", {}))
    asyncio.run(exercise(surface, "surface_trim_parameters", {
        "object_name": "Surface", "u_min": 0.1, "u_max": 0.9,
        "v_min": 0.1, "v_max": 0.9,
    }))

    asyncio.run(exercise(surface, "surface_reparameterize", {
        "object_name": "Surface", "u_poles": 4, "v_poles": 4,
    }))
    asyncio.run(exercise(surface, "surface_edit_pole", {
        "object_name": "Surface", "u_index": 1, "v_index": 1,
        "x": 0.0, "y": 0.0, "z": 0.0,
    }))
    asyncio.run(exercise(surface, "surface_edit_weight", {
        "object_name": "Surface", "u_index": 1, "v_index": 1,
        "weight": 1.0,
    }))
    asyncio.run(exercise(surface, "surface_normal", {"object_name": "Surface"}))
    asyncio.run(exercise(surface, "surface_project_point", {
        "object_name": "Surface", "x": 0.0, "y": 0.0, "z": 0.0,
    }))
    asyncio.run(exercise(surface, "surface_exchange_uv", {"object_name": "Surface"}))
    asyncio.run(exercise(surface, "surface_tangent", {"object_name": "Surface"}))
    asyncio.run(exercise(surface, "surface_derivative", {"object_name": "Surface"}))
    asyncio.run(exercise(surface, "surface_get_pole", {"object_name": "Surface", "u_index": 1, "v_index": 1}))
    asyncio.run(exercise(surface, "surface_set_knot", {"object_name": "Surface", "direction": "U", "index": 1, "value": 0.0}))
    asyncio.run(exercise(surface, "surface_set_periodic", {"object_name": "Surface", "direction": "U", "periodic": False}))
    asyncio.run(exercise(surface, "surface_insert_knot", {"object_name": "Surface", "direction": "U", "parameter": 0.25}))
    asyncio.run(exercise(surface, "surface_remove_knot", {"object_name": "Surface", "direction": "U", "index": 1}))
    asyncio.run(exercise(surface, "surface_increase_degree", {"object_name": "Surface", "u_degree": 3, "v_degree": 3}))


def test_specialized_capability_catalog_matches_registered_tools():
    """An implemented tool missing from discovery is a functional regression."""
    import importlib.util
    import json

    spec=importlib.util.spec_from_file_location('resources',ROOT/'resources/freecad.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    resources={}
    class Registry:
        def resource(self, uri):
            def decorate(fn):
                resources[uri]=fn
                return fn
            return decorate
    module.register_resources(Registry(), None)
    catalog=json.loads(asyncio.run(resources['freecad://capabilities']()))['tools']
    for domain, category in [('assembly','assembly'),('cam','cam_path'),('surface','surface')]:
        tree=ast.parse((ROOT/'tools'/f'{domain}.py').read_text())
        registered={n.name for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.decorator_list}
        listed=[item['name'] for item in catalog[category]['tools']]
        assert len(listed)==len(set(listed)), f'Duplicate {domain} tool'
        assert registered==set(listed)
