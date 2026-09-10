"""Native adapter assertions, imported by FreeCAD's bundled Python only."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import traceback


def tools_for(root, domain):
    import FreeCAD as App

    spec = importlib.util.spec_from_file_location(
        domain, Path(root) / "src/freecad_nexus/tools" / (domain + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registered = {}

    class Registry:
        def tool(self):
            def decorate(fn):
                registered[fn.__name__] = fn
                return fn

            return decorate

    class NativeBridge:
        async def execute_python(self, code):
            scope = {"App": App, "FreeCAD": App}
            try:
                exec(compile(code, "<MCP adapter>", "exec"), scope)
                return SimpleNamespace(
                    success=True,
                    result=scope.get("_result_"),
                    stdout="",
                    stderr="",
                    error_traceback="",
                )
            except Exception:
                return SimpleNamespace(
                    success=False,
                    result=None,
                    stdout="",
                    stderr="",
                    error_traceback=traceback.format_exc(),
                )

    async def bridge():
        return NativeBridge()

    getattr(module, "register_" + domain + "_tools")(Registry(), bridge)

    def call(tool_name, /, expect_success=True, **kwargs):
        # Every await in this bridge completes immediately. Stepping directly
        # avoids an active asyncio loop around FreeCAD's synchronous asset API.
        coro = registered[tool_name](**kwargs)
        try:
            coro.send(None)
        except StopIteration as finished:
            result = finished.value
        else:
            coro.close()
            raise AssertionError("Unexpected asynchronous suspension")
        assert result["success"] is expect_success, f"{tool_name}: {result}"
        if expect_success:
            json.dumps(result["result"])  # Result must survive the MCP JSON boundary.
        return result["result"] if expect_success else result

    return call


def grid():
    return [
        [[float(i), float(j), float(i * j) / 4] for j in range(4)] for i in range(4)
    ]


def surface_geometry(root, output):
    from surface_geometry_cases import run

    return run(root, output)


def surface_shapes(root, output):
    from surface_shape_cases import run

    return run(root, output)


def surface_seams(root, output):
    from surface_seam_cases import run

    return run(root, output)


def cam_arc_geometry(root, output):
    from cam_arc_cases import run

    return run(root, output)


def cam_setup_alignment(root, output):
    from cam_alignment_cases import run

    return run(root, output)


def cam_model_replacement(root, output):
    from cam_model_cases import run

    return run(root, output)


def assembly_instance_sync(root, output):
    from assembly_sync_cases import run

    return run(root, output)


def assembly_joint_families(root, output):
    from assembly_joint_cases import run

    return run(root, output)


def assembly_coupled_motion(root, output):
    from assembly_coupling_cases import run

    return run(root, output)


def assembly_bom_lifecycle(root, output):
    from assembly_bom_cases import run

    return run(root, output)


def assembly_flexible_instances(root, output):
    from assembly_instance_cases import run

    return run(root, output)


def cam_rotation_center(root, output):
    from cam_center_cases import run

    return run(root, output)


def cam_setup_lifecycle(root, output):
    from cam_setup_cases import run

    return run(root, output)


def assembly_simulation_lifecycle(root, output):
    from assembly_simulation_cases import run

    return run(root, output)


def cam_stock_setup(root, output):
    from cam_stock_cases import run

    return run(root, output)


def cam_postprocessing(root, output):
    from cam_post_cases import run

    return run(root, output)


def surface_construction(root, output):
    import FreeCAD as App

    App.newDocument("SurfaceConstruction")
    call = tools_for(root, "surface")
    pts = grid()
    weights = [[1.0] * 4 for _ in range(4)]
    weights[1][2] = 2.0
    result = call("surface_create_bspline", name="Nurbs", poles=pts, weights=weights)
    s = App.ActiveDocument.getObject(result["name"]).Shape.Faces[0].Surface
    assert s.NbUPoles == s.NbVPoles == 4
    assert s.getPole(2, 3).isEqual(App.Vector(*pts[1][2]), 1e-12)
    assert abs(s.getWeight(2, 3) - 2.0) < 1e-12
    result = call("surface_create_bezier", name="Bezier", poles=pts, weights=weights)
    b = App.ActiveDocument.getObject(result["name"]).Shape.Faces[0].Surface
    assert b.NbUPoles == b.NbVPoles == 4
    assert b.getPole(3, 2).isEqual(App.Vector(*pts[2][1]), 1e-12)
    sampled = call("surface_interpolate_points", name="Fitted", points=pts)
    assert App.ActiveDocument.getObject(sampled["name"]).Shape.isValid()
    return {"pole_grid": [s.NbUPoles, s.NbVPoles], "rational_weight": s.getWeight(2, 3)}


def surface_cut_mesh(root, output):
    from surface_mesh_cases import run

    return run(root, output)


def surface_spline_editing(root, output):
    from surface_spline_cases import run

    return run(root, output)


def surface_editing(root, output):
    import FreeCAD as App

    App.newDocument("SurfaceEditing")
    call = tools_for(root, "surface")
    call("surface_create_bspline", name="Source", poles=grid())
    source = App.ActiveDocument.Source.Shape.Faces[0].Surface
    call(
        "surface_insert_knot",
        object_name="Source",
        direction="U",
        parameter=0.3,
        name="Inserted",
    )
    result = call(
        "surface_remove_knot",
        object_name="Inserted",
        direction="U",
        index=2,
        multiplicity=0,
        name="Removed",
    )
    assert result["native_result"] is True and result["knots"] == [0.0, 1.0]
    elevated = call(
        "surface_increase_degree",
        object_name="Source",
        u_degree=5,
        v_degree=4,
        name="Elevated",
    )
    assert (elevated["u_degree"], elevated["v_degree"]) == (5, 4)
    surface = App.ActiveDocument.Elevated.Shape.Faces[0].Surface
    for u, v in [(0, 0), (0.13, 0.61), (0.85, 0.21), (1, 1)]:
        assert (surface.value(u, v) - source.value(u, v)).Length < 1e-8
    call(
        "surface_edit_control_net",
        object_name="Source",
        name="Edited",
        edits=[
            {"u_index": 2, "v_index": 3, "pole": [1.0, 2.0, 3.0], "weight": 1.7},
            {"u_index": 3, "v_index": 2, "pole": [2.0, 1.0, 2.0]},
        ],
    )
    edited = App.ActiveDocument.Edited.Shape.Faces[0].Surface
    assert abs(edited.getWeight(2, 3) - 1.7) < 1e-12
    assert edited.getPole(2, 3).z == 3 and source.getPole(2, 3).z == 0.5
    # A deformed interior control net cannot in general lose a knot under a
    # very strict tolerance. OCC false must produce a failure and no object.
    call(
        "surface_edit_control_net",
        object_name="Inserted",
        name="Perturbed",
        edits=[{"u_index": 2, "v_index": 2, "pole": [1.0, 1.0, 8.0]}],
    )
    count = len(App.ActiveDocument.Objects)
    refused = call(
        "surface_remove_knot",
        expect_success=False,
        object_name="Perturbed",
        direction="U",
        index=2,
        multiplicity=0,
        tolerance=1e-12,
    )
    assert "cannot meet tolerance" in refused["error"]
    assert len(App.ActiveDocument.Objects) == count
    before = [o.Name for o in App.ActiveDocument.Objects]
    call(
        "surface_remove_knot",
        expect_success=False,
        object_name="Source",
        direction="U",
        index=1,
    )
    call(
        "surface_edit_control_net",
        expect_success=False,
        object_name="Source",
        edits=[{"u_index": 99, "v_index": 1, "weight": 2.0}],
    )
    assert before == [o.Name for o in App.ActiveDocument.Objects]
    return {"degree_elevated": [5, 4], "removed_knot": 0.3, "source_preserved": True}


def cam_templates(root, output):
    import FreeCAD as App
    import Part

    App.newDocument("CamTemplates")
    call = tools_for(root, "cam")
    model = App.ActiveDocument.addObject("Part::Feature", "Model")
    model.Shape = Part.makeBox(20, 20, 10)
    call(
        "cam_create_job",
        name="Job",
        model_names=["Model"],
        stock_type="Box",
        stock_params={"Length": 30, "Width": 40, "Height": 50},
    )
    job = App.ActiveDocument.Job
    call("cam_set_fixture", job_name="Job", fixture_names=["G54", "G55"])
    assert job.Fixtures == ["G54", "G55"]
    tc = job.Tools.Group[0]
    tc.HorizFeed = "123 mm/min"
    tc.Tool.Diameter = "6 mm"
    tc.setExpression("VertRapid", job.SetupSheet.Name + ".VertRapid")
    job.SetupSheet.SafeHeightOffset = "8 mm"
    job.SetupSheet.VertRapid = "240 mm/min"
    path = str(Path(output) / "job.json")
    call("cam_export_job_template", job_name="Job", file_path=path)
    template = json.loads(Path(path).read_text())
    assert "SetupSheet" in template and len(template["ToolController"]) == 1
    loaded = call(
        "cam_create_job_from_template",
        name="Loaded",
        template_file=path,
        model_names=["Model"],
    )
    copy = App.ActiveDocument.getObject(loaded["name"])
    assert abs(copy.Stock.Length.Value - 30) < 1e-9
    assert copy.Fixtures == ["G54", "G55"]
    assert abs(copy.SetupSheet.SafeHeightOffset.Value - 8) < 1e-9
    assert abs(copy.Tools.Group[0].HorizFeed.Value - tc.HorizFeed.Value) < 1e-9
    assert copy.Tools.Group[0].Tool.Diameter.Value == 6
    assert copy.SetupSheet.Name in copy.Tools.Group[0].ExpressionEngine[0][1]
    tool_file = str(Path(output) / "tool.fctb")
    call("cam_export_toolbit", tool_name=tc.Tool.Name, file_path=tool_file)
    imported = call("cam_import_toolbit", tool_file=tool_file, job_name=copy.Name)
    assert App.ActiveDocument.getObject(imported["tool_controller"]) in copy.Tools.Group
    assert App.ActiveDocument.getObject(imported["tool"]).Diameter.Value == 6
    before = len(App.ActiveDocument.Objects)
    call(
        "cam_import_toolbit",
        expect_success=False,
        tool_file=tool_file,
        job_name="MissingJob",
    )
    assert len(App.ActiveDocument.Objects) == before
    # A stale controller link must not disappear when removing its operation.
    op = call(
        "cam_add_operation",
        job_name="Job",
        operation="Profile",
        tool_controller_name=tc.Name,
    )
    operation = App.ActiveDocument.getObject(op["name"])
    assert operation.Path.Commands
    schema = call("cam_get_operation_schema", operation="Profile", job_name="Job")
    side = next(prop for prop in schema["properties"] if prop["name"] == "Side")
    assert side["enum"] == operation.getEnumerationsOfProperty("Side")
    assert call(
        "cam_validate_operation_parameters",
        operation_name=operation.Name,
        parameters={"Side": side["enum"][0]},
    )["valid"]
    assert not call(
        "cam_validate_operation_parameters",
        operation_name=operation.Name,
        parameters={"Side": "invalid"},
    )["valid"]
    dressup = call(
        "cam_add_dressup",
        dressup_name="Dressed",
        dressup="Boundary",
        base_operation=operation.Name,
    )
    assert App.ActiveDocument.getObject(dressup["name"]).Base is operation
    call("cam_remove_operation", operation_name=operation.Name)
    assert App.ActiveDocument.getObject(dressup["name"]) is None
    assert App.ActiveDocument.getObject(tc.Name) is tc and tc in job.Tools.Group
    return {
        "template_roundtrip": True,
        "toolbit_roundtrip": True,
        "source_controller_preserved": True,
    }


def assembly_connectors(root, output):
    import FreeCAD as App
    import Part

    App.newDocument("AssemblyConnectors")
    call = tools_for(root, "assembly")
    call("assembly_create", name="Asm")
    for n in ("A", "B"):
        o = App.ActiveDocument.addObject("Part::Feature", n)
        o.Shape = Part.makeBox(2, 2, 2)
        call(
            "assembly_insert_component",
            assembly_name="Asm",
            source_name=n,
            instance_name="L" + n,
        )
    result = call(
        "assembly_add_constraint",
        assembly_name="Asm",
        constraint_type="Distance",
        first="LA",
        second="LB",
        first_element="Face1",
        second_element="Face1",
        value=5,
    )
    joint = result["name"]
    call(
        "assembly_set_joint_connector",
        joint_name=joint,
        x=1.0,
        y=2.0,
        z=3.0,
        angle=30.0,
    )
    result = call("assembly_get_joint_connector", joint_name=joint)
    assert result["detach"] and result["reference"]["object"] == "LA"
    old_rotation = result["placement"]["rotation"]
    call("assembly_set_joint_connector", joint_name=joint, x=4.0)
    result = call("assembly_get_joint_connector", joint_name=joint)
    assert result["placement"]["base"] == [4.0, 2.0, 3.0]
    assert result["placement"]["rotation"] == old_rotation
    call("assembly_set_joint_connector", joint_name=joint, angle=60.0)
    call("assembly_set_joint_connector", joint_name=joint, detach=False)
    assert not call("assembly_get_joint_connector", joint_name=joint)["detach"]
    call("assembly_set_placement", instance_name="LA", x=7.0)
    plc = call("assembly_get_subobject_placement", assembly_name="Asm", subname="LA.")
    assert abs(plc["base"][0] - 7) < 1e-9
    top = App.ActiveDocument.addObject("App::Part", "Top")
    top.Placement.Base = App.Vector(100, 0, 0)
    top.addObject(App.ActiveDocument.Asm)
    App.ActiveDocument.Asm.Placement.Base = App.Vector(10, 0, 0)
    plc = call("assembly_get_global_placement", assembly_name="Asm", object_name="LA")
    assert abs(plc["base"][0] - 117) < 1e-9, plc
    assert plc["root_object"] == "Top" and plc["subname"] == "Asm.LA."
    plc = call(
        "assembly_get_global_placement",
        assembly_name="Asm",
        object_name="A",
        subname="LA.",
    )
    assert abs(plc["base"][0] - 117) < 1e-9
    call(
        "assembly_get_global_placement",
        expect_success=False,
        assembly_name="Asm",
        object_name="A",
        subname="BadPath.",
    )
    return {"connector_partial_edit": True, "reference_restored": True}


def surface_features(root, output):
    import FreeCAD as App
    import Part

    App.newDocument("SurfaceFeatures")
    call = tools_for(root, "surface")
    corners = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 0)]
    boundaries = []
    for i in range(4):
        curve = Part.BezierCurve()
        curve.setPoles([App.Vector(*corners[i]), App.Vector(*corners[i + 1])])
        obj = App.ActiveDocument.addObject("Part::Feature", "Border" + str(i))
        obj.Shape = curve.toShape()
        boundaries.append({"object": obj.Name, "edge": "Edge1"})
    for style in ("stretch", "coons", "curved"):
        result = call(
            "surface_fill_boundary",
            name="Fill_" + style,
            boundary_names=[r["object"] for r in boundaries],
            method=style,
        )
        obj = App.ActiveDocument.getObject(result["name"])
        assert (
            obj.TypeId == "Surface::GeomFillSurface"
            and abs(obj.Shape.Area - 100) < 1e-6
        )
        assert (
            obj.FillType
            == {"stretch": "Stretched", "coons": "Coons", "curved": "Curved"}[style]
        )
    result = call(
        "surface_create_filling",
        name="Filling",
        boundaries=boundaries,
        settings={"Tolerance3d": 0.001},
    )
    filled = App.ActiveDocument.getObject(result["name"])
    assert filled.TypeId == "Surface::Filling" and abs(filled.Shape.Area - 100) < 1e-6
    result = call(
        "surface_create_sections",
        name="Sections",
        sections=[
            {"object": "Border0", "element": "Edge1"},
            {"object": "Border2", "element": "Edge1"},
        ],
    )
    section = App.ActiveDocument.getObject(result["name"])
    assert section.TypeId == "Surface::Sections" and section.Shape.isValid()
    call(
        "surface_extend",
        object_name="Fill_stretch",
        name="Extended",
        u_min=0.1,
        u_max=0.2,
        v_min=0.15,
        v_max=0.05,
        tolerance=0.001,
    )
    extended = App.ActiveDocument.Extended
    assert (
        extended.TypeId == "Surface::Extend" and extended.Face[0].Name == "Fill_stretch"
    )
    assert abs(extended.Shape.Area - 156) < 1e-4, extended.Shape.Area
    support = App.ActiveDocument.addObject("Part::Feature", "Support")
    support.Shape = Part.makePlane(10, 10)
    call(
        "surface_create_filling",
        name="G1Fill",
        boundaries=[
            {
                "object": "Support",
                "edge": "Edge" + str(i),
                "face": "Face1",
                "continuity": "G1",
            }
            for i in range(1, 5)
        ],
    )
    assert App.ActiveDocument.G1Fill.BoundaryOrder == [1] * 4
    for n, a, b in [
        ("BlendStart", (0, 0, 0), (2, 0, 0)),
        ("BlendEnd", (5, 2, 0), (7, 2, 0)),
    ]:
        obj = App.ActiveDocument.addObject("Part::Feature", n)
        obj.Shape = Part.makeLine(App.Vector(*a), App.Vector(*b))
    call(
        "surface_blend_curve",
        name="Blend",
        start_object="BlendStart",
        end_object="BlendEnd",
    )
    edge = App.ActiveDocument.Blend.Shape.Edges[0]
    assert (edge.valueAt(edge.FirstParameter) - App.Vector(2, 0, 0)).Length < 1e-8
    assert (edge.valueAt(edge.LastParameter) - App.Vector(5, 2, 0)).Length < 1e-8
    box = Part.makeBox(3, 4, 5)
    names = []
    for face in box.Faces:
        obj = App.ActiveDocument.addObject("Part::Feature", "BoxFace")
        obj.Shape = face
        names.append(obj.Name)
    sewn = call("surface_sew", name="Sewn", object_names=names)
    assert sewn["is_closed"] and sewn["shells"] == 1
    call("surface_to_solid", object_name="Sewn", name="Solid")
    assert App.ActiveDocument.Solid.Shape.ShapeType == "Solid"
    assert abs(App.ActiveDocument.Solid.Shape.Volume - 60) < 1e-8
    cap = call("surface_capabilities")
    assert cap["has_surface_extend"] and not cap["has_bspline_extend_method"]
    assert App.ActiveDocument.Name == "SurfaceFeatures"
    # Native dependency links survive FCStd serialization and recompute.
    path = str(Path(output) / "surfaces.FCStd")
    doc = App.ActiveDocument
    doc.saveAs(path)
    App.closeDocument(doc.Name)
    doc = App.openDocument(path)
    assert doc.Extended.Face[0] is doc.Fill_stretch
    assert doc.Filling.BoundaryOrder == [0] * 4
    curve = Part.BezierCurve()
    curve.setPoles([App.Vector(0, 12, 0), App.Vector(10, 12, 0)])
    doc.Border2.Shape = curve.toShape()
    doc.recompute()
    assert abs(doc.Sections.Shape.BoundBox.YMax - 12) < 1e-6
    return {
        "fill_styles": 3,
        "native_filling": True,
        "native_extension_area": 156,
        "serialized_links": True,
    }


def assembly_exploded_lifecycle(root, output):
    from assembly_view_cases import run

    return run(root, output)


def assembly_motion_and_view(root, output):
    import FreeCAD as App
    import Part
    import CommandCreateSimulation, CommandCreateView

    App.newDocument("AssemblyMotion")
    call = tools_for(root, "assembly")
    call("assembly_create", name="Asm")
    for n in ("A", "B"):
        o = App.ActiveDocument.addObject("Part::Feature", n)
        o.Shape = Part.makeBox(2, 2, 2)
        call(
            "assembly_insert_component",
            assembly_name="Asm",
            source_name=n,
            instance_name="L" + n,
        )
    ground = call("assembly_ground_component", assembly_name="Asm", component_name="LA")
    joint = call(
        "assembly_add_constraint",
        assembly_name="Asm",
        constraint_type="Slider",
        first="LA",
        second="LB",
    )
    assert call("assembly_solve", assembly_name="Asm")["solved"]
    assert call("assembly_validate_references", assembly_name="Asm")["valid"]
    freedom = call("assembly_get_freedom", assembly_name="Asm")
    assert (
        freedom["components"] == 2
        and freedom["grounded"] == 1
        and freedom["estimated_dof"] == 1
    )
    diagnostic = call("assembly_solver_diagnostics", assembly_name="Asm")
    assert (
        diagnostic["solved"]
        and diagnostic["invalid_references"] == []
        and diagnostic["conflicts"] is None
    )
    props = call("assembly_inspect_joint", joint_name=joint["name"])["properties"]
    assert props["Distance"]["value"] == 0 and props["Reference1"][0]["object"] == "LA"
    call(
        "assembly_set_joint_limits",
        joint_name=joint["name"],
        enable_length_min=True,
        length_min=-20,
        enable_length_max=True,
        length_max=20,
    )
    sim = call(
        "assembly_create_simulation", assembly_name="Asm", start=0, end=1, step=0.1
    )
    s = App.ActiveDocument.getObject(sim["name"])
    assert isinstance(s.Proxy, CommandCreateSimulation.Simulation)
    motion = call(
        "assembly_add_motion",
        simulation_name=s.Name,
        joint_name=joint["name"],
        motion_type="Linear",
        formula="5*time",
    )
    assert isinstance(
        App.ActiveDocument.getObject(motion["name"]).Proxy,
        CommandCreateSimulation.Motion,
    )
    frames = call("assembly_run_simulation", simulation_name=s.Name)
    call("assembly_get_frame", assembly_name="Asm", frame=0)
    initial = App.ActiveDocument.LB.Placement.copy()
    call("assembly_get_frame", assembly_name="Asm", frame=frames["frames"] - 1)
    final = App.ActiveDocument.LB.Placement.copy()
    displacement = (final.Base - initial.Base).Length
    assert abs(displacement - 5) < 1e-6, displacement
    call("assembly_get_frame", assembly_name="Asm", frame=0)
    before = App.ActiveDocument.LB.Placement.copy()
    view = call("assembly_create_exploded_view", assembly_name="Asm")
    call(
        "assembly_add_exploded_step",
        view_name=view["name"],
        component_name="LB",
        dx=10,
        dy=0,
        dz=0,
    )
    assert App.ActiveDocument.LB.Placement.isSame(before, 1e-9)
    v = App.ActiveDocument.getObject(view["name"])
    assert len(v.Group) == 1 and isinstance(
        v.Group[0].Proxy, CommandCreateView.ExplodedViewStep
    )
    shape = call("assembly_export_exploded_shape", view_name=v.Name)
    assert shape["valid"] and App.ActiveDocument.LB.Placement.isSame(before, 1e-9)
    assert App.ActiveDocument.getObject(shape["name"]).Shape.BoundBox.XLength >= 12
    filename = str(Path(output) / "assembly.FCStd")
    App.ActiveDocument.recompute()
    App.ActiveDocument.saveAs(filename)
    App.closeDocument(App.ActiveDocument.Name)
    App.openDocument(filename)
    assert isinstance(
        App.ActiveDocument.getObject(sim["name"]).Proxy,
        CommandCreateSimulation.Simulation,
    )
    assert isinstance(
        App.ActiveDocument.getObject(view["name"]).Proxy, CommandCreateView.ExplodedView
    )
    assert call("assembly_run_simulation", simulation_name=sim["name"])["frames"] > 1
    removed = call(
        "assembly_remove_component", assembly_name="Asm", component_name="LA"
    )
    assert set(removed["removed_joints"]) == {ground["joint"], joint["name"]}
    assert removed["removed_motions"] == [motion["name"]]
    assert (
        App.ActiveDocument.A
        and App.ActiveDocument.B
        and not App.ActiveDocument.getObject("LA")
    )
    return {
        "frames": frames["frames"],
        "linear_displacement": displacement,
        "exploded_source_preserved": True,
        "save_reopen": True,
    }


def cam_path_statistics(root, output):
    import FreeCAD as App
    import Part

    App.newDocument("CamPaths")
    call = tools_for(root, "cam")
    dressups = call("cam_list_dressups")
    assert dressups["installed_count"] > dressups["available_count"]
    ramp = next(d for d in dressups["dressups"] if d["dressup"] == "RampEntry")
    assert ramp["installed"] and ramp["mcp_creation_supported"] and not ramp["available"]
    assert "RampEntry" in call("cam_capabilities")["installed_dressups"]
    commands = [
        {"name": "G0", "parameters": {"X": 3, "Y": 4}},
        {"name": "G1", "parameters": {"Z": 12}},
        {"name": "M5"},
    ]
    created = call("cam_create_custom_path", name="Custom", commands=commands)
    result = call("cam_simulate_toolpath", operation_name=created["name"])
    assert (
        result["rapid_command_count"] == 1
        and result["cutting_command_count"] == 1
        and result["non_motion_command_count"] == 1
    )
    assert (
        result["statistics_complete"]
        and abs(result["rapid_length"] - 5) < 1e-9
        and abs(result["cutting_length"] - 12) < 1e-9
    )
    call("cam_remove_operation", operation_name=created["name"])
    assert not App.ActiveDocument.getObject(created["name"])
    commands = [
        {"name": "G0", "parameters": {"X": 1, "Y": 0}},
        {"name": "G3", "parameters": {"X": 0, "Y": 1, "I": -1, "J": 0}},
    ]
    created = call("cam_create_custom_path", name="Arc", commands=commands)
    result = call("cam_simulate_toolpath", operation_name=created["name"])
    import math

    assert abs(result["cutting_length"] - math.pi / 2) < 1e-8
    commands = [
        {"name": "G20"},
        {"name": "G91"},
        {"name": "G1", "parameters": {"X": 1}},
        {"name": "G1", "parameters": {"X": 1}},
    ]
    created = call("cam_create_custom_path", name="Inches", commands=commands)
    assert (
        abs(
            call("cam_simulate_toolpath", operation_name=created["name"])[
                "cutting_length"
            ]
            - 50.8
        )
        < 1e-8
    )
    created = call(
        "cam_create_custom_path",
        name="Cycle",
        commands=[{"name": "G83", "parameters": {"X": 1, "Z": -1, "R": 2, "Q": 1}}],
    )
    result = call("cam_simulate_toolpath", operation_name=created["name"])
    assert not result["statistics_complete"] and result["cutting_length"] is None
    model = App.ActiveDocument.addObject("Part::Feature", "Model")
    model.Shape = Part.makeBox(10, 10, 4)
    call("cam_create_job", name="Job", model_names=["Model"])
    tc = App.ActiveDocument.Job.Tools.Group[0]
    op = call(
        "cam_add_operation",
        job_name="Job",
        operation="Profile",
        tool_controller_name=tc.Name,
    )
    result = call(
        "cam_remove_tool_controller", expect_success=False, tool_controller_name=tc.Name
    )
    assert "referenced by operations" in result["error"]
    assert App.ActiveDocument.getObject(tc.Name)
    call(
        "cam_reorder_operations",
        expect_success=False,
        job_name="Job",
        operation_names=[op["name"], op["name"]],
    )
    assert len(App.ActiveDocument.Job.Operations.Group) == 1
    operation = App.ActiveDocument.getObject(op["name"])
    original = operation.StepDown.Value
    before = {o.Name for o in App.ActiveDocument.Objects}
    call(
        "cam_add_operation",
        expect_success=False,
        job_name="Job",
        operation="Profile",
        parameters={"DoesNotExist": 1},
    )
    assert before == {o.Name for o in App.ActiveDocument.Objects}
    call(
        "cam_set_operation_parameters",
        expect_success=False,
        operation_name=op["name"],
        parameters={"StepDown": "2 mm", "Side": "invalid"},
    )
    assert operation.StepDown.Value == original
    call(
        "cam_set_operation_parameters",
        expect_success=False,
        operation_name=op["name"],
        parameters={"StepDown": "2 mm", "ExtraOffset": "not a quantity"},
    )
    assert operation.StepDown.Value == original
    assert App.ActiveDocument.UndoMode == 0
    # An existing interactive edit must never be committed/aborted by MCP.
    document = App.ActiveDocument
    document.UndoMode = 1
    document.openTransaction("User edit")
    document.addObject("Part::Box", "UserBox")
    call(
        "cam_create_custom_path",
        expect_success=False,
        name="Blocked",
        commands=commands,
    )
    assert document.HasPendingTransaction and document.getObject("UserBox")
    assert document.getObject("Blocked") is None
    document.abortTransaction()
    document.UndoMode = 0
    return {
        "linear_lengths": [5, 12],
        "arc_length": math.pi / 2,
        "inch_incremental_length": 50.8,
        "unsupported_cycle_explicit": True,
    }


def cam_stock_simulation(root, output):
    import FreeCAD as App
    import Part

    App.newDocument("CamStock")
    call = tools_for(root, "cam")
    model = App.ActiveDocument.addObject("Part::Feature", "Model")
    model.Shape = Part.makeBox(6, 6, 3)
    call(
        "cam_create_job",
        name="Job",
        model_names=["Model"],
        stock_type="Box",
        stock_params={"Length": 10, "Width": 10, "Height": 5},
    )
    job = App.ActiveDocument.Job
    tc = job.Tools.Group[0]
    tc.Tool.Diameter = "2 mm"
    # Explicit toolpath cuts a slot through the stock, independent of defaults.
    bb = job.Stock.Shape.BoundBox
    y = bb.Center.y
    z = bb.ZMin + 1
    created = call(
        "cam_create_custom_path",
        name="SlotPath",
        job_name="Job",
        commands=[
            {"name": "G0", "parameters": {"X": bb.XMin + 2, "Y": y, "Z": bb.ZMax + 2}},
            {"name": "G1", "parameters": {"Z": z}},
            {"name": "G1", "parameters": {"X": bb.XMax - 2}},
            {"name": "G0", "parameters": {"Z": bb.ZMax + 2}},
        ],
    )
    operation = App.ActiveDocument.getObject(created["name"])
    operation.addProperty("App::PropertyLink", "ToolController")
    operation.ToolController = tc
    result = call(
        "cam_simulate_stock", job_name="Job", resolution=0.5, include_mesh=True
    )
    assert result["available"] and result["removed_mesh_volume"] > 0, result
    assert 0 < result["remaining_mesh_volume"] < result["initial_mesh_volume"]
    import Path as NativePath

    original_path = operation.Path
    for plane, axes, centers in (("G17", "XYZ", "IJ"), ("G18", "ZXY", "KI"), ("G19", "YZX", "JK")):
        start = {"X": bb.Center.x, "Y": bb.Center.y, "Z": bb.ZMin + 2}
        start[axes[0]] += 0.8
        end = dict(start)
        end[axes[2]] += 0.2
        end.update({centers[0]: -0.8, centers[1]: 0})
        operation.Path = NativePath.Path([
            NativePath.Command(plane), NativePath.Command("G0", start),
            NativePath.Command("G2", end),
        ])
        arc_result = call("cam_simulate_stock", job_name="Job", resolution=0.5)
        assert arc_result["available"] and arc_result["removed_mesh_volume"] > 0, arc_result
        assert 0 < arc_result["remaining_mesh_volume"] < arc_result["initial_mesh_volume"]
    operation.Path = original_path
    assert (
        len(result["mesh"]["triangles"]) == result["facets"]
        and result["mesh"]["vertices"]
    )
    assert operation.Path.Commands[0].Name == "G0"
    return {k: v for k, v in result.items() if k != "mesh"}


def assembly_external_links(root, output):
    import FreeCAD as App

    call = tools_for(root, "assembly")
    source = App.newDocument("External")
    source.addObject("Part::Box", "Box")
    call('assembly_create',name='SourceAsm')
    for name in ('Base','Moving'):
        call('assembly_insert_component',assembly_name='SourceAsm',source_name='Box',instance_name=name)
    call('assembly_add_constraint',assembly_name='SourceAsm',constraint_type='Slider',first='Base',second='Moving',solve=False)
    source.recompute()
    filename = str(Path(output) / "component.FCStd")
    source.saveAs(filename)
    App.closeDocument(source.Name)
    document = App.newDocument("Main")
    call("assembly_create", name="Asm")
    count = len(App.listDocuments())
    call(
        "assembly_insert_external_component",
        expect_success=False,
        assembly_name="Asm",
        file_path=filename,
        source_name="Box",
    )
    assert App.ActiveDocument is document and len(App.listDocuments()) == count
    document.saveAs(str(Path(output) / "main.FCStd"))
    call(
        "assembly_insert_external_component",
        expect_success=False,
        assembly_name="Asm",
        file_path=filename,
        source_name="Missing",
    )
    assert App.ActiveDocument is document and len(App.listDocuments()) == count
    result = call(
        "assembly_insert_external_component",
        assembly_name="Asm",
        file_path=filename,
        source_name="Box",
    )
    assert App.ActiveDocument is document
    link = document.getObject(result["instance"])
    assert link.LinkedObject.Document.Name == result["source_document"]
    assert link.Shape.Volume > 0
    main_path=str(Path(output)/'main.FCStd')
    instance_name=link.Name
    volume=link.Shape.Volume
    link.Placement.Base=App.Vector(12,34,56)
    document.recompute();document.save()
    for name in list(App.listDocuments()):App.closeDocument(name)
    document=App.openDocument(main_path);App.setActiveDocument(document.Name);document.recompute()
    reopened=document.getObject(instance_name)
    assert reopened.LinkedObject and abs(reopened.Shape.Volume-volume)<1e-7
    assert reopened.Placement.Base==App.Vector(12,34,56)
    assert Path(reopened.LinkedObject.Document.FileName).resolve()==Path(filename).resolve()
    external_document=reopened.LinkedObject.Document
    call('assembly_remove_component',assembly_name='Asm',component_name=instance_name)
    assert document.getObject(instance_name) is None and external_document.getObject('Box')
    external=call('assembly_insert_external_component',assembly_name='Asm',file_path=filename,source_name='SourceAsm',instance_name='ExternalAssembly')
    assert external['type']=='Assembly::AssemblyLink'
    flexible=call('assembly_set_component_rigid',instance_name=external['instance'],rigid=False)
    components={row['source']['object']:document.getObject(row['name']) for row in flexible['components'] if row['source']}
    call('assembly_ground_component',assembly_name='Asm',component_name=components['Base'].Name)
    components['Moving'].Placement.Base=App.Vector(8,9,13)
    solved=call('assembly_solve',assembly_name='Asm')
    assert solved['solved'],solved
    assert (components['Moving'].Placement.Base-App.Vector(0,0,13)).Length<1e-7
    child_name=components['Moving'].Name
    if App.GuiUp:
        simulation=call('assembly_create_simulation',assembly_name='Asm',end=1,step=.1)
        copied=flexible['joint_copies'][0]['name']
        motion=call('assembly_add_motion',simulation_name=simulation['name'],joint_name=copied,motion_type='Linear',formula='13+2*time')
        bound_source=document.getObject(motion['name']).MCPSourceJoint
        assert bound_source.Document is not document and Path(bound_source.Document.FileName).resolve()==Path(filename).resolve(), (bound_source.Document.Name,bound_source.Name,bound_source.Document.FileName,filename)
    document.save()
    for name in list(App.listDocuments()):App.closeDocument(name)
    document=App.openDocument(main_path);App.setActiveDocument(document.Name);document.recompute()
    assert call('assembly_solve',assembly_name='Asm')['solved']
    assert abs(document.getObject(child_name).Placement.Base.z-13)<1e-7
    if App.GuiUp:
        bound=document.getObject(motion['name'])
        assert Path(bound.MCPSourceJoint.Document.FileName).resolve()==Path(filename).resolve()
        result=call('assembly_run_simulation',simulation_name=simulation['name'])
        call('assembly_get_frame',assembly_name='Asm',frame=result['frames']-1)
        assert abs(document.getObject(child_name).Placement.Base.z-15)<1e-7
        call('assembly_remove_simulation',simulation_name=simulation['name'])
        assert document.getObject(copied)
    return {
        "active_document_restored": True,
        "external_link_valid": True,
        "invalid_source_cleaned": True,
        "external_link_saved_reopened":True,
        "source_preserved_on_removal":True,
    }


def cam_library_workflow(root, output):
    import FreeCAD as App
    import Part
    from Path.Tool.camassets import user_asset_store, cam_assets
    from Path.Tool.toolbit import ToolBit
    user_asset_store.set_dir(Path(output)/'assets')
    App.newDocument('Libraries')
    call=tools_for(root,'cam')
    bit=ToolBit.from_shape_id('endmill.fcstd');bit.set_id('six-mm');tool=bit.attach_to_doc(doc=App.ActiveDocument);tool.Diameter='6 mm';tool.Length='25 mm'
    result=call('cam_create_tool_library',label='Test tools',library_id='test-tools',tools=[{'number':7,'tool_object':tool.Name}])
    assert result['tools'][0]['number']==7 and result['tools'][0]['id']=='six-mm'
    assert cam_assets.exists('toolbitlibrary://test-tools',store='local')
    inspect=call('cam_inspect_tool_library',library_id='test-tools')
    assert App.Units.Quantity(inspect['tools'][0]['definition']['parameter']['Diameter']).Value==6,inspect
    path=str(Path(output)/'export'/'tools.fctl')
    exported=call('cam_export_tool_library',library_id='test-tools',file_path=path)
    assert all(Path(p).is_file() for p in exported['files']) and len(exported['files'])>=3
    call('cam_export_tool_library',library_id='test-tools',file_path=str(Path(output)/'tools.json'),format='camotics')
    call('cam_export_tool_library',library_id='test-tools',file_path=str(Path(output)/'tools.tbl'),format='linuxcnc')
    assert 'T7 ' in (Path(output)/'tools.tbl').read_text()
    # Import into a different empty store so cached local assets cannot hide missing dependencies.
    user_asset_store.set_dir(Path(output)/'imported')
    loaded=call('cam_import_tool_library',file_path=path,library_id='imported')
    assert loaded['tool_count']==1 and loaded['tools'][0]['number']==7
    camotics=call('cam_import_tool_library',file_path=str(Path(output)/'tools.json'),library_id='camotics')
    assert camotics['tools'][0]['number']==7
    model=App.ActiveDocument.addObject('Part::Feature','Model');model.Shape=Part.makeBox(20,20,10)
    call('cam_create_job',name='Job',model_names=['Model'])
    tc=call('cam_add_library_tool_to_job',library_id='imported',tool_number=7,job_name='Job')
    assert App.ActiveDocument.getObject(tc['tool']).Diameter.Value==6
    call('cam_add_library_tool_to_job',expect_success=False,library_id='imported',tool_number=7,job_name='Job')
    edited=call('cam_edit_tool_library',library_id='imported',label='Renamed',remove_numbers=[7],tools=[{'number':9,'tool_id':'six-mm'}])
    assert edited['label']=='Renamed' and edited['tools'][0]['number']==9
    snapshot=cam_assets.get_raw('toolbitlibrary://imported')
    call('cam_edit_tool_library',expect_success=False,library_id='imported',tools=[{'number':10,'tool_id':'six-mm'}])
    assert cam_assets.get_raw('toolbitlibrary://imported')==snapshot
    call('cam_delete_tool_library',library_id='imported')
    assert not cam_assets.exists('toolbitlibrary://imported',store='local')
    assert cam_assets.exists('toolbit://six-mm',store='local')
    # A non-builtin shape ID must migrate as a real FCStd dependency.
    shape_data=cam_assets.get_raw('toolbitshape://endmill',store='builtin')
    cam_assets.add_raw('toolbitshape','custom-profile',shape_data,store='local')
    custom=ToolBit.from_dict({'id':'custom-bit','shape':'custom-profile.fcstd','shape-type':'Endmill','parameter':{'Diameter':'4 mm','Length':'32 mm'}})
    custom_tool=custom.attach_to_doc(doc=App.ActiveDocument)
    call('cam_create_tool_library',library_id='custom-library',label='Custom',tools=[{'number':3,'tool_object':custom_tool.Name}])
    custom_path=str(Path(output)/'custom.fctl')
    call('cam_export_tool_library',library_id='custom-library',file_path=custom_path)
    user_asset_store.set_dir(Path(output)/'custom-import')
    imported=call('cam_import_tool_library',library_id='custom-import',file_path=custom_path)
    assert imported['tools'][0]['definition']['shape']=='custom-profile.fcstd'
    assert cam_assets.exists('toolbitshape://custom-profile',store='local')
    assert cam_assets.get_raw('toolbitshape://custom-profile')==shape_data
    # Failure after staging custom shapes must restore the previously empty store.
    user_asset_store.set_dir(Path(output)/'failed-import')
    original_manifest=Path(custom_path).read_text()
    data=json.loads(original_manifest)
    data['tools'].append({'nr':4,'path':'missing.fctb'})
    Path(custom_path).write_text(json.dumps(data))
    call('cam_import_tool_library',expect_success=False,file_path=custom_path,library_id='invalid')
    assert not cam_assets.list_assets(store='local')
    Path(custom_path).write_text(original_manifest)
    imperial=Path(output)/'imperial.json'
    imperial.write_text(json.dumps({'2':{'shape':'Cylindrical','units':'imperial','diameter':.25,'length':1.0}}))
    inch=call('cam_import_tool_library',file_path=str(imperial),library_id='inch')
    assert App.Units.Quantity(inch['tools'][0]['definition']['parameter']['Diameter']).Value==6.35
    return {'library_crud':True,'fctl_roundtrip':True,'camotics_roundtrip':True,'linuxcnc_export':True,'job_attachment':True}


def cam_machine_workflow(root, output):
    """Numeric asset persistence and nonvacuous controller-limit validation."""
    import math
    import FreeCAD as App
    import Part
    from Path.Tool.camassets import cam_assets, user_asset_store

    user_asset_store.set_dir(Path(output) / 'machines')
    doc = App.newDocument('Machines')
    call = tools_for(root, 'cam')
    limits = {'max_power': 2, 'min_rpm': 3000, 'max_rpm': 24000,
              'max_torque': 5, 'peak_torque_rpm': 8000, 'min_feed': 10, 'max_feed': 3000}
    created = call('cam_create_machine', label='Router', machine_id='router', parameters=limits)
    expected = {'max_power_w': 2000, 'min_rpm': 3000, 'max_rpm': 24000,
                'max_torque_nm': 5, 'peak_torque_rpm': 8000,
                'min_feed_mm_min': 10, 'max_feed_mm_min': 3000}
    loaded = call('cam_inspect_machine', machine_id='router')
    for key, value in expected.items():
        assert math.isclose(created[key], value, rel_tol=1e-12), (key, created)
        assert math.isclose(loaded[key], value, rel_tol=1e-12), (key, loaded)
    raw = cam_assets.get_raw('machine://router', store='local')
    payload = json.loads(raw)
    assert payload['max_power'] == 2000 and payload['max_rpm'] == 400
    assert payload['max_feed'] == 3000 and payload['max_torque'] == 5
    curve = call('cam_evaluate_machine', machine_id='router', rpm=12000)
    assert curve['within_limits'] and math.isclose(curve['torque_nm'], 2000 * 9.5488 / 12000)
    assert not call('cam_evaluate_machine', machine_id='router', rpm=25000)['within_limits']
    assert call('cam_evaluate_machine', machine_id='router', rpm=0)['torque_nm'] == 0
    call('cam_create_machine', expect_success=False, machine_id='router', parameters={'max_power': 9})
    assert cam_assets.get_raw('machine://router', store='local') == raw
    for value in [-1, '2 kW', True]:
        call('cam_create_machine', expect_success=False, machine_id='bad', parameters={'max_power': value})
        assert not cam_assets.exists('machine://bad', store='local')
    call('cam_create_machine', expect_success=False, machine_id='bad',
         parameters={'min_rpm': 30000, 'max_rpm': 24000})
    model = doc.addObject('Part::Feature', 'Model')
    model.Shape = Part.makeBox(20, 20, 10)
    call('cam_create_job', name='Job', model_names=['Model'])
    controller = doc.Job.Tools.Group[0]
    controller.SpindleSpeed = 12000
    controller.HorizFeed = '1000 mm/min'
    controller.VertFeed = '100 mm/min'
    report = call('cam_validate_job_machine', job_name='Job', machine_id='router')
    assert report['valid'] and math.isclose(report['controllers'][0]['horizontal_feed_mm_min'], 1000), report
    controller.HorizFeed = '3001 mm/min'
    controller.SpindleSpeed = 25000
    failed = call('cam_validate_job_machine', job_name='Job', machine_id='router')
    assert not failed['valid'] and len(failed['controllers'][0]['issues']) == 2
    controller.HorizFeed = '0 mm/min'
    assert not call('cam_validate_job_machine', job_name='Job', machine_id='router')['valid']
    assert not report['machine_job_binding'] and not report['collision_check_performed']
    edited=call('cam_edit_machine',machine_id='router',label='Updated router',parameters={'max_power':3,'max_rpm':30000,'max_feed':5000})
    assert edited['max_power_w']==3000 and edited['max_rpm']==30000 and edited['max_feed_mm_min']==5000,edited
    for key in ('min_rpm','peak_torque_rpm','max_torque_nm','min_feed_mm_min'):
        assert math.isclose(edited[key],expected[key]),(key,edited)
    edited_raw=cam_assets.get_raw('machine://router',store='local')
    for values in ({'max_rpm':1000},{'min_feed':6000},{'peak_torque_rpm':31000},{'max_power':True},{'axes':5}):
        call('cam_edit_machine',machine_id='router',parameters=values,expect_success=False)
        assert cam_assets.get_raw('machine://router',store='local')==edited_raw
    fcm=Path(output)/'router.fcm'
    exported=call('cam_export_machine',machine_id='router',file_path=str(fcm))
    payload=json.loads(fcm.read_text())
    assert payload['version']==1 and payload['max_power']==3000 and payload['max_rpm']==500,exported
    assert payload['max_feed']==5000
    call('cam_export_machine',machine_id='router',file_path=str(fcm),expect_success=False)
    call('cam_import_machine',file_path=str(fcm),expect_success=False)
    imported=call('cam_import_machine',file_path=str(fcm),machine_id='copy',label='Copy router')
    for key in expected:assert math.isclose(imported[key],edited[key]),(key,imported)
    assert imported['label']=='Copy router' and imported['id']=='copy'
    original_copy=cam_assets.get_raw('machine://copy',store='local')
    bad=Path(output)/'bad-machine.fcm'
    for field,value in [('version',2),('max_rpm',1),('max_power','3 kW'),('unknown_field',9)]:
        invalid=dict(payload);invalid[field]=value;bad.write_text(json.dumps(invalid))
        call('cam_import_machine',file_path=str(bad),machine_id='copy',overwrite=True,expect_success=False)
        assert cam_assets.get_raw('machine://copy',store='local')==original_copy
    call('cam_edit_machine',machine_id='copy',parameters={'max_power':4})
    replaced=call('cam_import_machine',file_path=str(fcm),machine_id='copy',overwrite=True)
    assert replaced['max_power_w']==3000
    call('cam_delete_machine',machine_id='copy')
    call('cam_delete_machine', machine_id='router')
    assert not cam_assets.exists('machine://router', store='local')
    return {'numeric_roundtrip': expected, 'torque_nm': curve['torque_nm'],
            'feed_and_spindle_violations_detected': True, 'duplicate_preserved': True}


def cam_gui_dressups(root, output):
    import FreeCAD as App
    import Part
    App.newDocument('Dressups')
    call=tools_for(root,'cam')
    model=App.ActiveDocument.addObject('Part::Feature','Model');model.Shape=Part.makeBox(10,10,4)
    call('cam_create_job',name='Job',model_names=['Model'])
    probe=Path(output)/'probe.xyz'
    probe.write_text(''.join(f'{x} {y} 1.0\n' for y in (-20,0,20) for x in (-20,0,20)))
    cases={'AxisMap':{'Radius':10.0,'AxisMap':'Y->A'},'ZCorrect':{'probefile':str(probe)},
           'RampEntry':{'Angle':30.0},'LeadInOut':{},'Dragknife':{'offset':.25,'pivotheight':.1},'LegacyDogbone':{}}
    results={}
    for kind,parameters in cases.items():
        op=call('cam_add_operation',job_name='Job',operation='Profile')
        base=App.ActiveDocument.getObject(op['name']);before=base.Path.toGCode()
        dressed=call('cam_add_dressup',dressup_name=kind+'Dressup',dressup=kind,base_operation=base.Name,parameters=parameters)
        obj=App.ActiveDocument.getObject(dressed['name'])
        assert obj.Base is base and obj in App.ActiveDocument.Job.Operations.Group and base not in App.ActiveDocument.Job.Operations.Group,(kind,obj.Base.Name,[o.Name for o in App.ActiveDocument.Job.Operations.Group])
        assert obj.Path.Commands and base.Path.toGCode()==before
        if kind=='AxisMap':assert any('A' in cmd.Parameters for cmd in obj.Path.Commands)
        if kind=='ZCorrect':
            assert obj.interpSurface.isValid()
            before_z=[cmd.Parameters['Z'] for cmd in base.Path.Commands if 'Z' in cmd.Parameters]
            after_z=[cmd.Parameters['Z'] for cmd in obj.Path.Commands if 'Z' in cmd.Parameters]
            assert min(after_z)>min(before_z)+.9
        if kind in ('RampEntry','LeadInOut','Dragknife'):assert obj.Path.toGCode()!=before,kind
        results[kind]={'commands':len(obj.Path.Commands),'native_proxy':type(obj.Proxy).__name__}
    filename=str(Path(output)/'dressups.FCStd');App.ActiveDocument.saveAs(filename)
    App.closeDocument(App.ActiveDocument.Name);App.openDocument(filename);App.ActiveDocument.recompute()
    for kind in cases:
        obj=App.ActiveDocument.getObject(kind+'Dressup')
        assert obj.Base and obj.Path.Commands
    return results


def cam_operation_families(root, output):
    from cam_operation_cases import operation_families
    return operation_families(root, output)


def assembly_exchange(root, output):
    from assembly_exchange_cases import run

    return run(root, output)


def assembly_motion_bindings(root, output):
    from assembly_motion_binding_cases import run

    return run(root, output)


def cam_canned_cycles(root, output):
    from cam_cycle_cases import run

    return run(root, output)


def surface_face_replacement(root, output):
    from surface_topology_cases import run

    return run(root, output)
