"""Assembly workbench tools using FreeCAD's Assembly API."""
from collections.abc import Awaitable, Callable
import textwrap
from pathlib import Path
from typing import Any

_STATE_RUNTIME = Path(__file__).with_name('_scripts').joinpath('assembly_state.py').read_text(encoding='utf-8')

_JOINT_RUNTIME = _STATE_RUNTIME + "\n" + Path(__file__).with_name("_scripts").joinpath("assembly_joints.py").read_text(encoding="utf-8")
_JOINT_RUNTIME += "\n" + Path(__file__).with_name("_scripts").joinpath("assembly_sync.py").read_text(encoding="utf-8")

_EXCHANGE_RUNTIME = _JOINT_RUNTIME + "\n" + Path(__file__).with_name("_scripts").joinpath("assembly_exchange.py").read_text(encoding="utf-8")

_SIMULATION_RUNTIME = _JOINT_RUNTIME + "\n" + Path(__file__).with_name("_scripts").joinpath("assembly_simulation.py").read_text(encoding="utf-8")

_BOM_RUNTIME = _JOINT_RUNTIME + "\n" + Path(__file__).with_name("_scripts").joinpath("assembly_bom.py").read_text(encoding="utf-8")

_VIEW_RUNTIME = _JOINT_RUNTIME + "\n" + Path(__file__).with_name("_scripts").joinpath("assembly_views.py").read_text(encoding="utf-8")
_INSTANCE_RUNTIME = _JOINT_RUNTIME + "\n" + Path(__file__).with_name("_scripts").joinpath("assembly_instances.py").read_text(encoding="utf-8")

_TRANSACTION_RUNTIME = Path(__file__).with_name('_scripts').joinpath('transaction.py').read_text(encoding='utf-8')

