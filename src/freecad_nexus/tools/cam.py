"""Native FreeCAD CAM/Path workbench tools.

The functions in this module deliberately create the real ``Path`` objects
provided by FreeCAD.  They do not emulate toolpaths: operation creation,
recomputation and post processing are delegated to the installed workbench.
"""
from collections.abc import Awaitable, Callable
import textwrap
from pathlib import Path
from typing import Any

_OPERATIONS = {
    "profile": "Profile", "pocket": "Pocket", "drilling": "Drilling",
    "adaptive": "Adaptive", "helix": "Helix", "engrave": "Engrave",
    "surface": "Surface", "deburr": "Deburr", "probe": "Probe",
    "slot": "Slot", "tapping": "Tapping", "threadmilling": "ThreadMilling",
    "vcarve": "Vcarve", "waterline": "Waterline", "millface": "MillFace",
    "pocketshape": "PocketShape", "custom": "Custom",
}

# Send the shared native runtime with commands; it need not be installed in
# FreeCAD's Python environment separately from the MCP server.
_DRESSUP_RUNTIME = Path(__file__).with_name('_scripts').joinpath('cam_dressups.py').read_text(encoding='utf-8')
_LIBRARY_RUNTIME = Path(__file__).with_name('_scripts').joinpath('cam_libraries.py').read_text(encoding='utf-8')
_MACHINE_RUNTIME = Path(__file__).with_name('_scripts').joinpath('cam_machines.py').read_text(encoding='utf-8')
_DISCOVERY_RUNTIME = Path(__file__).with_name('_scripts').joinpath('cam_discovery.py').read_text(encoding='utf-8')
_PROPERTY_RUNTIME = Path(__file__).with_name('_scripts').joinpath('cam_properties.py').read_text(encoding='utf-8')
_CONTROLLER_RUNTIME = Path(__file__).with_name('_scripts').joinpath('cam_controllers.py').read_text(encoding='utf-8')
_OPERATION_RUNTIME = Path(__file__).with_name('_scripts').joinpath('cam_operations.py').read_text(encoding='utf-8')
_PATH_RUNTIME = Path(__file__).with_name('_scripts').joinpath('cam_cycles.py').read_text(encoding='utf-8') + "\n" + Path(__file__).with_name('_scripts').joinpath('cam_paths.py').read_text(encoding='utf-8')


_JOB_RUNTIME = _PROPERTY_RUNTIME + _OPERATION_RUNTIME + _CONTROLLER_RUNTIME + Path(__file__).with_name("_scripts").joinpath("cam_jobs.py").read_text(encoding="utf-8")
_STOCK_RUNTIME = Path(__file__).with_name("_scripts").joinpath("cam_stock.py").read_text(encoding="utf-8")
_SETUP_RUNTIME = _JOB_RUNTIME + _STOCK_RUNTIME + Path(__file__).with_name("_scripts").joinpath("cam_setup.py").read_text(encoding="utf-8")
_MODEL_RUNTIME = _SETUP_RUNTIME + Path(__file__).with_name("_scripts").joinpath("cam_models.py").read_text(encoding="utf-8")
_POST_RUNTIME = Path(__file__).with_name("_scripts").joinpath("cam_postprocess.py").read_text(encoding="utf-8")

_REPORT_RUNTIME = Path(__file__).with_name("_scripts").joinpath("cam_reports.py").read_text(encoding="utf-8")

_TRANSACTION_RUNTIME = Path(__file__).with_name('_scripts').joinpath('transaction.py').read_text(encoding='utf-8')

def register_cam_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    async def run(code: str) -> dict[str, Any]:
        wrapped = _TRANSACTION_RUNTIME + "\nwith mcp_transaction('CAM MCP operation'):\n" + textwrap.indent(code, "    ")
        result = await (await get_bridge()).execute_python(wrapped)
        return {"success": result.success, "result": result.result,
                "stdout": result.stdout, "stderr": result.stderr,
                "error": result.error_traceback}

    @mcp.tool()
    async def cam_create_job(
        name: str = "Job", model_names: list[str] | None = None,
        stock_type: str = "Automatic", stock_params: dict[str, Any] | None = None,
        stock_object_name: str = "", stock_placement: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a native CAM Job with validated models and stock.

        Models must be valid top-level geometry; omitted names discover eligible
        geometry excluding CAM resources and tooling. Stock types: Automatic
        (native preference default), FromBase (ExtXneg/ExtXpos/etc allowances),
        Box (Length/Width/Height), Cylinder (Radius/Height), Existing (clone
        stock_object_name solid). Numeric dimensions use mm; quantity strings
        accepted. Placement uses base [x,y,z] and quaternion rotation [x,y,z,w].
        Unknown/missing models and invalid dimensions roll back the whole Job.
        """
        return await run(_JOB_RUNTIME + _STOCK_RUNTIME + f"\n_result_ = create_job({name!r}, {model_names or []!r}, {stock_type!r}, {stock_params or {}!r}, {stock_object_name!r}, {stock_placement!r})")

    @mcp.tool()
    async def cam_set_stock(
        job_name: str, stock_type: str = "Box", dimensions: dict[str, Any] | None = None,
        stock_object_name: str = "", placement: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Configure native stock, preserving omitted dimensions on same-type edits.

        Supports Box, Cylinder, FromBase allowances and Existing solid clones.
        Dimensions are mm numbers or explicit quantities. placement accepts
        base and quaternion rotation. Unreferenced replaced stock is removed;
        independently referenced stock is retained and reported. Invalid
        dimensions, silent native clamping and recompute errors roll back.
        """
        return await run(_JOB_RUNTIME + _STOCK_RUNTIME + f"\n_result_ = configure_stock(operation_job({job_name!r}), {stock_type!r}, {dimensions or {}!r}, {stock_object_name!r}, {placement!r})")

    @mcp.tool()
    async def cam_add_tool_controller(job_name: str, tool_number: int = 1,
                                      spindle_speed: float = 1000, horizontal_feed: float = 100,
                                      vertical_feed: float = 50, tool: dict[str, Any] | None = None,
                                      controller_name: str = "", create_new: bool = False,
                                      tool_asset: str = "") -> dict[str, Any]:
        """Create/update a native controller using RPM and mm/min numeric inputs.

        By default updates the first Job controller; create_new adds one with
        a unique tool number. tool_asset names a native shape, e.g. probe.fcstd,
        drill.fcstd, tap.fcstd or endmill.fcstd. tool supplies ToolBit properties
        such as Diameter or Pitch, accepting explicit quantities like '1 mm'.
        Unknown properties and failed recomputes roll back the entire change.
        """
        return await run(_PROPERTY_RUNTIME + _CONTROLLER_RUNTIME +
                         f"\n_result_=add_controller({job_name!r},{tool_number!r},{spindle_speed!r},"
                         f"{horizontal_feed!r},{vertical_feed!r},{tool or {}!r},{controller_name!r},"
                         f"{create_new!r},{tool_asset!r})")

    @mcp.tool()
    async def cam_set_tool_controller(tool_controller_name: str, tool_number: int | None = None,
                                      spindle_speed: float | None = None, horizontal_feed: float | None = None,
                                      vertical_feed: float | None = None, tool: dict[str, Any] | None = None) -> dict[str, Any]:
        """Update ToolController RPM/mm-min and ToolBit properties atomically."""
        return await run(_PROPERTY_RUNTIME + _CONTROLLER_RUNTIME +
                         f"\n_result_=set_controller({tool_controller_name!r},{tool_number!r},{spindle_speed!r},"
                         f"{horizontal_feed!r},{vertical_feed!r},{tool or {}!r})")

    @mcp.tool()
    async def cam_export_tool_controller(tool_controller_name: str) -> dict[str, Any]:
        """Inspect a controller and native ToolBit as JSON with explicit units."""
        return await run(_CONTROLLER_RUNTIME +
                         f"\n_result_=controller_data(native_controller({tool_controller_name!r}))")

    @mcp.tool()
    async def cam_get_operation_schema(operation: str, job_name: str = "",
                                       tool_controller_name: str = "") -> dict[str, Any]:
        """Inspect native properties using a compatible Job ToolController.

        No schema-probe object remains in the Job. When no controller is named,
        the first compatible controller in Job order is used.
        """
        op = operation.strip().lower()
        if op not in _OPERATIONS:
            raise ValueError(f"Unsupported CAM operation: {operation}")
        if not job_name:
            raise ValueError("job_name is required for a native operation schema")
        return await run(_OPERATION_RUNTIME +
                         f"\n_result_=operation_schema({job_name!r},{_OPERATIONS[op]!r},{tool_controller_name!r})")

    @mcp.tool()
    async def cam_add_operation(job_name: str, operation: str, base_object: str = "",
                                parameters: dict[str, Any] | None = None,
                                tool_controller_name: str = "", base_subelements: list[str] | None = None) -> dict[str, Any]:
        """Create and recompute a native Path operation with a compatible tool.

        A named controller must belong to the Job and support the operation.
        Otherwise the first compatible Job controller is chosen without a GUI
        prompt. path_ready requires active valid machining commands; comments
        alone do not establish a usable path. It does not verify collisions or
        machining correctness. Base geometry and scalar parameters are atomic.
        base_object resolves to a Job clone; a design source must have exactly
        one instance in this Job, so later setup transforms affect its path.
        """
        op = operation.strip().lower()
        if op not in _OPERATIONS:
            raise ValueError(f"Unsupported CAM operation: {operation}")
        return await run(_PROPERTY_RUNTIME + _OPERATION_RUNTIME +
                         f"\n_result_=add_operation({job_name!r},{_OPERATIONS[op]!r},{base_object!r},"
                         f"{base_subelements or []!r},{parameters or {}!r},{tool_controller_name!r})")

    @mcp.tool()
    async def cam_recompute_operation(operation_name: str) -> dict[str, Any]:
        """Recompute a native operation, rejecting invalid results and stale paths."""
        return await run(_PROPERTY_RUNTIME + _OPERATION_RUNTIME +
                         f"\n_result_=update_operation({operation_name!r},{{}},True)")

    @mcp.tool()
    async def cam_set_operation_parameters(operation_name: str, parameters: dict[str, Any], recompute: bool = True) -> dict[str, Any]:
        """Set parameters atomically, replacing expressions for specified fields.

        With recompute=False the previous path is explicitly marked stale.
        Vector properties accept three numeric coordinates in mm.
        """
        return await run(_PROPERTY_RUNTIME + _OPERATION_RUNTIME +
                         f"\n_result_=update_operation({operation_name!r},{parameters!r},{recompute!r})")

    @mcp.tool()
    async def cam_list_operations() -> dict[str, Any]:
        """List Path operations importable in the installed FreeCAD build."""
        return await run("""import importlib
items=[]
for key,modname in %r.items():
 try:
  mod=importlib.import_module('Path.Op.'+modname); items.append({'operation':modname,'module':'Path.Op.'+modname,'available':hasattr(mod,'Create')})
 except Exception as exc: items.append({'operation':modname,'module':'Path.Op.'+modname,'available':False,'error':str(exc)})
_result_={'operations':items,'available_count':sum(1 for x in items if x['available'])}""" % (_OPERATIONS,))

    @mcp.tool()
    async def cam_capabilities() -> dict[str, Any]:
        """Distinguish installed APIs from MCP creation and runtime availability."""
        return await run(_DISCOVERY_RUNTIME + """
mods={}
for mod in ('Path','PathSimulator','CAMSimulator','Path.Tool.camassets','Path.Post.Processor'):
 try:
  spec=importlib.util.find_spec(mod)
  mods[mod]={'installed':spec is not None,'file':spec.origin if spec else None}
 except (ImportError,ValueError,AttributeError) as exc: mods[mod]={'installed':False,'error':str(exc)}
items=dressup_catalog()
_result_={'gui_up':bool(App.GuiUp),'modules':mods,'operations':%r,
          'dressups':[d['dressup'] for d in items if d['mcp_creation_supported']],
          'installed_dressups':[d['dressup'] for d in items if d['installed']],
          'dressup_details':items,'stock_simulator':{'backend':'PathSimulator','gui_required':True}}
""" % (_OPERATIONS,))

    @mcp.tool()
    async def cam_simulate_toolpath(operation_name: str, include_commands: bool = False) -> dict[str, Any]:
        """Analyze XYZ motion and G81/G82/G85 drilling/boring cycles.

        Cycle expansion follows LinuxCNC RS274 (G98/G99, sticky depth/R,
        absolute/incremental repeats, three planes). Counts include expanded
        moves; cycle_dwell_seconds counts G82 dwell, not full machine time.
        Cancel cycles before changing units, plane or distance mode. Other
        cycles, rotary axes and compensation report incomplete statistics.
        """
        return await run(_PATH_RUNTIME + """
o=App.ActiveDocument.getObject(%r)
if not o or not o.isDerivedFrom('Path::Feature'): raise ValueError('Path operation not found')
if callable(getattr(getattr(o,'Proxy',None),'execute',None)): o.Proxy.execute(o)
App.ActiveDocument.recompute()
_result_=path_statistics(o.Path)
_result_.update({'name':o.Name,'cycle_time':str(getattr(o,'CycleTime','')),'estimated_time_seconds':None,
                'commands':[c.toGCode() for c in o.Path.Commands] if %r else None})
""" % (operation_name,include_commands))

    @mcp.tool()
    async def cam_simulate_stock(job_name: str, resolution: float = 0.5,
                                 include_mesh: bool = False) -> dict[str, Any]:
        """Run native voxel stock removal for supported XYZ toolpaths.

        Resolution is in mm. Returns measured mesh volume and optional mesh
        vertices/triangles. Requires GUI FreeCAD. Unsupported cycles or axes
        fail explicitly; this does not verify machine collisions or fixtures.
        G81/G82/G85 are expanded using LinuxCNC RS274 motion semantics.
        """
        import math
        if not math.isfinite(resolution) or resolution <= 0:
            raise ValueError('resolution must be finite and positive')
        return await run(_PATH_RUNTIME + """
j=App.ActiveDocument.getObject(%r)
if not j or not hasattr(j,'Tools'): raise ValueError('CAM Job not found')
_result_=simulate_stock(j,%r,%r)
""" % (job_name,resolution,include_mesh))

    @mcp.tool()
    async def cam_validate_job(job_name: str, recompute: bool = False) -> dict[str, Any]:
        """Check native models, stock, controllers, active paths and recompute state.

        Detects invalid/empty objects, duplicate tools, foreign controllers,
        empty paths and missing machining/probing motion. Does not establish
        collision clearance or correct machining. recompute refreshes paths.
        """
        return await run(_JOB_RUNTIME + f"\n_result_ = job_validation(operation_job({job_name!r}), {recompute!r})")

    @mcp.tool()
    async def cam_job_statistics(job_name: str) -> dict[str, Any]:
        """Aggregate active operation statistics and report unsupported motion."""
        return await run(_PATH_RUNTIME + """
j=App.ActiveDocument.getObject(%r)
if not j or not hasattr(j,'Operations'): raise ValueError('CAM Job not found')
items=[]; empty=[]
for o in j.Operations.Group:
 if not getattr(o,'Active',True): continue
 stats=path_statistics(o.Path); stats['name']=o.Name; items.append(stats)
 if not o.Path.Commands: empty.append(o.Name)
complete=all(s['statistics_complete'] for s in items)
_result_={'job':j.Name,'operation_count':len(j.Operations.Group),'active_operation_count':len(items),
          'command_count':sum(s['command_count'] for s in items),'path_length':sum(s['path_length'] for s in items) if complete else None,
          'rapid_length':sum(s['rapid_length'] for s in items) if complete else None,
          'cutting_length':sum(s['cutting_length'] for s in items) if complete else None,
          'statistics_complete':complete,'length_unit':'mm','operations':items,'empty_active_operations':empty,
          'cycle_time':str(getattr(j,'CycleTime',''))}
""" % job_name)

    @mcp.tool()
    async def cam_validate_operation_parameters(operation_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Validate parameter names and values against an existing Path operation."""
        return await run("""o=App.ActiveDocument.getObject(%r)
if not o: raise ValueError('Operation not found')
valid=[]; rejected=[]; errors=[]
for key,val in %r.items():
 if not hasattr(o,key): rejected.append(key); continue
 try:
  cur=getattr(o,key)
  if isinstance(cur,(int,float)) and not isinstance(cur,bool): float(val)
  elif isinstance(cur,bool) and not isinstance(val,bool): raise TypeError('expected boolean')
  elif o.getTypeIdOfProperty(key)=='App::PropertyEnumeration' and str(val) not in o.getEnumerationsOfProperty(key): raise ValueError('value is not an allowed enumeration')
  valid.append({'name':key,'type':o.getTypeIdOfProperty(key),'current':str(cur)})
 except Exception as exc: errors.append({'name':key,'error':str(exc)})
_result_={'operation':o.Name,'valid':not rejected and not errors,'accepted':valid,'rejected':rejected,'errors':errors}""" % (operation_name, parameters))

    @mcp.tool()
    async def cam_generate_gcode(
        job_name: str, file_path: str = "", post_processor: str = "",
        post_processor_args: str | None = None, overwrite: bool = False,
        include_gcode: bool = False,
    ) -> dict[str, Any]:
        """Postprocess a validated Job, preserving native output sections/files.

        file_path is a native FreeCAD output filename/template, or omitted to
        use the Job setting. SplitOutput and OrderOutputBy follow the Job.
        Supports native %j/%d/%T/%t/%W/%O/%S substitutions. Every section gets
        its own file, never concatenated programs. Existing files require
        overwrite=True; all content is staged before publishing files.
        Returned path is set only for a single written file; inspect files
        for split outputs. None sections are skipped, empty strings remain
        explicit empty files as requested by native postprocessors. GUI
        editors are disabled where the postprocessor exposes that option.
        """
        return await run(_JOB_RUNTIME + _POST_RUNTIME + f"\n_result_ = post_gcode({job_name!r}, {file_path!r}, {post_processor!r}, {post_processor_args!r}, {overwrite!r}, {include_gcode!r})")

    @mcp.tool()
    async def cam_get_post_processor_info(job_name: str, post_processor: str = "") -> dict[str, Any]:
        """Inspect a native postprocessor's units, argument help and option schema."""
        return await run(_JOB_RUNTIME + _POST_RUNTIME + f"\n_result_ = postprocessor_info({job_name!r}, {post_processor!r})")

    @mcp.tool()
    async def cam_set_setup_sheet(job_name: str, parameters: dict[str, Any], recompute: bool = True) -> dict[str, Any]:
        """Edit native SetupSheet defaults atomically using cam_get_setup_sheet schema.

        Numeric rapid speeds use mm/min; lengths use mm. Explicit quantity
        strings are accepted. Linked expressions update on recompute;
        creation-default settings affect new operations. Unknown fields and
        failed recomputes roll back. Explicit values replace field expressions.
        """
        return await run(_JOB_RUNTIME + f"\n_result_ = edit_setup_sheet({job_name!r}, {parameters!r}, {recompute!r})")

    @mcp.tool()
    async def cam_generate_setup_report(
        job_name: str, file_path: str = "", include_images: bool = True,
        include_html: bool = False, overwrite: bool = False,
    ) -> dict[str, Any]:
        """Generate FreeCAD's native CAM Sanity setup report and structured data.

        Returns native tool/stock/run summaries, squawks and Job validation.
        file_path optionally writes self-contained HTML; include_html returns
        it inline. GUI images preserve visibility, selection and the active
        view; headless reports explicitly omit images. Existing output needs
        overwrite=True. No browser or editor opens. This is a setup summary,
        not a machine collision or machining correctness verification.
        """
        return await run(_JOB_RUNTIME + _POST_RUNTIME + _REPORT_RUNTIME + f"\n_result_ = generate_setup_report({job_name!r}, {file_path!r}, {include_images!r}, {include_html!r}, {overwrite!r})")

    @mcp.tool()
    async def cam_list_post_processors() -> dict[str, Any]:
        """List post processors available in this FreeCAD installation."""
        return await run("import Path; _result_={'available':Path.Preferences.allAvailablePostProcessors(),'enabled':Path.Preferences.allEnabledPostProcessors(),'default':Path.Preferences.defaultPostProcessor()}")

    @mcp.tool()
    async def cam_list_tool_library() -> dict[str, Any]:
        """List built-in and local CAM ToolBit assets from FreeCAD's asset manager."""
        return await run("""import Path.Tool.camassets as CA, asyncio
manager=CA.cam_assets
try: manager.setup()
except Exception: pass
diagnostics=[]; assets=[]
for store_name,store in manager.stores.items():
 try:
  found=asyncio.run(store.list_assets('toolbit'))
  assets.extend({'store':store_name,'asset':str(x)} for x in found)
 except Exception as exc: diagnostics.append({'store':store_name,'error':str(exc)})
_result_={'count':len(assets),'toolbits':assets,'stores':{k:str(v._base_dir) for k,v in manager.stores.items()},'diagnostics':diagnostics}""")

    @mcp.tool()
    async def cam_add_dressup(dressup_name: str, dressup: str, base_operation: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create a native dressup and replace the top-level base in its Job.

        Boundary, Array, Tags, DogboneII, RampEntry, LeadInOut, Dragknife,
        AxisMap, ZCorrect and LegacyDogbone are supported where installed.
        The last six require GUI FreeCAD. 'dogbone' retains the DogboneII
        alias. ZCorrect probefile paths refer to the FreeCAD host.
        """
        return await run(_DISCOVERY_RUNTIME + _PROPERTY_RUNTIME + _DRESSUP_RUNTIME + f"\n_result_=create_dressup({dressup_name!r},{dressup!r},{base_operation!r},{parameters or {}!r})")

    @mcp.tool()
    async def cam_set_operation_base(operation_name: str, base_object: str = "", subelements: list[str] | None = None) -> dict[str, Any]:
        """Set one validated Job model Base and regenerate nested/dressed paths.

        base_object selects a Job clone or a uniquely instanced design source.
        Empty base_object clears Base for automatic Job geometry. Subelements
        are one-based FaceN/EdgeN/VertexN. Use cam_set_operation_bases for several
        models. Invalid references and failed native recomputes roll back.
        """
        refs = subelements or []
        bases = [{"object": base_object, "subelements": refs}] if base_object else []
        return await run(_MODEL_RUNTIME + f"\nif {bool(refs) and not base_object!r}: raise ValueError('Subelements require a base object')\n_result_=set_operation_bases({operation_name!r},{bases!r})\n_result_.update(base=_result_['bases'][0]['object'] if _result_['bases'] else None,subelements={refs!r})")

    @mcp.tool()
    async def cam_set_operation_bases(operation_name: str, bases: list[dict[str, Any]]) -> dict[str, Any]:
        """Set multiple native Job model references for an operation.

        bases entries contain object (clone or uniquely instanced source) and
        subelements (one-based FaceN/EdgeN/VertexN list). Group each model in
        one entry; [] clears explicit Base. Regenerates the Job and dressups,
        reports actual path readiness, and rolls back native reference errors.
        """
        return await run(_MODEL_RUNTIME + f"\n_result_=set_operation_bases({operation_name!r},{bases!r})")

    @mcp.tool()
    async def cam_inspect_job(job_name: str) -> dict[str, Any]:
        """Inspect native Job structure, output configuration, tools and path status.

        ToolController quantities are JSON values with explicit RPM/mm-min.
        """
        return await run(_JOB_RUNTIME + f"\n_result_ = inspect_job({job_name!r})")

    @mcp.tool()
    async def cam_set_job_settings(
        job_name: str, post_processor: str | None = None,
        post_processor_args: str | None = None, output_file: str | None = None,
        split_output: bool | None = None, order_output_by: str | None = None,
    ) -> dict[str, Any]:
        """Edit native Job output fields atomically; None retains, empty strings clear.

        order_output_by accepts the installed native enum (Operation, Tool or
        Fixture). Discover postprocessor options with cam_get_post_processor_info.
        """
        changes = {key:value for key,value in [
            ('PostProcessor',post_processor),('PostProcessorArgs',post_processor_args),
            ('PostProcessorOutputFile',output_file),('SplitOutput',split_output),
            ('OrderOutputBy',order_output_by)] if value is not None}
        return await run(_JOB_RUNTIME + f"""
job = operation_job({job_name!r})
changes = {changes!r}
apply_parameters(job, changes)
job.Document.recompute()
_result_ = {{'job':job.Name,'post_processor':job.PostProcessor,'post_args':job.PostProcessorArgs,
 'output':job.PostProcessorOutputFile,'split_output':job.SplitOutput,'order_output_by':job.OrderOutputBy}}""")

    @mcp.tool()
    async def cam_set_job_property(job_name: str, property_name: str, value: Any) -> dict[str, Any]:
        """Set a validated writable scalar Job property with native quantity support."""
        return await run(_JOB_RUNTIME + f"""
job = operation_job({job_name!r})
if {property_name!r} in ('Fixtures',): raise ValueError('Use cam_set_fixture for work coordinate systems')
apply_parameters(job, {{{property_name!r}: {value!r}}})
job.Document.recompute()
_result_ = {{'job':job.Name,'property':{property_name!r},'value':cam_json_value(getattr(job,{property_name!r}))}}""")

    @mcp.tool()
    async def cam_list_dressups() -> dict[str, Any]:
        """List installed dressups, explicitly distinguishing MCP creation coverage."""
        return await run(_DISCOVERY_RUNTIME + """
items=dressup_catalog()
_result_={'dressups':items,'available_count':sum(d['available'] for d in items),'installed_count':sum(d['installed'] for d in items)}
""")

    @mcp.tool()
    async def cam_set_fixture(job_name: str, fixture_names: list[str]) -> dict[str, Any]:
        """Assign work coordinate system codes such as G54/G55 to a Job.

        FreeCAD's Fixtures property is a string list of WCS codes, not links
        to fixture geometry. Post processors determine supported WCS codes.
        """
        if not fixture_names or any(not n.strip() for n in fixture_names):
            raise ValueError('Provide at least one nonempty WCS code')
        if len(set(fixture_names)) != len(fixture_names):
            raise ValueError('WCS codes must be unique')
        return await run("""j=App.ActiveDocument.getObject(%r)
if not j: raise ValueError('Job not found')
if not hasattr(j,'Fixtures'): raise RuntimeError('This FreeCAD build has no Job Fixtures property')
j.Fixtures=%r; App.ActiveDocument.recompute(); _result_={'job':j.Name,'fixtures':list(j.Fixtures)}""" % (job_name, fixture_names))

    @mcp.tool()
    async def cam_get_setup_sheet(job_name: str) -> dict[str, Any]:
        """Inspect native SetupSheet values, units, property descriptions and enums."""
        return await run(_JOB_RUNTIME + f"\n_result_ = setup_sheet_data(operation_job({job_name!r}))")

    @mcp.tool()
    async def cam_set_operation_state(operation_name: str, active: bool = True) -> dict[str, Any]:
        """Activate or deactivate a native Path operation."""
        return await run("o=App.ActiveDocument.getObject(%r); o.Active=%r; App.ActiveDocument.recompute(); _result_={'name':o.Name,'active':o.Active}" % (operation_name, active))

    @mcp.tool()
    async def cam_get_toolpath(operation_name: str) -> dict[str, Any]:
        """Return native Path command text and toolpath statistics."""
        return await run("o=App.ActiveDocument.getObject(%r); p=getattr(o,'Path',None); cmds=getattr(p,'Commands',[]) if p else []; _result_={'name':o.Name,'command_count':len(cmds),'length':float(getattr(p,'Length',0.0)) if p else 0.0,'commands':[{'name':getattr(c,'Name',''),'parameters':dict(getattr(c,'Parameters',{})),'gcode':c.toGCode()} for c in cmds]}" % operation_name)

    @mcp.tool()
    async def cam_create_custom_path(name: str, commands: list[dict[str, Any]], job_name: str = "") -> dict[str, Any]:
        """Create a native Path::Feature from command dictionaries."""
        return await run("""import Path
data=%r
if not all(isinstance(c,dict) and isinstance(c.get('name'),str) and isinstance(c.get('parameters',{}),dict) for c in data): raise ValueError('Commands require name and parameters fields')
path=Path.Path([Path.Command(c['name'],c.get('parameters',{})) for c in data]); o=App.ActiveDocument.addObject('Path::Feature',%r); o.Path=path
if %r: App.ActiveDocument.getObject(%r).Proxy.addOperation(o)
App.ActiveDocument.recompute(); _result_={'name':o.Name,'commands':len(path.Commands),'length':float(path.Length),'job':%r or None}""" % (commands, name, bool(job_name), job_name, job_name))

    @mcp.tool()
    async def cam_remove_operation(operation_name: str) -> dict[str, Any]:
        """Remove a native Path operation and its dependent dressups."""
        return await run("""o=App.ActiveDocument.getObject(%r)
if not o or not o.isDerivedFrom('Path::Feature') or hasattr(o,'Tools') or hasattr(o,'ToolNumber'): raise ValueError('Path operation or custom path required')
name=o.Name; dependent=[]; visited={name}
def collect_dressups(base):
 for child in list(base.InList):
  if child.Name in visited or not child.isDerivedFrom('Path::Feature'): continue
  if getattr(child,'Base',None) is not base: continue
  visited.add(child.Name); collect_dressups(child); dependent.append(child.Name)
collect_dressups(o)
for n in dependent: App.ActiveDocument.removeObject(n)
App.ActiveDocument.removeObject(name); App.ActiveDocument.recompute(); _result_={'removed':name,'dependent_removed':dependent}""" % operation_name)

    @mcp.tool()
    async def cam_remove_tool_controller(tool_controller_name: str) -> dict[str, Any]:
        """Remove a ToolController from its Job and document."""
        return await run("""tc=App.ActiveDocument.getObject(%r)
if not tc or not hasattr(tc,'ToolNumber'): raise ValueError('ToolController not found')
users=[o.Name for o in tc.InList if getattr(o,'ToolController',None) is tc]
if users: raise ValueError('ToolController is referenced by operations: '+', '.join(users))
name=tc.Name
for j in [o for o in App.ActiveDocument.Objects if hasattr(o,'Tools') and tc in getattr(o.Tools,'Group',[])]:
 group=[item for item in j.Tools.Group if item.Name != tc.Name]
 j.Tools.Group=group
App.ActiveDocument.removeObject(name); App.ActiveDocument.recompute(); _result_={'removed':name}""" % tool_controller_name)

    @mcp.tool()
    async def cam_set_tool_controller_property(tool_controller_name: str, property_name: str, value: Any) -> dict[str, Any]:
        """Set an arbitrary writable native ToolController property."""
        return await run("""tc=App.ActiveDocument.getObject(%r)
if not tc: raise ValueError('ToolController not found')
key=%r; value=%r
if key not in tc.PropertiesList: raise ValueError('Unknown ToolController property: '+key)
if key in ('Proxy','Tool'): raise ValueError('Property is managed or unsafe through this tool')
old=getattr(tc,key)
if isinstance(old,bool): value=bool(value)
elif isinstance(old,(int,float)) and not isinstance(old,bool): value=type(old)(value)
setattr(tc,key,value); App.ActiveDocument.recompute(); _result_={'name':tc.Name,'property':key,'value':str(getattr(tc,key))}""" % (tool_controller_name,property_name,value))

    @mcp.tool()
    async def cam_reorder_operations(job_name: str, operation_names: list[str]) -> dict[str, Any]:
        """Reorder native Job operations using the Path operation group."""
        return await run("""j=App.ActiveDocument.getObject(%r)
if not j: raise ValueError('Job not found')
lookup={o.Name:o for o in j.Operations.Group}
if len(lookup) != len(%r) or set(lookup) != set(%r): raise ValueError('operation_names must contain every top-level Job operation exactly once')
j.Operations.Group=[lookup[n] for n in %r]; App.ActiveDocument.recompute(); _result_={'job':j.Name,'operations':[o.Name for o in j.Operations.Group]}""" % (job_name, operation_names, operation_names, operation_names))

    @mcp.tool()
    async def cam_inspect_setup(job_name: str) -> dict[str, Any]:
        """Inspect Job model clone names, design sources, placements and stock."""
        return await run(_SETUP_RUNTIME + f"\n_result_=setup_data(operation_job({job_name!r}))")

    @mcp.tool()
    async def cam_inspect_setup_reference(
        job_name: str, reference_object: str, subelement: str = "",
        point_mode: str = "auto", direction_mode: str = "auto",
        parameters: list[float] | None = None,
    ) -> dict[str, Any]:
        """Resolve a Job clone/stock FaceN, EdgeN or VertexN in document mm.

        Native subelements are one-based. Auto point uses circle/ellipse center
        or center of mass; alternatives: bounds_center, center_of_mass, parameter.
        parameters are normalized [edge_t, unused] or [face_u, face_v]. Auto
        direction uses plane normal, circle/cylinder/cone/torus axis or edge
        tangent; normal/tangent/axis/none may be selected explicitly. Curved
        face normals require normal mode. Trimmed-out UV and singularities fail.
        """
        return await run(_SETUP_RUNTIME + f"\n_result_=setup_reference(operation_job({job_name!r}),{reference_object!r},{subelement!r},{point_mode!r},{direction_mode!r},{parameters if parameters is not None else [0.5,0.5]!r})")

    @mcp.tool()
    async def cam_align_setup(
        job_name: str, reference_object: str, subelement: str,
        target_axis: list[float] | None = None, direction_mode: str = "auto",
        parameters: list[float] | None = None, rotation_center: list[float] | None = None,
        model_names: list[str] | None = None, stock_mode: str = "auto",
    ) -> dict[str, Any]:
        """Align a selected clone/stock direction with target_axis (default +Z).

        Applies the shortest rotation about rotation_center (default origin).
        Repeating an aligned request preserves orientation. References follow
        cam_inspect_setup_reference conventions. All Job clones move unless
        model_names selects a subset; stock_mode follows cam_transform_setup.
        The source direction is read before moving; design objects stay fixed.
        """
        return await run(_SETUP_RUNTIME + f"\n_result_=align_setup({job_name!r},{reference_object!r},{subelement!r},{target_axis if target_axis is not None else [0,0,1]!r},{direction_mode!r},{parameters if parameters is not None else [0.5,0.5]!r},{rotation_center if rotation_center is not None else [0,0,0]!r},{model_names!r},{stock_mode!r})")

    @mcp.tool()
    async def cam_set_setup_origin(
        job_name: str, reference_object: str = "", subelement: str = "",
        point: list[float] | None = None, target: list[float] | None = None,
        axes: str = "XYZ", point_mode: str = "auto", parameters: list[float] | None = None,
        model_names: list[str] | None = None, stock_mode: str = "auto",
    ) -> dict[str, Any]:
        """Translate setup so a geometry reference or explicit point reaches target.

        target defaults to [0,0,0] in document mm. axes selects XYZ, XY, Z etc.
        Geometry points follow cam_inspect_setup_reference; circle centers,
        vertices, face centers and explicit UV points are supported. Moves all
        clones unless model_names selects a subset; sources stay fixed and
        nested toolpaths regenerate. This changes model setup, not WCS labels.
        """
        return await run(_SETUP_RUNTIME + f"\n_result_=set_setup_origin({job_name!r},{reference_object!r},{subelement!r},{point!r},{target if target is not None else [0,0,0]!r},{axes!r},{point_mode!r},{parameters if parameters is not None else [0.5,0.5]!r},{model_names!r},{stock_mode!r})")

    @mcp.tool()
    async def cam_center_setup_in_stock(
        job_name: str, model_names: list[str] | None = None, axes: str = "XYZ",
    ) -> dict[str, Any]:
        """Center selected clones' combined bounding box inside fixed explicit stock.

        axes=XY keeps setup height; XYZ centers all directions. The selected
        models move together, retaining their relative placements. FromBase
        stock is rejected because it follows model bounds; stock stays fixed.
        """
        return await run(_SETUP_RUNTIME + f"\n_result_=center_setup_in_stock({job_name!r},{model_names!r},{axes!r})")

    @mcp.tool()
    async def cam_transform_setup(
        job_name: str, translation: list[float] | None = None,
        rotation_axis: list[float] | None = None, rotation_angle: float = 0,
        rotation_center: list[float] | None = None, model_names: list[str] | None = None,
        stock_mode: str = "auto",
    ) -> dict[str, Any]:
        """Transform native Job model clones and regenerate machining paths.

        Coordinates are document-space mm, rotation degrees about the supplied
        center; delta composes before existing placements. Omitted model_names
        selects all clones; inspect setup to find clone names. Design sources
        stay in place. stock_mode auto refits FromBase bounds, otherwise moves
        stock by the same delta; keep preserves placement; refit requires
        FromBase; transform rotates explicit stock (FromBase permits translation
        only). All nested operations are recomputed and native errors roll back.
        """
        return await run(_SETUP_RUNTIME + f"\n_result_=transform_setup({job_name!r},{model_names!r},{translation if translation is not None else [0,0,0]!r},{rotation_axis if rotation_axis is not None else [0,0,1]!r},{rotation_angle!r},{rotation_center if rotation_center is not None else [0,0,0]!r},{stock_mode!r})")

    @mcp.tool()
    async def cam_inspect_model_references(job_name: str, model_name: str) -> dict[str, Any]:
        """List a model clone's native references and required replacement mapping.

        job_name identifies the owning Job. Reports blocked external or
        expression dependencies and all referenced one-based FaceN/EdgeN/VertexN.
        """
        return await run(_MODEL_RUNTIME + f"\n_result_=model_replacement_info({job_name!r},{model_name!r})")

    @mcp.tool()
    async def cam_replace_job_model(
        job_name: str, model_name: str, source_name: str,
        subelement_map: dict[str, str] | None = None,
        placement_mode: str = "preserve_setup", refit_stock: bool = True,
    ) -> dict[str, Any]:
        """Replace a Job clone's design source and remap dependent subelements.

        Retains clone identity, operation/dressup links and design sources.
        subelement_map must explicitly include every referenced FaceN/EdgeN/
        VertexN, even unchanged names; semantic equivalence is caller-selected.
        preserve_setup retains the clone Placement; preserve_transform applies
        its existing source-to-setup delta to the new source Placement. Refits
        FromBase stock by default; explicit stock requires refit_stock=False.
        Regenerates all paths; invalid geometry/dependencies roll back.
        """
        return await run(_MODEL_RUNTIME + f"\n_result_=replace_setup_model({job_name!r},{model_name!r},{source_name!r},{subelement_map or {}!r},{placement_mode!r},{refit_stock!r})")

    @mcp.tool()
    async def cam_add_job_models(job_name: str, source_names: list[str], refit_stock: bool = True) -> dict[str, Any]:
        """Add native model clones, allowing repeated instances of a source.

        Refits FromBase stock by default; explicit stock requires refit_stock=False.
        Regenerates nested operation paths and retains original design objects.
        """
        return await run(_SETUP_RUNTIME + f"\n_result_=add_setup_models({job_name!r},{source_names!r},{refit_stock!r})")

    @mcp.tool()
    async def cam_remove_job_model(job_name: str, model_name: str, refit_stock: bool = True) -> dict[str, Any]:
        """Remove one Job model clone, retaining its source and other instances.

        Rejects the last model and clones with explicit dependent references.
        Refits FromBase stock by default; explicit stock requires refit_stock=False.
        Recomputes operations that use automatic Job geometry.
        """
        return await run(_SETUP_RUNTIME + f"\n_result_=remove_setup_model({job_name!r},{model_name!r},{refit_stock!r})")

    @mcp.tool()
    async def cam_set_center_of_rotation(job_name: str, x: float, y: float, z: float) -> dict[str, Any]:
        """Set native Path.Center metadata on the Job and all operation paths.

        Coordinates are mm. Includes nested dressups and hidden base paths;
        rejects centers inconsistent with an AxisMap's native radius center.
        Models and G-code coordinates are unchanged. Requested center persists
        on the Job, while native regeneration/reopen may reset Path.Center;
        inspect status and reapply after regeneration. This is not a machining
        setup transform, WCS offset or G-code rotary-axis conversion.
        """
        return await run(_JOB_RUNTIME + f"\n_result_=set_rotation_center({job_name!r},[{x!r},{y!r},{z!r}])")

    @mcp.tool()
    async def cam_inspect_rotation_center(job_name: str) -> dict[str, Any]:
        """Compare requested rotation metadata with every current native path.

        Reports stale values after native regeneration or reopen, without
        modifying the toolpaths. Reapply using cam_set_center_of_rotation.
        """
        return await run(_JOB_RUNTIME + f"\n_result_=rotation_center_status(operation_job({job_name!r}))")

    @mcp.tool()
    async def cam_create_job_from_template(name: str, template_file: str, model_names: list[str] | None = None) -> dict[str, Any]:
        """Create a native Path Job from FreeCAD's JSON job template."""
        names = model_names or []
        return await run("""import Path.Main.Job as PathJob, json
from pathlib import Path
from Path.Base.Util import isValidBaseObject
path=Path(%r)
with path.open(encoding='utf-8') as stream: template=json.load(stream)
if not isinstance(template,dict) or template.get('Version')!=1: raise ValueError('Expected native Job template Version 1')
doc=App.ActiveDocument
if doc is None: raise ValueError('Open a document containing the Job models first')
names=%r
jbase=[doc.getObject(n) for n in names] if names else [o for o in doc.Objects if isValidBaseObject(o)]
if any(o is None or not isValidBaseObject(o) for o in jbase): raise ValueError('Model name is missing or not a valid CAM model')
if not jbase: raise ValueError('Job requires at least one model object')
j=PathJob.Create(%r,jbase,str(path)); doc.recompute(); _result_={'name':j.Name,'type':j.TypeId,'template':str(path),'models':[o.Name for o in j.Model.Group],'tools':[t.Name for t in j.Tools.Group]}""" % (template_file, names, name))

    @mcp.tool()
    async def cam_export_job_template(job_name: str, file_path: str) -> dict[str, Any]:
        """Export settings, stock, ToolControllers and SetupSheet to a native template.

        Job templates contain machining defaults, not geometry or operations;
        save the FCStd document to preserve the entire machining project.
        """
        return await run("""import json
from pathlib import Path
from Path.Main.Job import ObjectJob
j=App.ActiveDocument.getObject(%r)
if not j or not isinstance(getattr(j,'Proxy',None),ObjectJob): raise ValueError('Object is not a native Path Job')
data=j.Proxy.templateAttrs(j)
if getattr(j,'Stock',None) is not None:
 import Path.Main.Stock as PathStock
 data['Stock']=PathStock.TemplateAttributes(j.Stock)
data['ToolController']=[t.Proxy.templateAttrs(t) for t in getattr(j.Tools,'Group',[]) if hasattr(getattr(t,'Proxy',None),'templateAttrs')]
setup=j.Proxy.setupSheet
data['SetupSheet']=setup.templateAttributes(includeOps=setup.operationsWithSettings())
data['Fixtures']=[{code:True} for code in j.Fixtures]
data['OrderOutputBy']=j.OrderOutputBy; data['SplitOutput']=j.SplitOutput
data=setup.encodeTemplateAttributes(data)
path=Path(%r)
with path.open('w',encoding='utf-8') as stream: json.dump(data,stream,indent=2,sort_keys=True)
_result_={'job':j.Name,'path':str(path),'exists':path.is_file(),'size':path.stat().st_size,'tool_count':len(data['ToolController']),'includes_setup_sheet':True}""" % (job_name, file_path))

    @mcp.tool()
    async def cam_import_toolbit(tool_file: str, job_name: str = "", tool_controller_name: str = "") -> dict[str, Any]:
        """Import a native ToolBit JSON file from the FreeCAD host.

        A job_name creates a new controller in that Job unless an existing
        tool_controller_name is supplied. With neither, create only a ToolBit.
        """
        return await run("""from Path.Tool.toolbit import ToolBit
from Path.Tool import Controller as TC
from Path.Main.Job import ObjectJob
doc=App.ActiveDocument
if doc is None: raise ValueError('No active document')
job=doc.getObject(%r) if %r else None
if %r and (job is None or not isinstance(getattr(job,'Proxy',None),ObjectJob)): raise ValueError('Native Job not found')
tc=doc.getObject(%r) if %r else None
if %r and (tc is None or not isinstance(getattr(tc,'Proxy',None),TC.ToolController)): raise ValueError('Native ToolController not found')
if job and tc and tc not in job.Tools.Group: raise ValueError('ToolController does not belong to the supplied Job')
bit=ToolBit.from_file(%r); obj=bit.attach_to_doc(doc=doc)
if tc: tc.Tool=obj
elif job:
 number=max([t.ToolNumber for t in job.Tools.Group]+[0])+1
 tc=TC.Create(name=obj.Label,tool=obj,toolNumber=number,assignViewProvider=bool(App.GuiUp)); job.Proxy.addToolController(tc)
doc.recompute(); _result_={'tool':obj.Name,'label':obj.Label,'job':job.Name if job else None,'tool_controller':tc.Name if tc else None,'toolbit':bit.to_dict()}""" % (job_name, bool(job_name), bool(job_name), tool_controller_name, bool(tool_controller_name), bool(tool_controller_name), tool_file))

    @mcp.tool()
    async def cam_export_toolbit(tool_name: str, file_path: str) -> dict[str, Any]:
        """Write a native .fctb ToolBit file that can be loaded by FreeCAD."""
        return await run("""from pathlib import Path
from Path.Tool.toolbit import ToolBit
from Path.Tool.toolbit.serializers.fctb import FCTBSerializer
o=App.ActiveDocument.getObject(%r)
if not o or not isinstance(getattr(o,'Proxy',None),ToolBit): raise ValueError('Native ToolBit not found')
data=FCTBSerializer.serialize(o.Proxy); path=Path(%r); path.write_bytes(data)
_result_={'tool':o.Name,'path':str(path),'bytes':len(data),'tool_id':o.Proxy.get_id()}""" % (tool_name,file_path))


    @mcp.tool()
    async def cam_create_tool_library(label: str, tools: list[dict[str, Any]] | None = None,
                                       library_id: str = "", store: str = "local") -> dict[str, Any]:
        """Create a persistent native CAM tool library.

        Each tools entry has number and exactly one source: tool_id (asset),
        tool_file (FreeCAD-host path), or tool_object (document ToolBit name).
        Writes the library and its ToolBits into the selected CAM asset store.
        """
        return await run(_LIBRARY_RUNTIME + f"\n_result_=create_library({label!r},{tools or []!r},{library_id!r},{store!r})")

    @mcp.tool()
    async def cam_inspect_tool_library(library_id: str, store: str = "local") -> dict[str, Any]:
        """Read native library tool numbers, labels and complete ToolBit definitions."""
        return await run(_LIBRARY_RUNTIME + f"\n_result_=library_data(load_library({library_id!r},{store!r}))")

    @mcp.tool()
    async def cam_edit_tool_library(library_id: str, label: str | None = None,
                                     tools: list[dict[str, Any]] | None = None,
                                     remove_numbers: list[int] | None = None,
                                     store: str = "local") -> dict[str, Any]:
        """Rename a library, upsert numbered tools, or remove tool numbers.

        Tool sources match cam_create_tool_library. Removing a library member
        retains the shared ToolBit asset. Duplicate numbers/IDs are rejected.
        """
        return await run(_LIBRARY_RUNTIME + f"\n_result_=edit_library({library_id!r},{label!r},{tools or []!r},{remove_numbers or []!r},{store!r})")

    @mcp.tool()
    async def cam_delete_tool_library(library_id: str, store: str = "local") -> dict[str, Any]:
        """Delete the library asset, retaining its shared ToolBits and shapes."""
        return await run(_LIBRARY_RUNTIME + f"""
asset_store({store!r},writable=True)
uri=asset_uri({library_id!r},'toolbitlibrary')
if not cam_assets.exists(uri,store={store!r}): raise ValueError('Library not found')
cam_assets.delete(uri,store={store!r})
_result_={{'deleted':str(uri),'store':{store!r},'toolbits_preserved':True}}
""")

    @mcp.tool()
    async def cam_export_tool_library(library_id: str, file_path: str, format: str = "fctl",
                                       store: str = "local", overwrite: bool = False) -> dict[str, Any]:
        """Export native FCTL with companion ToolBits/shapes, CAMotics JSON or LinuxCNC TBL.

        Paths refer to the FreeCAD host. LinuxCNC output uses the current
        FreeCAD unit preferences and is export-only in the installed native API.
        """
        return await run(_LIBRARY_RUNTIME + f"\n_result_=export_library({library_id!r},{file_path!r},{format!r},{store!r},{overwrite!r})")

    @mcp.tool()
    async def cam_import_tool_library(file_path: str, library_id: str = "", label: str | None = None,
                                       store: str = "local", overwrite: bool = False) -> dict[str, Any]:
        """Import FCTL or CAMotics JSON into a native persistent CAM asset library."""
        return await run(_LIBRARY_RUNTIME + f"\n_result_=import_library({file_path!r},{library_id!r},{label!r},{store!r},{overwrite!r})")

    @mcp.tool()
    async def cam_add_library_tool_to_job(library_id: str, tool_number: int, job_name: str,
                                          store: str = "local") -> dict[str, Any]:
        """Attach a library ToolBit and numbered native ToolController to a Job."""
        return await run(_LIBRARY_RUNTIME + f"\n_result_=attach_library_tool({library_id!r},{tool_number!r},{job_name!r},{store!r})")


    @mcp.tool()
    async def cam_create_machine(label: str = "Machine", machine_id: str = "", parameters: dict[str, Any] | None = None, store: str = "local") -> dict[str, Any]:
        """Create a native spindle/feed-limit asset; duplicate IDs are rejected.

        Numeric parameters: max_power in kW; min_rpm, max_rpm and
        peak_torque_rpm in RPM; max_torque in Nm; min_feed and max_feed
        in mm/min. Returns explicit W/RPM/Nm/mm-min quantities. This asset
        does not bind a geometric machine model to a Job.
        """
        return await run(_LIBRARY_RUNTIME + _MACHINE_RUNTIME + f"\n_result_=create_machine({label!r},{parameters or {}!r},{machine_id!r},{store!r})")

    @mcp.tool()
    async def cam_edit_machine(
        machine_id: str, parameters: dict[str, Any] | None = None,
        label: str | None = None, store: str = "local",
    ) -> dict[str, Any]:
        """Edit native machine limits atomically, preserving omitted fields.

        Parameters use the same units as cam_create_machine: kW, RPM, Nm and
        mm/min. Invalid limits fail without native setter auto-clamping.
        Stored quantities are read back and verified before committing.
        """
        return await run(_LIBRARY_RUNTIME + _MACHINE_RUNTIME + f"\n_result_=edit_machine({machine_id!r},{parameters if parameters is not None else {}!r},{label!r},{store!r})")

    @mcp.tool()
    async def cam_export_machine(
        machine_id: str, file_path: str, store: str = "local", overwrite: bool = False,
    ) -> dict[str, Any]:
        """Export native Machine v1 JSON (.fcm), with verified quantities.

        Native files store W, Hz, Nm and mm/min. The installed native loader
        misreads W/Hz as kW/RPM; cam_import_machine corrects this upstream bug.
        Export retains the native schema and reports this interoperability
        limitation. Existing files require overwrite=True.
        """
        return await run(_LIBRARY_RUNTIME + _MACHINE_RUNTIME + f"\n_result_=export_machine({machine_id!r},{file_path!r},{store!r},{overwrite!r})")

    @mcp.tool()
    async def cam_import_machine(
        file_path: str, machine_id: str = "", label: str | None = None,
        store: str = "local", overwrite: bool = False,
    ) -> dict[str, Any]:
        """Import native Machine v1 JSON (.fcm) into a CAM asset store.

        Uses the file ID unless overridden, with filename fallback if absent.
        Validates the complete asset before writing; reads back native W/Hz
        quantities correctly and rejects duplicate IDs unless overwrite=True.
        """
        return await run(_LIBRARY_RUNTIME + _MACHINE_RUNTIME + f"\n_result_=import_machine({file_path!r},{machine_id!r},{label!r},{store!r},{overwrite!r})")

    @mcp.tool()
    async def cam_inspect_machine(machine_id: str, store: str = "local") -> dict[str, Any]:
        """Inspect a persisted CAM machine's validated limits."""
        return await run(_LIBRARY_RUNTIME + _MACHINE_RUNTIME + f"\n_result_=inspect_machine({machine_id!r},{store!r})")

    @mcp.tool()
    async def cam_delete_machine(machine_id: str, store: str = "local") -> dict[str, Any]:
        """Delete a persisted CAM machine definition."""
        return await run(_LIBRARY_RUNTIME + _MACHINE_RUNTIME + f"\n_result_=delete_machine({machine_id!r},{store!r})")

    @mcp.tool()
    async def cam_evaluate_machine(machine_id: str, rpm: float, store: str = "local") -> dict[str, Any]:
        """Evaluate native machine torque and spindle limits at an RPM."""
        return await run(_LIBRARY_RUNTIME + _MACHINE_RUNTIME + f"\n_result_=evaluate_machine({machine_id!r},{rpm!r},{store!r})")

    @mcp.tool()
    async def cam_validate_job_machine(job_name: str, machine_id: str, store: str = "local") -> dict[str, Any]:
        """Validate Job ToolControllers against a persisted native machine definition."""
        return await run(_LIBRARY_RUNTIME + _MACHINE_RUNTIME + f"\n_result_=validate_job_machine({job_name!r},{machine_id!r},{store!r})")