def register_assembly_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    async def run(code: str) -> dict[str, Any]:
        # Console documents start with Undo disabled; enable it temporarily
        # so exceptions really roll back the native document mutation.
        wrapped = _TRANSACTION_RUNTIME + "\nwith mcp_transaction('Assembly MCP operation'):\n" + textwrap.indent(code, "    ")
        r = await (await get_bridge()).execute_python(wrapped)
        return {"success": r.success, "result": r.result, "stdout": r.stdout, "stderr": r.stderr, "error": r.error_traceback}

    @mcp.tool()
    async def assembly_create(name: str = "Assembly") -> dict[str, Any]:
        """Create an Assembly container (native Assembly workbench when available)."""
        return await run("doc=App.ActiveDocument or App.newDocument('Assembly'); o=doc.getObject(%r) or doc.addObject('Assembly::AssemblyObject',%r); o.Type='Assembly'; import UtilsAssembly; UtilsAssembly.getJointGroup(o); doc.recompute(); _result_={'name':o.Name,'type':o.TypeId,'joint_group':UtilsAssembly.getJointGroup(o).Name}" % (name,name))

    @mcp.tool()
    async def assembly_insert_component(assembly_name: str, source_name: str, instance_name: str = "") -> dict[str, Any]:
        """Insert a native AssemblyLink (or App::Link for non-assembly sources)."""
        inst = instance_name or source_name + "_Instance"
        return await run("""doc=App.ActiveDocument; a=doc.getObject(%r); s=doc.getObject(%r)
if not a or not s: raise ValueError('Assembly or source object not found')
typ='Assembly::AssemblyLink' if s.isDerivedFrom('Assembly::AssemblyObject') else 'App::Link'; l=a.newObject(typ,%r); l.LinkedObject=s; doc.recompute(); _result_={'instance':l.Name,'source':s.Name,'type':l.TypeId}""" % (assembly_name,source_name,inst))

    @mcp.tool()
    async def assembly_insert_external_component(assembly_name: str, file_path: str,
                                                 source_name: str = "", instance_name: str = "") -> dict[str, Any]:
        """Insert an external link into a saved assembly document and restore active context."""
        return await run("""import os
if not os.path.isfile(%r): raise FileNotFoundError(%r)
doc=App.ActiveDocument; a=doc.getObject(%r)
if not a: raise ValueError('Assembly not found')
if not doc.FileName: raise ValueError('Save the assembly document before inserting an external link')
existing=set(App.listDocuments()); src_doc=None
try:
 src_doc=App.openDocument(%r)
 if not src_doc: raise RuntimeError('Unable to open external FreeCAD document')
 source=src_doc.getObject(%r) if %r else next((o for o in src_doc.Objects if hasattr(o,'Shape') and not o.Shape.isNull()),None)
 if not source: raise ValueError('No shape object found in external document')
 name=%r or source.Name+'_Instance'
 typ='Assembly::AssemblyLink' if source.isDerivedFrom('Assembly::AssemblyObject') else 'App::Link'
 link=a.newObject(typ,name); link.LinkedObject=source; doc.recompute()
 _result_={'instance':link.Name,'source':source.Name,'source_document':src_doc.Name,'file_path':%r,'type':link.TypeId}
except Exception:
 if src_doc is not None and src_doc.Name not in existing: App.closeDocument(src_doc.Name)
 raise
finally:
 App.setActiveDocument(doc.Name)""" % (file_path,file_path,assembly_name,file_path,source_name,bool(source_name),instance_name,file_path))

    @mcp.tool()
    async def assembly_remove_component(assembly_name: str, component_name: str) -> dict[str, Any]:
        """Remove a direct link instance, preserving sources and other instances.

        Supports App::Link and nested AssemblyLink-owned component/joint copies.
        Removes dependent joints/motions; trims exploded-step selections or
        removes empty steps. Other explicit dependents cause a preflight error.
        """
        return await run(_INSTANCE_RUNTIME + f"\n_result_=remove_assembly_component({assembly_name!r},{component_name!r})")

    @mcp.tool()
    async def assembly_create_part(assembly_name: str, part_name: str = "Part", link_existing: bool = True) -> dict[str, Any]:
        """Create a native Assembly App::Part/Body/Sketch and optionally link it."""
        safe_name = part_name.replace('"', '_')
        return await run("""import UtilsAssembly
a=App.ActiveDocument.getObject(%r)
if not a: raise ValueError('Assembly not found')
part,body=UtilsAssembly.createPart(%r,App.ActiveDocument); link=None
if %r:
 link=a.newObject('App::Link',%r+'_Link'); link.LinkedObject=part
App.ActiveDocument.recompute(); _result_={'assembly':a.Name,'part':part.Name,'body':body.Name,'link':link.Name if link else None}""" % (assembly_name, part_name, link_existing, safe_name))

    @mcp.tool()
    async def assembly_set_placement(instance_name: str, x: float=0, y: float=0, z: float=0, rx: float=0, ry: float=0, rz: float=1, angle: float=0) -> dict[str, Any]:
        """Set an assembly component placement."""
        return await run("o=App.ActiveDocument.getObject(%r); o.Placement=App.Placement(App.Vector(%s,%s,%s),App.Rotation(App.Vector(%s,%s,%s),%s)); App.ActiveDocument.recompute(); _result_=o.Name" % (instance_name,x,y,z,rx,ry,rz,angle))

    @mcp.tool()
    async def assembly_add_constraint(
        assembly_name: str, constraint_type: str, first: str, second: str,
        first_element: str = "", second_element: str = "",
        first_vertex: str = "", second_vertex: str = "", value: float | None = None,
        distance2: float | None = None, reversed: bool = False,
        first_offset: float = 0.0, second_offset: float = 0.0, solve: bool = True,
    ) -> dict[str, Any]:
        """Create a validated native joint and explicitly report solver status.

        References select different moving components within assembly_name.
        Native FaceN/EdgeN/VertexN names are one-based. value is distance in
        mm for Distance, pitch radius for RackPinion, pitch for Screw, first
        radius for Gears/Belt, or degrees for Angle. distance2 is the second
        Gears/Belt radius. Radii must be positive; screw/rack pitch nonzero.
        Coupling joints require structural support joints connected to ground;
        Screw/RackPinion also require an aligned Slider. Native solve failures
        roll back creation. With no ground, solver.solved is false. solve=False
        permits staged construction and reports recompute_pending.
        """
        return await run(_JOINT_RUNTIME + f"\n_result_ = assembly_create_joint({assembly_name!r}, {constraint_type!r}, {first!r}, {second!r}, {first_element!r}, {second_element!r}, {first_vertex!r}, {second_vertex!r}, {value!r}, {distance2!r}, {reversed!r}, {first_offset!r}, {second_offset!r}, {solve!r})")

    @mcp.tool()
    async def assembly_inspect(assembly_name: str) -> dict[str, Any]:
        """Inspect all group joints, including suppressed and invalid references."""
        return await run(_JOINT_RUNTIME + f"""
a = native_assembly({assembly_name!r})
_result_ = {{'name':a.Name,'type':a.TypeId,
 'members':[{{'name':x.Name,'type':x.TypeId,'placement':json_value(x.Placement) if hasattr(x,'Placement') else None}} for x in a.Group],
 'joints':joint_records(a)}}""")

    @mcp.tool()
    async def assembly_connection_graph(assembly_name: str) -> dict[str, Any]:
        """Inspect component edges, suppression, grounding and invalid references."""
        return await run(_JOINT_RUNTIME + f"""
a = native_assembly({assembly_name!r}); edges = []
for joint in assembly_joint_objects(a):
 refs = [getattr(joint,'Reference1',None),getattr(joint,'Reference2',None)]
 names = [ref[0].Name if ref and ref[0] else None for ref in refs]
 edges.append({{'joint':joint.Name,'type':getattr(joint,'JointType','Grounded'),
 'a':names[0],'b':names[1],'grounded_component':getattr(getattr(joint,'ObjectToGround',None),'Name',None),
 'suppressed':bool(getattr(joint,'Suppressed',False))}})
_result_ = {{'assembly':a.Name,'nodes':[{{'name':o.Name,'type':o.TypeId}} for o in assembly_moving_components(a)],'edges':edges}}""")

    @mcp.tool()
    async def assembly_component_connectivity(assembly_name: str, component_name: str, ignored_joint: str = "") -> dict[str, Any]:
        """Inspect native ground connectivity and optional downstream components.

        Downstream traversal requires an explicit joint to ignore; no arbitrary
        first joint is excluded. Invalid references fail before native queries.
        """
        return await run(_JOINT_RUNTIME + f"""
a = native_assembly({assembly_name!r}); c = assembly_reference(a,{component_name!r})[0]
issues, active, grounds = assembly_preflight(a)
if any(i['kind'] in ('invalid_reference','invalid_ground','self_reference') for i in issues): raise ValueError('Repair invalid references before querying native connectivity')
ignore = a.Document.getObject({ignored_joint!r}) if {bool(ignored_joint)!r} else None
if {bool(ignored_joint)!r} and ignore not in active: raise ValueError('ignored_joint must be an active joint of this Assembly')
downstream = a.getDownstreamParts(c,ignore) if ignore is not None else None
_result_ = {{'assembly':a.Name,'component':c.Name,'grounded':bool(a.isPartGrounded(c)),
 'connected_to_ground':bool(a.isPartConnected(c)),'ignored_joint':{ignored_joint!r} or None,
 'downstream_unconstrained':[x.Name for x in downstream] if downstream is not None else None}}""")

    @mcp.tool()
    async def assembly_inspect_joint(joint_name: str) -> dict[str, Any]:
        """Inspect native joint properties with structured units and links."""
        return await run(_STATE_RUNTIME + """
j=App.ActiveDocument.getObject(%r)
if not j or not (hasattr(j,'JointType') or hasattr(j,'ObjectToGround')): raise ValueError('Assembly joint not found')
_result_={'name':j.Name,'type':getattr(j,'JointType','Grounded'),
          'properties':{key:json_value(getattr(j,key)) for key in j.PropertiesList if key not in ('Proxy','ExpressionEngine')}}
""" % joint_name)

    @mcp.tool()
    async def assembly_ground_component(assembly_name: str, component_name: str) -> dict[str, Any]:
        """Ground an Assembly component; repeated grounding returns its existing joint."""
        return await run(_JOINT_RUNTIME + f"""
a = native_assembly({assembly_name!r})
ref = assembly_reference(a, {component_name!r})
c = ref[0]; group = UtilsAssembly.getJointGroup(a)
existing = next((j for j in group.Group if getattr(j,'ObjectToGround',None) is c), None)
with assembly_explicit_solve():
 j = existing or group.newObject('App::FeaturePython','GroundedJoint')
 if existing is None: JointObject.GroundedJoint(j,c)
 a.Document.recompute()
_result_ = {{'component':c.Name,'joint':j.Name,'type':'Grounded','existing':existing is not None}}""")

    @mcp.tool()
    async def assembly_solve(assembly_name: str, store_previous: bool = False) -> dict[str, Any]:
        """Validate active joint references/supports before running native solve.

        Reports native return code, issues and solved status. No grounded
        component is unsolved. Invalid joints remain available for repair;
        the native filtering API is not used to inspect them. store_previous
        enables assembly_undo_solve for the resulting placement change.
        """
        return await run(_JOINT_RUNTIME + f"\n_result_ = assembly_solve_result(native_assembly({assembly_name!r}), {store_previous!r})")

    @mcp.tool()
    async def assembly_export_asmt(assembly_name: str, file_path: str, overwrite: bool = False) -> dict[str, Any]:
        """Export checked native ASMT, including deeper flexible instance joints.

        Stages and validates constraint names, marker references and part poses
        before publishing. Repairs exact known native RTTI type-name corruption.
        Reports components omitted by the native solver exporter. This exchange
        contains no CAD shapes or measured mass/inertia or Simulation motions.
        Existing files require overwrite=True. Invalid input leaves them intact.
        """
        return await run(_EXCHANGE_RUNTIME + f"\n_result_=export_assembly_asmt({assembly_name!r},{file_path!r},{overwrite!r})")

    @mcp.tool()
    async def assembly_import_asmt(file_path: str, document_name: str = "") -> dict[str, Any]:
        """Probe the installed Assembly ASMT importer and report its status.

        FreeCAD 1.1 ships ``AssemblyImport`` as a placeholder for ASMT input;
        this tool exposes that fact explicitly and never reports a false import.
        """
        return await run("""import os
path=%r
if not os.path.isfile(path): raise FileNotFoundError(path)
try:
 import AssemblyImport
 doc=App.ActiveDocument or App.newDocument(%r or 'ImportedAssembly')
 before=len(doc.Objects); result=AssemblyImport.open(path); after=len(doc.Objects)
 _result_={'path':path,'available':True,'implemented':after>before,'objects_added':after-before,'result':str(result),'message':'Importer completed' if after>before else 'FreeCAD AssemblyImport.open is currently a placeholder'}
except Exception as exc:
 _result_={'path':path,'available':False,'implemented':False,'error_type':type(exc).__name__,'error':str(exc)}""" % (file_path, document_name))

    @mcp.tool()
    async def assembly_component_status(assembly_name: str, component_name: str) -> dict[str, Any]:
        """Report native grounded state and placement for an assembly component."""
        return await run("""a=App.ActiveDocument.getObject(%r); c=App.ActiveDocument.getObject(%r)
if not a or not c: raise ValueError('Assembly or component not found')
_result_={'assembly':a.Name,'component':c.Name,'grounded':bool(a.isPartGrounded(c)) if hasattr(a,'isPartGrounded') else False,'placement':{'x':c.Placement.Base.x,'y':c.Placement.Base.y,'z':c.Placement.Base.z},'type':c.TypeId}""" % (assembly_name, component_name))

    @mcp.tool()
    async def assembly_list_connectors(object_name: str) -> dict[str, Any]:
        """List selectable faces, edges and vertices for Assembly joint references."""
        return await run("""o=App.ActiveDocument.getObject(%r)
if not o: raise ValueError('Object not found')
shape=getattr(o,'Shape',None)
if shape is None and hasattr(o,'LinkedObject'): shape=o.LinkedObject.Shape
if shape is None: raise ValueError('Object has no shape')
_result_={'object':o.Name,'faces':[f'Face{i}' for i in range(1,len(shape.Faces)+1)],'edges':[f'Edge{i}' for i in range(1,len(shape.Edges)+1)],'vertices':[f'Vertex{i}' for i in range(1,len(shape.Vertexes)+1)]}""" % object_name)

    @mcp.tool()
    async def assembly_resolve_sub_element(assembly_name: str, subname: str) -> dict[str, Any]:
        """Resolve an Assembly subelement path using the native resolver."""
        return await run("""a=App.ActiveDocument.getObject(%r)
if not a: raise ValueError('Assembly not found')
if hasattr(a,'resolveSubElement'):
 obj=a.resolveSubElement(%r,False,0)
 resolved_obj=obj[0] if isinstance(obj,tuple) else obj
 resolved_name=getattr(resolved_obj,'Name',None); resolved_type=getattr(resolved_obj,'TypeId',None)
 resolved_element=obj[2] if isinstance(obj,tuple) and len(obj)>2 else None
else:
 result=a.resolve(%r); obj=result[0] if isinstance(result,tuple) else result
 resolved_name=getattr(obj,'Name',None); resolved_type=getattr(obj,'TypeId',None); resolved_element=None
_result_={'assembly':a.Name,'subname':%r,'resolved':resolved_name,'type':resolved_type,'element':resolved_element}""" % (assembly_name, subname, subname, subname))

    @mcp.tool()
    async def assembly_create_bom(
        assembly_name: str, only_parts: bool = False, detail_parts: bool = True,
        detail_subassemblies: bool = True, columns: list[str] | None = None,
        name: str = "BillOfMaterials",
    ) -> dict[str, Any]:
        """Create native Assembly BOM with standard/custom/property columns.

        Defaults: Index, Name, Description, File Name, Quantity. A '.Length'
        column reads source properties. Quantity aggregates identical linked
        sources among siblings; nested rows are per subassembly, not flattened
        totals. Native custom text persistence matches source labels. Returns
        structure_validation because the installed native BOM can merge a
        parent's repeated source into its descendant row incorrectly.
        """
        return await run(_BOM_RUNTIME + f"\n_result_ = create_bom({assembly_name!r}, {only_parts!r}, {detail_parts!r}, {detail_subassemblies!r}, {columns!r}, {name!r})")

    @mcp.tool()
    async def assembly_parts_list(
        assembly_name: str, layout: str = "hierarchy", file_path: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Calculate assembly quantities by source document/object identity.

        hierarchy reports sibling quantities and totals multiplied by ancestor
        instances. flat merges unexpanded leaf components across the hierarchy.
        Returns both views; layout selects optional CSV output. This corrects
        native BOM descendant/sibling quantity merging without altering native
        spreadsheet cells. Existing CSV files require overwrite=True.
        """
        return await run(_BOM_RUNTIME + f"\n_result_ = parts_list({assembly_name!r}, {layout!r}, {file_path!r}, {overwrite!r})")

    @mcp.tool()
    async def assembly_inspect_bom(bom_name: str, recompute: bool = True) -> dict[str, Any]:
        """Read native BOM cell values, row/column counts, options and warnings."""
        return await run(_BOM_RUNTIME + f"\n_result_ = bom_data(native_bom({bom_name!r}), {recompute!r})")

    @mcp.tool()
    async def assembly_configure_bom(
        bom_name: str, columns: list[str] | None = None, only_parts: bool | None = None,
        detail_parts: bool | None = None, detail_subassemblies: bool | None = None,
    ) -> dict[str, Any]:
        """Change native BOM options/column order while retaining surviving custom text.

        Omitted fields retain settings. Source Label identifies native custom
        data; conflicting duplicate-label custom values are rejected. Renaming
        a custom column creates a different column and discards its old text.
        """
        return await run(_BOM_RUNTIME + f"\n_result_ = configure_bom({bom_name!r}, {columns!r}, {only_parts!r}, {detail_parts!r}, {detail_subassemblies!r})")

    @mcp.tool()
    async def assembly_edit_bom_cells(bom_name: str, edits: list[dict[str, Any]]) -> dict[str, Any]:
        """Edit native BOM custom text with {row, column, value} records.

        row is the spreadsheet row (first data row 2); column is its name.
        Generated Index/Name/Quantity/File Name and '.Property' columns are
        not editable. Duplicate source labels are rejected because native
        custom-data persistence cannot distinguish them. Values are literal
        text and are verified after native recompute. Native regeneration
        coerces numeric/unit/formula-like strings; unsupported literal values
        fail with rollback instead of silently changing their meaning.
        """
        return await run(_BOM_RUNTIME + f"\n_result_ = edit_bom_cells({bom_name!r}, {edits!r})")

    @mcp.tool()
    async def assembly_export_bom_csv(
        bom_name: str, file_path: str, overwrite: bool = False, delimiter: str = ",",
    ) -> dict[str, Any]:
        """Export native BOM CSV through the native spreadsheet exporter.

        Uses comma by default, doubled quotes and preserved trailing blanks.
        Checks the native exporter against evaluated cells, normalizing CSV
        when its escaping, dimensions or numeric precision differ. Native
        hierarchy/quantity mismatches fail: use assembly_parts_list instead.
        Existing output requires overwrite=True. Quantity cells use native
        internal units; inspect_bom retains corresponding unit metadata.
        """
        return await run(_BOM_RUNTIME + f"\n_result_ = export_bom_csv({bom_name!r}, {file_path!r}, {overwrite!r}, {delimiter!r})")

    @mcp.tool()
    async def assembly_set_joint_state(
        joint_name: str, suppressed: bool | None = None, angle: float | None = None,
        distance: float | None = None, distance2: float | None = None, solve: bool = True,
    ) -> dict[str, Any]:
        """Edit suppression and type-specific parameters atomically.

        Omitted fields are retained. Angle (degrees) is only for Angle joints;
        distance/distance2 (mm) follow assembly_add_constraint semantics.
        Explicit values replace expressions. These fields are not motor
        coordinates: use simulation motions to drive Revolute/Slider joints.
        """
        return await run(_JOINT_RUNTIME + f"\n_result_ = assembly_edit_joint({joint_name!r}, {suppressed!r}, {distance!r}, {distance2!r}, {angle!r}, {solve!r})")

    @mcp.tool()
    async def assembly_set_joint_property(joint_name: str, property_name: str, value: Any) -> dict[str, Any]:
        """Set a scalar native joint property with type and semantic validation.

        Dedicated state/limit/connector tools handle their corresponding
        fields. Reference, type, proxy, expression and placement changes are
        rejected here. Unsupported property types are not string-coerced.
        """
        return await run(_JOINT_RUNTIME + f"""
j = native_joint({joint_name!r}); key = {property_name!r}; value = {value!r}
if key in ('Distance','Distance2','Angle','Suppressed'):
 args = {{'Distance':'distance','Distance2':'distance2','Angle':'angle','Suppressed':'suppressed'}}
 changes = dict(suppressed=None,distance=None,distance2=None,angle=None,solve=True)
 changes[args[key]] = value
 _result_ = assembly_edit_joint(j.Name, **changes)
elif key in ('EnableLengthMin','EnableLengthMax','EnableAngleMin','EnableAngleMax','LengthMin','LengthMax','AngleMin','AngleMax'):
 fields = ('EnableLengthMin','EnableLengthMax','EnableAngleMin','EnableAngleMax','LengthMin','LengthMax','AngleMin','AngleMax')
 values = {{k:getattr(j,k) if k.startswith('Enable') else getattr(j,k).Value for k in fields}}
 values[key] = value
 _result_ = assembly_joint_limits(j.Name, values, True)
else:
 if key not in ('Label','Label2'): raise ValueError('Use a dedicated joint tool for this property: '+key)
 if not isinstance(value,str): raise ValueError('Label requires a string')
 setattr(j,key,value)
 _result_ = {{'name':j.Name,'type':j.JointType}}
_result_.update(property=key, value=json_value(getattr(j,key)))""")

    @mcp.tool()
    async def assembly_set_joint_limits(
        joint_name: str, enable_length_min: bool = False, length_min: float = 0.0,
        enable_length_max: bool = False, length_max: float = 0.0,
        enable_angle_min: bool = False, angle_min: float = 0.0,
        enable_angle_max: bool = False, angle_max: float = 360.0, solve: bool = True,
    ) -> dict[str, Any]:
        """Set validated static-solve limits: length in mm, angle in degrees.

        Length limits apply to Slider/Cylindrical, angular limits to
        Revolute/Cylindrical. Enabled min must not exceed max. Native
        kinematic simulation omits these limits; they do not bound motions.
        solve=False stages the changes without an explicit solve.
        """
        values = {'EnableLengthMin':enable_length_min,'LengthMin':length_min,
                  'EnableLengthMax':enable_length_max,'LengthMax':length_max,
                  'EnableAngleMin':enable_angle_min,'AngleMin':angle_min,
                  'EnableAngleMax':enable_angle_max,'AngleMax':angle_max}
        return await run(_JOINT_RUNTIME + f"\n_result_ = assembly_joint_limits({joint_name!r}, {values!r}, {solve!r})")

    @mcp.tool()
    async def assembly_remove_joint(joint_name: str) -> dict[str, Any]:
        """Remove a native joint and direct motions, retaining component geometry.

        Tracked instance motions retain their formulas for explicit repair and
        are listed by document/name. Other open documents are not edited.
        Unavailable source targets prevent generation until repaired or removed.
        """
        return await run(_JOINT_RUNTIME + f"\n_result_=remove_native_joint({joint_name!r})")

    @mcp.tool()
    async def assembly_get_joint_types() -> dict[str, Any]:
        """Discover installed native joint names, scalar parameters and limit support."""
        return await run(_JOINT_RUNTIME + """
_result_ = {'joint_types':list(JointObject.JointTypes),
 'motion_types':{'Revolute':['Angular'],'Slider':['Linear'],'Cylindrical':['Angular','Linear']},
 'pre_solve_types':list(JointObject.JointUsingPreSolve),
 'parameters':{kind:{'distance':kind in JointObject.JointUsingDistance,
 'distance2':kind in JointObject.JointUsingDistance2,'angle':kind in JointObject.JointUsingAngle,
 'length_limits':kind in JointObject.JointUsingLimitLength,'angle_limits':kind in JointObject.JointUsingLimitAngle,
 'reverse':kind in JointObject.JointUsingReverse} for kind in JointObject.JointTypes}}""")

    @mcp.tool()
    async def assembly_capabilities() -> dict[str, Any]:
        """Report Assembly workbench APIs available in the running FreeCAD."""
        return await run("""import importlib
items={}
for mod in ('JointObject','UtilsAssembly','CommandCreateSimulation','CommandCreateView','AssemblyImport'):
 try:
  m=importlib.import_module(mod); items[mod]={'available':True,'file':getattr(m,'__file__',None),'callables':[n for n in dir(m) if not n.startswith('_') and callable(getattr(m,n,None))]}
 except Exception as exc: items[mod]={'available':False,'error':str(exc)}
_result_={'assembly_object':hasattr(App.ActiveDocument,'addObject'),'export_asmt':hasattr(next((o for o in getattr(App.ActiveDocument,'Objects',[]) if 'AssemblyObject' in o.TypeId),None),'exportAsASMT') if App.ActiveDocument else False,'modules':items}""")

    @mcp.tool()
    async def assembly_validate_references(assembly_name: str) -> dict[str, Any]:
        """Resolve grounded and connector references without deleting invalid joints."""
        return await run(_JOINT_RUNTIME + f"""
a = native_assembly({assembly_name!r}); items = joint_records(a)
issues, active, grounds = assembly_preflight(a)
_result_ = {{'assembly':a.Name,'joint_count':len(items),
 'valid':all(all(r['valid'] for r in j['references']) for j in items), 'joints':items,
 'solver_prerequisite_issues':issues}}""")

    @mcp.tool()
    async def assembly_get_freedom(assembly_name: str) -> dict[str, Any]:
        """Estimate DOF from moving components and nominal joint counts.

        This is not the native constraint Jacobian rank; redundant and coupled
        constraints can make the estimate inaccurate. Exact DOF is unavailable
        through this adapter.
        """
        return await run(_STATE_RUNTIME + """
a=App.ActiveDocument.getObject(%r)
if not a: raise ValueError('Assembly not found')
components=assembly_moving_components(a); joints=joint_records(a)
active=[j for j in joints if not j['suppressed']]
constraints={'Fixed':6,'Grounded':6,'Revolute':5,'Cylindrical':4,'Slider':5,'Ball':3,'Distance':1,'Parallel':2,'Perpendicular':1,'Angle':1}
unknown=[j['name'] for j in active if j['type'] not in constraints]
estimated=None if unknown else max(0,6*len(components)-sum(constraints[j['type']] for j in active))
_result_={'assembly':a.Name,'components':len(components),'joints':len(joints),
          'grounded':sum(j['type']=='Grounded' for j in active),'active_joints':len(active),
          'estimated_dof':estimated,'exact_dof':None,'exact_dof_available':False,'unsupported_joint_estimates':unknown,
          'dof_method':'Nominal constraint count; does not detect redundant constraints or compute solver rank'}
""" % assembly_name)

    @mcp.tool()
    async def assembly_solver_diagnostics(assembly_name: str) -> dict[str, Any]:
        """Report native solve outcome, invalid references and coupling prerequisites.

        An invalid reference is separate from solver-reported conflicts.
        Exact constraint rank and native minimal conflict sets are not exposed.
        """
        return await run(_JOINT_RUNTIME + f"""
a = native_assembly({assembly_name!r})
_result_ = assembly_solve_result(a)
_result_['invalid_references'] = [i['joint'] for i in _result_['issues'] if i['kind'] in ('invalid_reference','invalid_ground','self_reference')]
_result_.update(conflicts=None, conflict_details_available=False, error=None)""")

    @mcp.tool()
    async def assembly_create_simulation(assembly_name: str, start: float = 0.0, end: float = 10.0, step: float = 0.1, error_tolerance: float = 1e-6, frames_per_second: int = 30) -> dict[str, Any]:
        """Create the native Assembly simulation feature and configure its time range."""
        return await run(_SIMULATION_RUNTIME + f"\n_result_=create_assembly_simulation({assembly_name!r},{start!r},{end!r},{step!r},{error_tolerance!r},{frames_per_second!r})")

    @mcp.tool()
    async def assembly_add_motion(simulation_name: str, joint_name: str, motion_type: str = "Angular", formula: str = "5*time") -> dict[str, Any]:
        """Add a native kinematic Motion to the simulation Group.

        Formula output is the absolute joint coordinate: mm for Linear and
        radians for Angular; time is seconds. For 30 degrees/second use
        'pi/6*time'. Native first frame may retain the pre-generation pose.
        Includes joints inside flexible subassemblies. Static joint limits
        are omitted by the native kinematic simulator.
        """
        return await run(_SIMULATION_RUNTIME + f"\n_result_=add_assembly_motion({simulation_name!r},{joint_name!r},{motion_type!r},{formula!r})")

    @mcp.tool()
    async def assembly_inspect_simulation(simulation_name: str) -> dict[str, Any]:
        """Inspect native time settings, motions, stable instance targets and frame state."""
        return await run(_SIMULATION_RUNTIME + f"\n_result_=simulation_data({simulation_name!r})")

    @mcp.tool()
    async def assembly_edit_motion(motion_name: str, joint_name: str | None = None,
                                   motion_type: str | None = None, formula: str | None = None) -> dict[str, Any]:
        """Edit a native motion, preserving omitted values and rejecting duplicate drives.

        Explicit joint_name establishes a new source/instance binding for a flexible
        joint copy. Tracked motions follow source reordering/recreated copies.
        Suppressed or deleted source joints are unavailable, never silently retargeted.
        Other broken motions remain listed as issues and can be repaired in turn.
        Formula syntax is checked by the native solver when simulation runs.
        """
        return await run(_SIMULATION_RUNTIME + f"\n_result_=edit_assembly_motion({motion_name!r},{joint_name!r},{motion_type!r},{formula!r})")

    @mcp.tool()
    async def assembly_edit_simulation(simulation_name: str, start: float | None = None,
                                       end: float | None = None, step: float | None = None,
                                       error_tolerance: float | None = None,
                                       frames_per_second: int | None = None) -> dict[str, Any]:
        """Edit native simulation timing with full validation; omitted fields are retained.

        Times use seconds. A successful edit invalidates cached frames; rerun to
        generate output. Motions need not exist yet when configuring the time range.
        """
        return await run(_SIMULATION_RUNTIME + f"\n_result_=edit_assembly_simulation({simulation_name!r},{start!r},{end!r},{step!r},{error_tolerance!r},{frames_per_second!r})")

    @mcp.tool()
    async def assembly_remove_motion(motion_name: str) -> dict[str, Any]:
        """Remove one native Motion and invalidate frames, retaining its joint."""
        return await run(_SIMULATION_RUNTIME + f"\n_result_=remove_assembly_motion({motion_name!r})")

    @mcp.tool()
    async def assembly_remove_simulation(simulation_name: str) -> dict[str, Any]:
        """Remove a native Simulation and its owned Motions, retaining joints/components."""
        return await run(_SIMULATION_RUNTIME + f"\n_result_=remove_assembly_simulation({simulation_name!r})")

    @mcp.tool()
    async def assembly_run_simulation(simulation_name: str) -> dict[str, Any]:
        """Generate and validate native kinematic frames for the current inputs.

        Failed generation invalidates previous MCP frames, even when FreeCAD
        retains partial solver output. Native kinematic motion omits joint
        limits. Frames must be regenerated after document edits or reopening.
        """
        return await run(_SIMULATION_RUNTIME + f"\n_result_ = run_assembly_simulation({simulation_name!r})")

    @mcp.tool()
    async def assembly_get_frame(assembly_name: str, frame: int) -> dict[str, Any]:
        """Apply a current MCP-generated simulation frame by zero-based index.

        Out-of-range indexes fail. Stale, failed or reopened simulations must
        be regenerated. Frame application does not trigger a new static solve.
        """
        return await run(_SIMULATION_RUNTIME + f"\n_result_ = apply_assembly_frame(native_assembly({assembly_name!r}), {frame!r})")

    @mcp.tool()
    async def assembly_simulation_status(assembly_name: str) -> dict[str, Any]:
        """Report whether MCP-generated frames still match the current document."""
        return await run(_SIMULATION_RUNTIME + f"\n_result_ = assembly_frame_state(native_assembly({assembly_name!r}))")

    @mcp.tool()
    async def assembly_create_exploded_view(assembly_name: str, name: str = "ExplodedView") -> dict[str, Any]:
        """Create the native Assembly exploded-view container (GUI FreeCAD required)."""
        return await run(_VIEW_RUNTIME + f"\n_result_ = create_exploded_view({assembly_name!r}, {name!r})")

    @mcp.tool()
    async def assembly_add_exploded_step(
        view_name: str, component_name: str = "", dx: float = 0, dy: float = 0,
        dz: float = 10, move_type: str = "Translate", component_paths: list[str] | None = None,
        rotation_axis: list[float] | None = None, rotation_angle: float = 0,
        rotation_center: list[float] | None = None, name: str = "",
    ) -> dict[str, Any]:
        """Define a native step for one or more component instances without moving them.

        Use component_name for a direct member or component_paths for nested
        paths from assembly_inspect_exploded_view. Paths use generated instance
        names, not linked source names. Coordinates are in the assembly frame;
        rotation_angle is degrees about rotation_center. Translate/Rotate are
        aliases for native Normal; Radial uses the translation vector's length
        and factor 4*length/assembly_bbox_diagonal, with no rotation.
        """
        paths = component_paths if component_paths is not None else [component_name]
        if component_paths is not None and component_name:
            raise ValueError("Use component_name or component_paths, not both")
        return await run(_VIEW_RUNTIME + f"\n_result_ = create_exploded_step({view_name!r}, {paths!r}, [{dx!r}, {dy!r}, {dz!r}], {rotation_axis or [0,0,1]!r}, {rotation_angle!r}, {rotation_center or [0,0,0]!r}, {move_type!r}, {name!r})")

    @mcp.tool()
    async def assembly_inspect_exploded_view(view_name: str) -> dict[str, Any]:
        """Inspect native steps, validated instance paths and hierarchy placements."""
        return await run(_VIEW_RUNTIME + f"\n_result_ = inspect_exploded_view({view_name!r})")

    @mcp.tool()
    async def assembly_edit_exploded_step(
        step_name: str, component_paths: list[str] | None = None,
        move_type: str | None = None, transform: dict[str, Any] | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        """Edit a native step atomically; omitted fields preserve current values.

        transform replaces the complete movement: translation [mm],
        rotation_axis, rotation_angle [degrees], rotation_center [mm]. Defaults
        within an explicit transform are zero translation/rotation about Z at
        the assembly origin. Coordinates are in the assembly frame.
        """
        return await run(_VIEW_RUNTIME + f"\n_result_ = edit_exploded_step({step_name!r}, {component_paths!r}, {move_type!r}, {transform!r}, {label!r})")

    @mcp.tool()
    async def assembly_reorder_exploded_steps(view_name: str, step_names: list[str]) -> dict[str, Any]:
        """Reorder every native view step exactly once; order affects geometry."""
        return await run(_VIEW_RUNTIME + f"\n_result_ = reorder_exploded_steps({view_name!r}, {step_names!r})")

    @mcp.tool()
    async def assembly_remove_exploded_step(step_name: str) -> dict[str, Any]:
        """Delete a native exploded step, preserving its component references."""
        return await run(_VIEW_RUNTIME + f"\n_result_ = remove_exploded_step({step_name!r})")

    @mcp.tool()
    async def assembly_remove_exploded_view(view_name: str) -> dict[str, Any]:
        """Delete a native view and its steps, preserving assembly components."""
        return await run(_VIEW_RUNTIME + f"\n_result_ = remove_exploded_view({view_name!r})")

    @mcp.tool()
    async def assembly_exploded_frames(
        view_name: str, frames_per_step: int = 10, include_hidden: bool = False,
    ) -> dict[str, Any]:
        """Calculate sequential exploded-animation placements without moving sources.

        Returns document-space component placements and bounds per frame.
        Uses stored rotation centers with linear translation and shortest-arc
        quaternion slerp. Native/imported steps without a stored pivot use
        endpoint translation and rotation interpolation. Radial
        directions derive from component bounds at each step start. No native
        timer/player is launched; use returned frames or export partial shapes.
        """
        return await run(_VIEW_RUNTIME + f"\n_result_ = exploded_frames({view_name!r}, {frames_per_step!r}, {include_hidden!r})")

    @mcp.tool()
    async def assembly_export_exploded_shape(
        view_name: str, name: str = "ExplodedShape", progress: float | None = None,
        include_lines: bool = True, include_hidden: bool = False,
    ) -> dict[str, Any]:
        """Create a separate exploded shape from native step definitions.

        progress ranges from 0 to step_count, supporting fractional steps;
        omitted means fully exploded. Includes parent placements, nested
        instance geometry and optional sequential movement lines. Original
        component placements remain unchanged. Native step transforms are
        composed explicitly to correct original exporter placement/line bugs.
        """
        return await run(_VIEW_RUNTIME + f"\n_result_ = export_exploded_shape({view_name!r}, {name!r}, {progress!r}, {include_lines!r}, {include_hidden!r})")

    @mcp.tool()
    async def assembly_instance_sync_status(assembly_name: str) -> dict[str, Any]:
        """Compare flexible copied-joint values to their source without recompute.

        Reports differences including Angle and detached connector placements
        omitted by the installed native synchronizer. Source matching follows
        native active-joint group order; component topology remains native-managed.
        current describes parameter synchronization; inspect motion_bindings
        separately for unavailable or manually changed motion targets.
        """
        return await run(_JOINT_RUNTIME + f"\n_result_=assembly_sync_status(native_assembly({assembly_name!r}))")

    @mcp.tool()
    async def assembly_sync_instances(assembly_name: str, solve: bool = False) -> dict[str, Any]:
        """Synchronize flexible joint copies including missing native fields.

        Runs native component synchronization, then copies all native scalar,
        offset/limit fields and detached connectors. Preserves existing moving
        component poses unless solve=True. Reports every corrected field.
        """
        return await run(_JOINT_RUNTIME + f"\na=native_assembly({assembly_name!r})\n_result_=synchronize_assembly_instances(a)\n_result_['solver']=assembly_solve_result(a,requested={solve!r},strict=True)")

    @mcp.tool()
    async def assembly_set_component_rigid(instance_name: str, rigid: bool = True, solve: bool = False) -> dict[str, Any]:
        """Switch native subassembly mode and migrate parent connector references.

        Flexible instances have independent child placements and copied source
        joints. Rigid mode restores source-relative child poses and removes
        copied joints. Native incompatible grounding removal is reported.
        Parent connectors need a child path before switching to flexible.
        Motions targeting deleted joint copies are removed and reported.
        Optional solve runs the parent solver including flexible joint copies.
        """
        return await run(_INSTANCE_RUNTIME + f"\n_result_=set_instance_rigid({instance_name!r},{rigid!r},{solve!r})")

    @mcp.tool()
    async def assembly_inspect_component(instance_name: str) -> dict[str, Any]:
        """Inspect native AssemblyLink mode, instance paths, sources and joint copies."""
        return await run(_INSTANCE_RUNTIME + f"\n_result_=instance_data(*assembly_instance({instance_name!r}))")

    @mcp.tool()
    async def assembly_flip_joint(joint_name: str) -> dict[str, Any]:
        """Flip the first/second joint connector orientation using the native proxy."""
        return await run("j=App.ActiveDocument.getObject(%r);\nif not hasattr(j,'Proxy') or not hasattr(j.Proxy,'flipOnePart'): raise ValueError('Object is not a native Assembly joint')\nj.Proxy.flipOnePart(j); App.ActiveDocument.recompute(); _result_={'name':j.Name,'type':j.JointType}" % joint_name)

    @mcp.tool()
    async def assembly_undo_solve(assembly_name: str) -> dict[str, Any]:
        """Restore placements saved by assembly_solve(store_previous=True)."""
        return await run(_JOINT_RUNTIME + f"""
a = native_assembly({assembly_name!r})
with assembly_explicit_solve():
 result = a.undoSolve()
 a.Document.recompute()
_result_ = {{'assembly':a.Name,'result':result}}""")

    @mcp.tool()
    async def assembly_get_global_placement(assembly_name: str, object_name: str,
                                            root_object: str = "", subname: str = "") -> dict[str, Any]:
        """Resolve placement through an explicit link path, including parent parts.

        Direct assembly members need only object_name. Nested or multiply linked
        sources require subname (for example LinkToPart.Body.Pad.Face1). Optional
        root_object changes the path root; otherwise use the assembly. Unique
        parent parts are included; ambiguous parent paths require explicit context.
        """
        return await run(f"""doc=App.ActiveDocument
if doc is None: raise ValueError('No active document')
a=doc.getObject({assembly_name!r}); o=doc.getObject({object_name!r})
if not a or not o: raise ValueError('Assembly or object not found')
root=doc.getObject({root_object!r}) if {bool(root_object)!r} else a
if root is None: raise ValueError('Root object not found')
path={subname!r}
if not path:
 if o is root: path=''
 elif o in getattr(root,'Group',[]): path=o.Name+'.'
 else: raise ValueError('Provide subname to disambiguate the target link path')
chain=root.getSubObjectList(path)
if o not in chain and not any(x.getLinkedObject() is o for x in chain): raise ValueError('Target is not referenced by subname')
visited=set()
while True:
 if root.Name in visited: raise ValueError('Cyclic parent path')
 visited.add(root.Name)
 parents=list(root.Parents)
 if not parents: break
 if len(parents)!=1: raise ValueError('Multiple parent contexts; choose a higher root_object and full subname')
 parent,prefix=parents[0]
 if not prefix.endswith('.'): prefix+='.'
 path=prefix+path; root=parent
if not hasattr(a,'getGlobalPlacementOf'): raise RuntimeError('FreeCAD has no link-aware global placement API')
p=a.getGlobalPlacementOf(o,root,path); r=p.Rotation
_result_={{'assembly':a.Name,'object':o.Name,'root_object':root.Name,'subname':path,'base':list(p.Base),'rotation':{{'x':r.Q[0],'y':r.Q[1],'z':r.Q[2],'w':r.Q[3]}}}}""")

    @mcp.tool()
    async def assembly_set_property(object_name: str, property_name: str, value: Any) -> dict[str, Any]:
        """Set a writable native Assembly object property with type conversion."""
        return await run("""o=App.ActiveDocument.getObject(%r)
if not o: raise ValueError('Object not found')
key=%r; value=%r
if key not in o.PropertiesList: raise ValueError('Unknown object property: '+key)
if key in ('Proxy','ExpressionEngine','Group','Joints','Placement'): raise ValueError('Property is managed or unsafe through this tool')
old=getattr(o,key)
if isinstance(old,bool): value=bool(value)
elif isinstance(old,(int,float)) and not isinstance(old,bool): value=type(old)(value)
setattr(o,key,value); App.ActiveDocument.recompute(); _result_={'object':o.Name,'property':key,'value':str(getattr(o,key))}""" % (object_name,property_name,value))

    @mcp.tool()
    async def assembly_list_subobjects(assembly_name: str, reason: int = 0) -> dict[str, Any]:
        """List native Assembly subelement reference paths."""
        return await run("""a=App.ActiveDocument.getObject(%r)
if not a: raise ValueError('Assembly not found')
if not hasattr(a,'getSubObjects'): raise RuntimeError('Assembly has no subobject enumeration API')
items=list(a.getSubObjects(int(%r))); _result_={'assembly':a.Name,'reason':int(%r),'subobjects':[str(x) for x in items]}""" % (assembly_name,reason,reason))

    @mcp.tool()
    async def assembly_set_element_visibility(assembly_name: str, element: str, visible: bool = True) -> dict[str, Any]:
        """Set visibility of a child element through the native Assembly API."""
        return await run("""a=App.ActiveDocument.getObject(%r)
if not a: raise ValueError('Assembly not found')
if not hasattr(a,'setElementVisible'): raise RuntimeError('Assembly has no element visibility API')
status=a.setElementVisible(%r,bool(%r)); App.ActiveDocument.recompute(); _result_={'assembly':a.Name,'element':%r,'visible':bool(%r),'status':status,'effective':a.isElementVisible(%r) if hasattr(a,'isElementVisible') else None}""" % (assembly_name,element,visible,element,visible,element))

    @mcp.tool()
    async def assembly_get_joint_connector(joint_name: str, connector: int = 1) -> dict[str, Any]:
        """Read a native joint connector reference, placement and detach state."""
        if connector not in (1, 2):
            raise ValueError("connector must be 1 or 2")
        return await run("""j=App.ActiveDocument.getObject(%r)
if not j: raise ValueError('Joint not found')
idx=int(%r); suffix=str(idx)
if not all(key+suffix in j.PropertiesList for key in ('Reference','Placement','Offset','Detach')): raise ValueError('Object has no native joint connector')
ref=getattr(j,'Reference'+suffix); p=getattr(j,'Placement'+suffix); offset=getattr(j,'Offset'+suffix)
def placement_data(value):
 return {'base':list(value.Base),'rotation':list(value.Rotation.Q)}
reference={'document':ref[0].Document.Name,'object':ref[0].Name,'subelements':list(ref[1])} if ref and ref[0] else None
_result_={'joint':j.Name,'connector':idx,'reference':reference,'detach':bool(getattr(j,'Detach'+suffix)),'placement':placement_data(p),'offset':placement_data(offset)}""" % (joint_name, connector))

    @mcp.tool()
    async def assembly_set_joint_connector(joint_name: str, connector: int = 1,
                                            detach: bool | None = None,
                                            x: float | None = None, y: float | None = None,
                                            z: float | None = None, rx: float = 0,
                                            ry: float = 0, rz: float = 1,
                                            angle: float | None = None) -> dict[str, Any]:
        """Edit a connector's local placement, preserving unspecified coordinates.

        Supplying coordinates or angle automatically detaches the connector
        unless detach=False is requested, which is incompatible with a manual
        placement. Set detach=False alone to restore geometry-driven placement.
        Rotation axis/angle use degrees; omitted angle preserves rotation.
        """
        if connector not in (1, 2):
            raise ValueError("connector must be 1 or 2")
        manual = any(v is not None for v in (x, y, z, angle))
        if detach is None and not manual:
            raise ValueError("provide detach, coordinates or angle")
        if manual and detach is False:
            raise ValueError("Manual placement requires a detached connector")
        if angle is not None and rx == ry == rz == 0:
            raise ValueError("rotation axis must be nonzero")
        return await run(f"""j=App.ActiveDocument.getObject({joint_name!r})
if not j: raise ValueError('Joint not found')
suffix=str({connector!r})
if not all(key+suffix in j.PropertiesList for key in ('Reference','Placement','Detach')): raise ValueError('Object has no native joint connector')
p=getattr(j,'Placement'+suffix); xyz=[p.Base.x,p.Base.y,p.Base.z]
for i,value in enumerate([{x!r},{y!r},{z!r}]):
 if value is not None: xyz[i]=float(value)
rotation=p.Rotation
if {angle is not None!r}: rotation=App.Rotation(App.Vector({rx!r},{ry!r},{rz!r}),{angle!r})
setattr(j,'Detach'+suffix,{True if manual else detach!r})
if {manual!r}: setattr(j,'Placement'+suffix,App.Placement(App.Vector(*xyz),rotation))
if {detach is False!r}: j.Proxy.updateJCSPlacements(j)
App.ActiveDocument.recompute(); p=getattr(j,'Placement'+suffix)
_result_={{'joint':j.Name,'connector':{connector!r},'detach':bool(getattr(j,'Detach'+suffix)),'base':list(p.Base),'rotation':list(p.Rotation.Q)}}""")

    @mcp.tool()
    async def assembly_set_joint_references(
        joint_name: str, first: str, second: str, first_element: str = "",
        second_element: str = "", first_vertex: str = "", second_vertex: str = "",
        solve: bool = True,
    ) -> dict[str, Any]:
        """Reconnect an existing joint to a validated pair of component references.

        Reattaches both connectors to geometry and retains existing offsets.
        Supports repairing a broken reference or swapping component order.
        The pair is validated before mutation; failed solves roll back edits.
        """
        return await run(_JOINT_RUNTIME + f"\n_result_ = assembly_reconnect_joint({joint_name!r}, {first!r}, {second!r}, {first_element!r}, {second_element!r}, {first_vertex!r}, {second_vertex!r}, {solve!r})")

    @mcp.tool()
    async def assembly_set_joint_offset(
        joint_name: str, connector: int = 1, x: float = 0, y: float = 0,
        z: float = 0, rx: float = 0, ry: float = 0, rz: float = 1,
        angle: float = 0, solve: bool = True,
    ) -> dict[str, Any]:
        """Replace an attachment offset (mm/degrees) and reattach to geometry.

        Offset composes with the selected geometry's connector placement.
        For an independent detached placement use assembly_set_joint_connector.
        """
        return await run(_JOINT_RUNTIME + f"\n_result_ = assembly_joint_offset({joint_name!r}, {connector!r}, [{x!r},{y!r},{z!r}], [{rx!r},{ry!r},{rz!r}], {angle!r}, {solve!r})")

    @mcp.tool()
    async def assembly_get_subobject_placement(assembly_name: str, subname: str,
                                                target_object: str = "") -> dict[str, Any]:
        """Resolve a nested Assembly subname with native getPlacementOf()."""
        return await run("""a=App.ActiveDocument.getObject(%r)
if not a: raise ValueError('Assembly not found')
p=a.getPlacementOf(%r,App.ActiveDocument.getObject(%r)) if %r else a.getPlacementOf(%r)
q=p.Rotation; _result_={'assembly':a.Name,'subname':%r,'target_object':%r or None,'base':[p.Base.x,p.Base.y,p.Base.z],'rotation':[q.Q[0],q.Q[1],q.Q[2],q.Q[3]]}""" % (assembly_name, subname, target_object, bool(target_object), subname, subname, target_object))
