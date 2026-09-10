"""Structured Surface workbench tools."""
from collections.abc import Awaitable, Callable
import textwrap
from pathlib import Path
from typing import Any

def _validate_grid(points: list[list[list[float]]]) -> None:
    """Reject ragged grids before sending them to the geometry kernel."""
    import math
    if len(points) < 2 or len(points[0]) < 2:
        raise ValueError("Expected at least a 2x2 grid of 3D points")
    if any(len(row) != len(points[0]) for row in points):
        raise ValueError("Point grid must be rectangular")
    if any(len(p) != 3 or any(not math.isfinite(x) for x in p) for row in points for p in row):
        raise ValueError("Each point must have three finite coordinates")


_GEOMETRY_RUNTIME = "\n".join(
    Path(__file__).with_name("_scripts").joinpath(script).read_text(encoding="utf-8")
    for script in ("surface_geometry.py", "surface_continuity.py", "surface_editing.py", "surface_topology.py")
)

_TRANSACTION_RUNTIME = Path(__file__).with_name('_scripts').joinpath('transaction.py').read_text(encoding='utf-8')

def register_surface_tools(mcp: Any, get_bridge: Callable[[], Awaitable[Any]]) -> None:
    async def run(code: str, get_bridge=get_bridge):
        wrapped = _GEOMETRY_RUNTIME + "\n" + _TRANSACTION_RUNTIME + "\nwith mcp_transaction('Surface MCP operation'):\n" + textwrap.indent(code, "    ")
        result = await (await get_bridge()).execute_python(wrapped)
        return {"success": result.success, "result": result.result, "stdout": result.stdout, "stderr": result.stderr, "error": result.error_traceback}

    @mcp.tool()
    async def surface_create_bspline(
        name: str, poles: list[list[list[float]]], u_degree: int = 3,
        v_degree: int = 3, u_knots: list[float] | None = None,
        v_knots: list[float] | None = None,
        u_multiplicities: list[int] | None = None,
        v_multiplicities: list[int] | None = None,
        weights: list[list[float]] | None = None,
        u_periodic: bool = False, v_periodic: bool = False,
    ) -> dict[str, Any]:
        """Build an exact NURBS surface from a rectangular control-pole grid.

        With no knot data, use open uniform clamped knots. Degrees must be
        smaller than the corresponding pole count. Periodic directions need
        explicit knots/multiplicities. For fitting sampled points instead of
        control poles, use surface_interpolate_points.
        """
        _validate_grid(poles)
        if weights is not None:
            if len(weights) != len(poles) or any(len(row) != len(poles[0]) for row in weights):
                raise ValueError("weights must match the pole grid")
            if any(w <= 0 for row in weights for w in row):
                raise ValueError("weights must be positive")
        def knots(count, degree, values, mults, periodic):
            if not 1 <= degree < count:
                raise ValueError("Degree must be positive and smaller than pole count")
            if values is None and mults is None:
                if periodic: raise ValueError("Periodic surfaces require explicit knot data")
                spans = count - degree
                return [i / spans for i in range(spans + 1)], [degree + 1] + [1] * (spans - 1) + [degree + 1]
            if values is None or mults is None or len(values) != len(mults) or len(values) < 2:
                raise ValueError("Provide matching knot and multiplicity lists")
            if any(a >= b for a,b in zip(values,values[1:])) or any(m < 1 for m in mults):
                raise ValueError("Knots must increase strictly and multiplicities must be positive")
            return values, mults
        uk,um = knots(len(poles),u_degree,u_knots,u_multiplicities,u_periodic)
        vk,vm = knots(len(poles[0]),v_degree,v_knots,v_multiplicities,v_periodic)
        return await run(f"""import Part
if App.ActiveDocument is None: raise ValueError('No active document')
pts=[[App.Vector(*p) for p in row] for row in {poles!r}]
s=Part.BSplineSurface()
args=[pts,{um!r},{vm!r},{uk!r},{vk!r},{u_periodic!r},{v_periodic!r},{u_degree!r},{v_degree!r}]
if {weights is not None!r}: args.append({weights!r})
s.buildFromPolesMultsKnots(*args)
o=App.ActiveDocument.addObject('Part::Feature',{name!r}); o.Shape=s.toShape(); App.ActiveDocument.recompute()
_result_={{'name':o.Name,'faces':len(o.Shape.Faces),'u_degree':s.UDegree,'v_degree':s.VDegree,'pole_counts':[s.NbUPoles,s.NbVPoles],'u_periodic':s.isUPeriodic(),'v_periodic':s.isVPeriodic()}}""")

    @mcp.tool()
    async def surface_interpolate_points(name: str, points: list[list[list[float]]]) -> dict[str, Any]:
        """Interpolate a rectangular grid of sampled points using OCC's fitting API."""
        _validate_grid(points)
        return await run(f"""import Part
s=Part.BSplineSurface(); s.interpolate([[App.Vector(*p) for p in row] for row in {points!r}])
o=App.ActiveDocument.addObject('Part::Feature',{name!r}); o.Shape=s.toShape(); App.ActiveDocument.recompute()
_result_={{'name':o.Name,'u_degree':s.UDegree,'v_degree':s.VDegree,'pole_counts':[s.NbUPoles,s.NbVPoles]}}""")

    @mcp.tool()
    async def surface_create_bezier(name: str, poles: list[list[list[float]]],
                                    weights: list[list[float]] | None = None) -> dict[str, Any]:
        """Create a Bezier surface with the exact supplied control poles and weights."""
        _validate_grid(poles)
        if weights is not None:
            if len(weights) != len(poles) or any(len(row) != len(poles[0]) for row in weights):
                raise ValueError("weights must match the pole grid")
            if any(w <= 0 for row in weights for w in row): raise ValueError("weights must be positive")
        return await run(f"""import Part
p={poles!r}; weights={weights!r}; s=Part.BezierSurface()
s.increase(len(p)-1,len(p[0])-1)
for i,row in enumerate(p,1):
 for j,point in enumerate(row,1):
  s.setPole(i,j,App.Vector(*point))
  if weights is not None: s.setWeight(i,j,weights[i-1][j-1])
o=App.ActiveDocument.addObject('Part::Feature',{name!r}); o.Shape=s.toShape(); App.ActiveDocument.recompute()
_result_={{'name':o.Name,'poles':[[list(v) for v in row] for row in s.getPoles()],'weights':s.getWeights(),'u_degree':s.UDegree,'v_degree':s.VDegree}}""")

    @mcp.tool()
    async def surface_ruled(name: str, edge1_name: str, edge2_name: str) -> dict[str, Any]:
        """Create a ruled surface between two edges or wires."""
        return await run("import Part; a=App.ActiveDocument.getObject(%r).Shape; b=App.ActiveDocument.getObject(%r).Shape; a=a.Edges[0] if a.Edges else a; b=b.Edges[0] if b.Edges else b; s=Part.makeRuledSurface(a,b); o=App.ActiveDocument.addObject('Part::Feature',%r); o.Shape=s; App.ActiveDocument.recompute(); _result_={'name':o.Name,'faces':len(s.Faces)}" % (edge1_name,edge2_name,name))

    @mcp.tool()
    async def surface_fill_boundary(name: str, boundary_names: list[str], method: str = "stretch", tolerance: float = 0.01) -> dict[str, Any]:
        """Create a native parametric GeomFillSurface from 2-4 boundary edges.

        Accepts stretch/stretched, coons or curved; these select the actual
        native FillType. Gordon is a different algorithm and is not accepted.
        tolerance is retained for compatibility; this feature has no tolerance
        property and reports tolerance_applied=False. Use surface_create_filling
        for tolerance-controlled filling with continuity constraints.
        """
        styles = {"stretch":"Stretched", "stretched":"Stretched", "coons":"Coons", "curved":"Curved"}
        key = method.strip().lower()
        if key not in styles: raise ValueError('method must be stretch, coons or curved; Gordon is not provided by GeomFillSurface')
        return await run(f"""refs=[]
for n in {boundary_names!r}:
 obj=App.ActiveDocument.getObject(n)
 if not obj or not hasattr(obj,'Shape') or not obj.Shape.Edges: raise ValueError('Boundary edge object not found: '+n)
 refs.extend((obj,['Edge'+str(i+1)]) for i in range(len(obj.Shape.Edges)))
if not 2<=len(refs)<=4: raise ValueError('GeomFillSurface requires two to four boundary edges')
o=App.ActiveDocument.addObject('Surface::GeomFillSurface',{name!r}); o.BoundaryList=refs; o.FillType={styles[key]!r}
App.ActiveDocument.recompute()
if o.Shape.isNull() or not o.Shape.isValid() or 'Invalid' in o.State: raise ValueError('Native filling failed: '+o.getStatusString())
_result_={{'name':o.Name,'type':o.TypeId,'method':o.FillType,'faces':len(o.Shape.Faces),'is_valid':o.Shape.isValid(),'tolerance_requested':{tolerance!r},'tolerance_applied':False}}""")

    @mcp.tool()
    async def surface_trim(object_name: str, tool_name: str, name: str = "", operation: str = "common") -> dict[str, Any]:
        """Trim/intersect a surface with a tool shape using OCC booleans."""
        op=operation.strip().lower()
        if op not in {"common", "cut", "section"}: raise ValueError("operation must be common, cut or section")
        return await run("""a=App.ActiveDocument.getObject(%r); b=App.ActiveDocument.getObject(%r)
if not a or not b: raise ValueError('Surface or tool object not found')
if %r=='common': shape=a.Shape.common(b.Shape)
elif %r=='cut': shape=a.Shape.cut(b.Shape)
else: shape=a.Shape.section(b.Shape)
out=App.ActiveDocument.addObject('Part::Feature',%r or a.Name+'_trim'); out.Shape=shape; App.ActiveDocument.recompute(); _result_={'name':out.Name,'operation':%r,'shape_type':shape.ShapeType,'faces':len(shape.Faces),'edges':len(shape.Edges),'is_valid':shape.isValid()}""" % (object_name, tool_name, op, op, name, op))

    @mcp.tool()
    async def surface_trim_parameters(
        object_name: str, u_min: float, u_max: float, v_min: float, v_max: float,
        name: str = "", face_index: int = 0, parameter_space: str = "native",
    ) -> dict[str, Any]:
        """Intersect a selected face with a UV rectangle, preserving trim holes.

        face_index is zero-based; parameter_space is native (default) or
        normalized [0,1]. Bounds must be ordered and within the face range.
        Supports analytic surfaces as well as BSplines and Beziers.
        """
        return await run(f"_result_ = surface_parameter_trim({object_name!r}, {face_index!r}, [{u_min!r}, {u_max!r}, {v_min!r}, {v_max!r}], {parameter_space!r}, {name!r})")

    @mcp.tool()
    async def surface_offset(object_name: str, distance: float = 1.0, tolerance: float = 0.01,
                             inter: bool = False, self_inter: bool = False, join: int = 0,
                             fill: bool = False, name: str = "") -> dict[str, Any]:
        """Offset a surface using OCC's native offset algorithm.

        ``join`` follows FreeCAD's API (0=arc, 1=tangent, 2=intersection);
        ``fill`` closes the offset into a solid where supported.
        """
        return await run("""o=App.ActiveDocument.getObject(%r)
if not o or o.Shape.isNull(): raise ValueError('Object has no shape')
shape=o.Shape.makeOffsetShape(float(%r),float(%r),bool(%r),bool(%r),0,int(%r),bool(%r))
out=App.ActiveDocument.addObject('Part::Feature',%r or o.Name+'_offset'); out.Shape=shape; App.ActiveDocument.recompute()
_result_={'name':out.Name,'shape_type':shape.ShapeType,'faces':len(shape.Faces),'solids':len(shape.Solids),'distance':%r,'tolerance':%r,'join':%r,'filled':%r}""" % (object_name, distance, tolerance, inter, self_inter, join, fill, name, distance, tolerance, join, fill))

    @mcp.tool()
    async def surface_to_solid(object_name: str, name: str = "") -> dict[str, Any]:
        """Convert a closed surface shell to a solid."""
        return await run("""import Part
src=App.ActiveDocument.getObject(%r)
if not src or src.Shape.isNull(): raise ValueError('Object has no shape')
if src.Shape.ShapeType=='Solid': shape=src.Shape
else:
 if len(src.Shape.Shells)!=1 or not src.Shape.Shells[0].isClosed(): raise ValueError('Exactly one closed surface shell is required')
 shape=Part.makeSolid(src.Shape.Shells[0])
 if not shape.isValid(): raise ValueError('Native solid construction produced invalid geometry')
out=App.ActiveDocument.addObject('Part::Feature',%r or src.Name+'_solid'); out.Shape=shape; App.ActiveDocument.recompute(); _result_={'name':out.Name,'solids':len(shape.Solids),'is_valid':shape.isValid()}""" % (object_name,name))

    @mcp.tool()
    async def surface_loft(
        name: str, section_names: list[str], solid: bool = False, ruled: bool = False,
        closed: bool = False, max_degree: int = 5, wire_indices: list[int] | None = None,
    ) -> dict[str, Any]:
        """Loft ordered wires with explicit zero-based wire selection per section.

        Single edges are accepted as wire index 0. closed connects the last
        section to the first; solid requires closed profile wires.
        """
        return await run(f"""names = {section_names!r}
if len(names)<2: raise ValueError('Loft requires at least two sections')
indexes = {wire_indices!r} if {wire_indices is not None!r} else [0]*len(names)
if len(indexes)!=len(names): raise ValueError('wire_indices must match section_names')
if not 1<={max_degree!r}<=25: raise ValueError('max_degree must be between 1 and 25')
wires = [surface_wire(n, i) for n,i in zip(names,indexes)]
if {solid!r} and not all(w.isClosed() for w in wires): raise ValueError('Solid loft requires closed profiles')
shape = Part.makeLoft(wires, {solid!r}, {ruled!r}, {closed!r}, {max_degree!r})
_result_ = surface_output(shape, {name!r})
_result_.update(section_count=len(wires), ruled={ruled!r}, closed={closed!r})""")

    @mcp.tool()
    async def surface_sweep(
        name: str, profile_name: str, path_name: str, make_solid: bool = False,
        frenet: bool = False, transition: int = 0,
        profile_wire_index: int = 0, path_wire_index: int = 0,
    ) -> dict[str, Any]:
        """Sweep a selected profile wire along a selected path wire.

        Wire indexes are zero-based. transition: 0=transformed, 1=right
        corners, 2=rounded corners. frenet selects the moving Frenet frame.
        """
        return await run(f"""profile = surface_wire({profile_name!r}, {profile_wire_index!r})
path = surface_wire({path_name!r}, {path_wire_index!r})
if {transition!r} not in (0,1,2): raise ValueError('transition must be 0, 1 or 2')
if {make_solid!r} and not profile.isClosed(): raise ValueError('Solid sweep requires a closed profile')
shape = path.makePipeShell([profile], {make_solid!r}, {frenet!r}, {transition!r})
_result_ = surface_output(shape, {name!r})""")

    @mcp.tool()
    async def surface_reverse(object_name: str, name: str = "") -> dict[str, Any]:
        """Reverse a shape copy while retaining its compound/shell/solid structure."""
        return await run(f"""obj = surface_object({object_name!r})
_result_ = surface_output(obj.Shape.reversed(), {name!r} or obj.Name+'_reversed')""")

    @mcp.tool()
    async def surface_to_nurbs(object_name: str, name: str = "") -> dict[str, Any]:
        """Convert all shape geometry to NURBS, preserving topology and trimming."""
        return await run(f"""obj = surface_object({object_name!r})
_result_ = surface_output(obj.Shape.toNurbs(), {name!r} or obj.Name+'_nurbs')""")

    @mcp.tool()
    async def surface_replace_faces(object_name: str, replacements: list[dict[str, Any]],
                                     name: str = "", tolerance: float = 0.001) -> dict[str, Any]:
        """Replace selected faces in a copied shell/solid, sewing shared boundaries.

        Each replacement has zero-based face_index, object, optional zero-based
        replacement_face_index (default 0), and reverse (default False). Faces
        use document coordinates. Every affected shell must retain its closure
        and face count; solids must remain valid, closed and positively oriented.
        tolerance is in mm. Produces a snapshot, retaining source objects and
        shell/solid structure; output face numbering may change. Replacement
        boundaries must already fit adjacent faces within the sewing tolerance.
        """
        return await run(f"_result_ = surface_replace_faces({object_name!r}, {replacements!r}, {name!r}, {tolerance!r})")

    @mcp.tool()
    async def surface_extract_face(object_name: str, face_index: int = 0, name: str = "") -> dict[str, Any]:
        """Extract one face from a multi-face surface/solid as a new surface."""
        return await run("src=App.ActiveDocument.getObject(%r); i=int(%r);\nif i<0 or i>=len(src.Shape.Faces): raise IndexError('face_index out of range')\nout=App.ActiveDocument.addObject('Part::Feature',%r or src.Name+'_face%d'); out.Shape=src.Shape.Faces[i]; App.ActiveDocument.recompute(); _result_={'name':out.Name,'face_index':i}" % (object_name,face_index,name,face_index))

    @mcp.tool()
    async def surface_extrude(object_name: str, dx: float = 0, dy: float = 0, dz: float = 10, name: str = "") -> dict[str, Any]:
        """Extrude a surface face or wire using FreeCAD's native Shape API."""
        return await run("""o=App.ActiveDocument.getObject(%r)
if not o or o.Shape.isNull(): raise ValueError('Object has no shape')
shape=o.Shape.extrude(App.Vector(%r,%r,%r)); out=App.ActiveDocument.addObject('Part::Feature',%r or o.Name+'_extrude'); out.Shape=shape; App.ActiveDocument.recompute(); _result_={'name':out.Name,'shape_type':shape.ShapeType,'faces':len(shape.Faces),'solids':len(shape.Solids)}""" % (object_name, dx, dy, dz, name))

    @mcp.tool()
    async def surface_revolve(
        object_name: str, axis_x: float = 0, axis_y: float = 0, axis_z: float = 1,
        angle: float = 360, name: str = "", center_x: float = 0,
        center_y: float = 0, center_z: float = 0,
    ) -> dict[str, Any]:
        """Revolve a shape about the specified axis through center_xyz (mm)."""
        return await run(f"""obj = surface_object({object_name!r})
if not math.isfinite({angle!r}) or not 0<abs({angle!r})<=360: raise ValueError('angle must be nonzero and at most 360 degrees')
axis = surface_unit(App.Vector({axis_x!r}, {axis_y!r}, {axis_z!r}))
shape = obj.Shape.revolve(App.Vector({center_x!r}, {center_y!r}, {center_z!r}), axis, {angle!r})
_result_ = surface_output(shape, {name!r} or obj.Name+'_revolve')
_result_['angle'] = {angle!r}""")

    @mcp.tool()
    async def surface_project(
        object_name: str, target_name: str, direction_x: float = 0,
        direction_y: float = 0, direction_z: float = -1,
        perspective: bool = False, name: str = "", face_index: int = 0,
        eye_point: list[float] | None = None,
    ) -> dict[str, Any]:
        """Project an edge or wire onto a zero-based target face.

        Parallel projection uses direction_xyz. Perspective projection
        requires eye_point=[x,y,z] in model coordinates, not a direction.
        """
        return await run(f"""src = surface_object({object_name!r})
target, face = surface_face({target_name!r}, {face_index!r})
if src.Shape.ShapeType not in ('Edge','Wire'): raise ValueError('Projection source must be an Edge or Wire')
if {perspective!r}:
 eye = {eye_point!r}
 if eye is None or len(eye)!=3 or not all(math.isfinite(x) for x in eye): raise ValueError('Perspective projection requires a finite eye_point [x,y,z]')
 shape = face.makePerspectiveProjection(src.Shape, App.Vector(*eye))
else:
 direction = App.Vector({direction_x!r}, {direction_y!r}, {direction_z!r})
 shape = face.makeParallelProjection(src.Shape, surface_unit(direction))
if not shape.Edges: raise ValueError('Projection produced no edges on the target face')
_result_ = surface_output(shape, {name!r} or src.Name+'_projection')
_result_.update(perspective={perspective!r}, face_index={face_index!r})""")

    @mcp.tool()
    async def surface_transform(
        object_name: str, tx: float = 0, ty: float = 0, tz: float = 0,
        rx: float = 0, ry: float = 0, rz: float = 1, angle: float = 0, name: str = "",
    ) -> dict[str, Any]:
        """Apply rotation about the world origin then translation to a shape copy.

        The delta composes with the existing placement; it does not replace it.
        Rotation axis is (rx,ry,rz), angle in degrees, translation in mm.
        """
        return await run(f"""obj = surface_object({object_name!r})
shape = obj.Shape.copy()
delta = App.Placement(App.Vector({tx!r}, {ty!r}, {tz!r}), App.Rotation(App.Vector({rx!r}, {ry!r}, {rz!r}), {angle!r}))
shape.Placement = delta.multiply(shape.Placement)
_result_ = surface_output(shape, {name!r} or obj.Name+'_transformed')
_result_['placement'] = {{'x': shape.Placement.Base.x, 'y': shape.Placement.Base.y, 'z': shape.Placement.Base.z}}""")

    @mcp.tool()
    async def surface_sew(name: str, object_names: list[str], tolerance: float = 0.001,
                          nonmanifold: bool = False, cut_free_edges: bool = True) -> dict[str, Any]:
        """Sew linked faces with the native parametric Surface::Sewing feature."""
        if not object_names or tolerance<=0: raise ValueError('Provide input surfaces and positive tolerance')
        return await run(f"""refs=[]
for n in {object_names!r}:
 obj=App.ActiveDocument.getObject(n)
 if not obj or not hasattr(obj,'Shape') or not obj.Shape.Faces: raise ValueError('Surface object not found: '+n)
 refs.append((obj,['Face'+str(i+1) for i in range(len(obj.Shape.Faces))]))
o=App.ActiveDocument.addObject('Surface::Sewing',{name!r}); o.ShapeList=refs; o.Tolerance={tolerance!r}
o.Nonmanifold={nonmanifold!r}; o.CutFreeEdges={cut_free_edges!r}; App.ActiveDocument.recompute()
if o.Shape.isNull() or not o.Shape.isValid() or 'Invalid' in o.State: raise ValueError('Native sewing failed: '+o.getStatusString())
_result_={{'name':o.Name,'type':o.TypeId,'shells':len(o.Shape.Shells),'faces':len(o.Shape.Faces),'is_closed':o.Shape.isClosed(),'is_valid':o.Shape.isValid()}}""")

    @mcp.tool()
    async def surface_evaluate(
        object_name: str, u: float = 0.5, v: float = 0.5,
        face_index: int = 0, parameter_space: str = "normalized",
    ) -> dict[str, Any]:
        """Evaluate a point on a selected face.

        face_index is zero-based. parameter_space is normalized ([0,1] in
        each face parameter range) or native; there is no automatic guessing.
        Evaluation uses the underlying surface and reports inside_face for
        the trimmed domain. Returned U/V and derivatives use native parameters.
        Curvature signs follow the oriented face normal.
        """
        return await run(f"_result_ = surface_evaluation({object_name!r}, {face_index!r}, {u!r}, {v!r}, {parameter_space!r}, 'point')")

    @mcp.tool()
    async def surface_extract_isocurve(
        object_name: str, direction: str = "U", parameter: float = 0.5,
        name: str = "", face_index: int = 0, parameter_space: str = "normalized",
    ) -> dict[str, Any]:
        """Extract constant-U or constant-V curves, clipped to the trimmed face.

        face_index is zero-based. parameter_space is normalized or native.
        Holes may split the result into multiple edges.
        """
        return await run(f"""obj, face = surface_face({object_name!r}, {face_index!r})
a, b, c, d = face.ParameterRange
direction = {direction!r}.strip().upper()
if direction not in ('U', 'V'): raise ValueError('direction must be U or V')
lo, hi = (a,b) if direction == 'U' else (c,d)
value = surface_parameter({parameter!r}, lo, hi, {parameter_space!r})
if not lo <= value <= hi: raise ValueError('Isocurve parameter is outside selected face range')
curve = face.Surface.uIso(value) if direction == 'U' else face.Surface.vIso(value)
edge = curve.toShape(c,d) if direction == 'U' else curve.toShape(a,b)
shape = edge.common(face)
if shape.isNull() or not shape.Edges or not shape.isValid(): raise ValueError('No valid isocurve within trimmed face')
out = App.ActiveDocument.addObject('Part::Feature', {name!r} or obj.Name+'_'+direction+'Iso')
out.Shape = shape; App.ActiveDocument.recompute()
_result_ = {{'name': out.Name, 'face_index': {face_index!r}, 'direction': direction,
            'parameter': value, 'parameter_space': 'native', 'edges': len(shape.Edges), 'length': shape.Length}}""")

    @mcp.tool()
    async def surface_segment(
        object_name: str, u_min: float, u_max: float, v_min: float, v_max: float,
        name: str = "", face_index: int = 0, parameter_space: str = "native",
    ) -> dict[str, Any]:
        """Intersect a selected face with a UV rectangle, preserving trim holes.

        face_index is zero-based; parameter_space is native (default) or
        normalized [0,1]. Bounds must be ordered and within the face range.
        Supports analytic surfaces as well as BSplines and Beziers.
        """
        return await run(f"_result_ = surface_parameter_trim({object_name!r}, {face_index!r}, [{u_min!r}, {u_max!r}, {v_min!r}, {v_max!r}], {parameter_space!r}, {name!r})")

    @mcp.tool()
    async def surface_extend(object_name: str, u_min: float = 0, u_max: float = 0,
                             v_min: float = 0, v_max: float = 0, name: str = "",
                             face_index: int = 0, tolerance: float = 0.1,
                             sample_u: int = 32, sample_v: int = 32) -> dict[str, Any]:
        """Extend a face using the native parametric Surface::Extend feature.

        Each amount is a fraction of the face's original U/V parameter span
        (0.1 extends that side by 10%), not a distance in mm. Native FreeCAD
        samples the extrapolated surface and approximates it with a BSpline;
        tolerance is the fitting tolerance in mm. The source remains linked.
        """
        import math
        if any(not math.isfinite(x) or not 0<=x<=10 for x in (u_min,u_max,v_min,v_max)):
            raise ValueError('Extension fractions must be finite and between 0 and 10')
        if face_index < 0 or sample_u < 2 or sample_v < 2 or not 0<tolerance<=10:
            raise ValueError('Invalid face index, sample count or fitting tolerance')
        return await run(f"""src=App.ActiveDocument.getObject({object_name!r})
if not src or not hasattr(src,'Shape') or len(src.Shape.Faces)<={face_index!r}: raise ValueError('Source face not found')
o=App.ActiveDocument.addObject('Surface::Extend',{name!r} or src.Name+'_extended')
o.Face=(src,['Face'+str({face_index!r}+1)]); o.ExtendUSymetric=False; o.ExtendVSymetric=False
o.ExtendUNeg={u_min!r}; o.ExtendUPos={u_max!r}; o.ExtendVNeg={v_min!r}; o.ExtendVPos={v_max!r}
o.Tolerance={tolerance!r}; o.SampleU={sample_u!r}; o.SampleV={sample_v!r}
App.ActiveDocument.recompute()
if o.Shape.isNull() or not o.Shape.isValid() or 'Invalid' in o.State: raise ValueError('Native extension failed: '+o.getStatusString())
_result_={{'name':o.Name,'type':o.TypeId,'source':src.Name,'extended':{any(x>0 for x in (u_min,u_max,v_min,v_max))!r},'fractions':[{u_min!r},{u_max!r},{v_min!r},{v_max!r}],'area':o.Shape.Area,'tolerance':o.Tolerance,'method':'sampled BSpline approximation'}}""")

    @mcp.tool()
    async def surface_surface_info(object_name: str, face_index: int = 0) -> dict[str, Any]:
        """Inspect the selected zero-based face surface, bounds and periodicity."""
        return await run(f"_result_ = spline_info({object_name!r}, {face_index!r})")

    @mcp.tool()
    async def surface_control_net(object_name: str, face_index: int = 0) -> dict[str, Any]:
        """Read document-space poles, weights and knots of a zero-based face."""
        return await run(f"_result_ = spline_info({object_name!r}, {face_index!r}, True)")

    @mcp.tool()
    async def surface_check_continuity(
        object_name: str, tolerance: float = 1e-6,
        other_object_name: str = "", face_index: int = 0, other_face_index: int = 1,
        edge_index: int | None = None, other_edge_index: int | None = None,
        samples: int = 21, angular_tolerance_deg: float = 0.1,
        curvature_tolerance: float = 1e-4,
    ) -> dict[str, Any]:
        """Sample G0/G1/G2 across one selected pair of face boundaries.

        Face indexes and edge indexes within each face are zero-based. By
        default compare faces 0 and 1 of object_name. For another single-face
        object set other_object_name and other_face_index=0. Omitted edge
        indexes select the closest complete pair by bidirectional samples.
        G0 tolerance is mm, angular tolerance degrees, curvature tolerance
        1/mm. G1 ignores reversed face winding but reports orientation.
        G2 compares second fundamental forms, including principal directions.
        This checks entire selected edges, not partial overlaps or all seams.
        It is a finite sampling estimate, not a mathematical proof; singular
        differentials yield inconclusive results rather than success.
        """
        return await run(f"_result_ = surface_continuity({object_name!r}, {tolerance!r}, {other_object_name!r}, {face_index!r}, {other_face_index!r}, {edge_index!r}, {other_edge_index!r}, {samples!r}, {angular_tolerance_deg!r}, {curvature_tolerance!r})")

    @mcp.tool()
    async def surface_curvature(
        object_name: str, u: float = 0.5, v: float = 0.5,
        face_index: int = 0, parameter_space: str = "normalized",
    ) -> dict[str, Any]:
        """Evaluate signed principal, mean and Gaussian curvature.

        face_index is zero-based. parameter_space is normalized ([0,1] in
        each face parameter range) or native; there is no automatic guessing.
        Evaluation uses the underlying surface and reports inside_face for
        the trimmed domain. Returned U/V and derivatives use native parameters.
        Curvature signs follow the oriented face normal.
        """
        return await run(f"_result_ = surface_evaluation({object_name!r}, {face_index!r}, {u!r}, {v!r}, {parameter_space!r}, 'curvature')")

    @mcp.tool()
    async def surface_inspect(object_name: str) -> dict[str, Any]:
        """Inspect surface faces, edges, bounds and closure."""
        return await run("o=App.ActiveDocument.getObject(%r); _result_={'name':o.Name,'faces':len(o.Shape.Faces),'edges':len(o.Shape.Edges),'solids':len(o.Shape.Solids),'shells':len(o.Shape.Shells),'is_closed':o.Shape.isClosed(),'is_valid':o.Shape.isValid(),'bounds':{'xmin':o.Shape.BoundBox.XMin,'xmax':o.Shape.BoundBox.XMax,'ymin':o.Shape.BoundBox.YMin,'ymax':o.Shape.BoundBox.YMax,'zmin':o.Shape.BoundBox.ZMin,'zmax':o.Shape.BoundBox.ZMax}}" % object_name)

    @mcp.tool()
    async def surface_capabilities() -> dict[str, Any]:
        """Report native Part/OCC surface methods available in this FreeCAD build."""
        return await run("""import Part
surface=Part.BSplineSurface(); shape=Part.Shape()
methods=[]
for n in dir(surface):
 if n.startswith('_'): continue
 try:
  if callable(getattr(surface,n)): methods.append(n)
 except Exception: pass
previous=App.ActiveDocument; probe=App.newDocument('MCP_SurfaceCapabilityProbe'); features={}
try:
 for typ in ('Surface::Filling','Surface::GeomFillSurface','Surface::Sections','Surface::Extend','Surface::FeatureBlendCurve','Surface::Sewing','Surface::Cut'):
  try:
   obj=probe.addObject(typ,'Probe'); features[typ]={'available':True,'properties':[{'name':p,'type':obj.getTypeIdOfProperty(p),'enum':obj.getEnumerationsOfProperty(p) if obj.getTypeIdOfProperty(p)=='App::PropertyEnumeration' else None} for p in obj.PropertiesList]}
  except Exception as exc: features[typ]={'available':False,'error':str(exc)}
finally:
 App.closeDocument(probe.Name)
 if previous: App.setActiveDocument(previous.Name)
_result_={'freecad_version':App.Version(),'bspline_methods':methods,'native_features':features,'shape_methods':[n for n in dir(shape) if n in ('makeOffsetShape','makeParallelProjection','makePerspectiveProjection','extrude','revolve','section','common','cut','fuse','sewShape')],'has_surface_extend':features['Surface::Extend']['available'],'has_bspline_extend_method':hasattr(surface,'extend')}""")

    @mcp.tool()
    async def surface_intersection(first_name: str, second_name: str, name: str = "") -> dict[str, Any]:
        """Create native section curves between shapes; empty intersections fail.

        Uses OCC section. Use surface_trim(operation='common') for overlapping
        surface regions or solid volumes instead of intersection curves.
        """
        return await run(f"""first = surface_object({first_name!r}); second = surface_object({second_name!r})
shape = first.Shape.section(second.Shape)
if not shape.Edges: raise ValueError('No intersection curves between shapes')
_result_ = surface_output(shape, {name!r} or 'SurfaceIntersection')
_result_['operation'] = 'section'""")

    @mcp.tool()
    async def surface_boundary_info(object_name: str) -> dict[str, Any]:
        """Return edge lengths and closed-wire status for surface boundaries."""
        return await run("o=App.ActiveDocument.getObject(%r); _result_={'name':o.Name,'wires':[{'closed':w.isClosed(),'length':w.Length,'edges':len(w.Edges)} for w in o.Shape.Wires],'edge_lengths':[e.Length for e in o.Shape.Edges]}" % object_name)

    @mcp.tool()
    async def surface_edit_pole(object_name: str, u_index: int, v_index: int,
                                x: float, y: float, z: float, name: str = "", face_index: int = 0) -> dict[str, Any]:
        """Edit a document-space pole on a copied selected face.

        Pole indexes are one-based; face_index is zero-based. Original UV
        boundary loops, including holes, map to the edited surface. Other
        faces are not copied and the source object remains unchanged.
        """
        return await run(f"_result_ = edit_control_net({object_name!r}, {face_index!r}, [{{'u_index': {u_index!r}, 'v_index': {v_index!r}, 'pole': [{x!r}, {y!r}, {z!r}]}}], {name!r})")

    @mcp.tool()
    async def surface_edit_weight(object_name: str, u_index: int, v_index: int,
                                  weight: float, name: str = "", face_index: int = 0) -> dict[str, Any]:
        """Edit a positive finite pole weight, preserving selected-face UV boundaries."""
        return await run(f"_result_ = edit_control_net({object_name!r}, {face_index!r}, [{{'u_index': {u_index!r}, 'v_index': {v_index!r}, 'weight': {weight!r}}}], {name!r})")

    @mcp.tool()
    async def surface_normal(
        object_name: str, u: float = 0.5, v: float = 0.5,
        face_index: int = 0, parameter_space: str = "normalized",
    ) -> dict[str, Any]:
        """Evaluate the oriented face normal.

        face_index is zero-based. parameter_space is normalized ([0,1] in
        each face parameter range) or native; there is no automatic guessing.
        Evaluation uses the underlying surface and reports inside_face for
        the trimmed domain. Returned U/V and derivatives use native parameters.
        Curvature signs follow the oriented face normal.
        """
        return await run(f"_result_ = surface_evaluation({object_name!r}, {face_index!r}, {u!r}, {v!r}, {parameter_space!r}, 'normal')")

    @mcp.tool()
    async def surface_project_point(
        object_name: str, x: float, y: float, z: float, face_index: int = 0,
    ) -> dict[str, Any]:
        """Nearest projection onto the underlying surface of a zero-based face.

        Returns native UV, distance in mm and inside_face; projections may
        fall outside the trimmed face. This does not clamp to its boundary.
        """
        return await run(f"""obj, face = surface_face({object_name!r}, {face_index!r})
point = App.Vector({x!r}, {y!r}, {z!r})
if not all(math.isfinite(a) for a in point): raise ValueError('Point must be finite')
u, v = face.Surface.projectPoint(point, 'LowerDistanceParameters')
p = face.valueAt(u, v)
_result_ = {{'name': obj.Name, 'face_index': {face_index!r}, 'u': u, 'v': v,
            'parameter_space': 'native', 'point': list(p), 'distance_mm': (p-point).Length,
            'inside_face': bool(face.isPartOfDomain(u, v)), 'projection_target': 'underlying_surface'}}""")

    @mcp.tool()
    async def surface_exchange_uv(object_name: str, name: str = "", face_index: int = 0) -> dict[str, Any]:
        """Swap U/V on a copied face, mapping trim loops and preserving physical normal."""
        return await run(f"_result_ = exchange_surface_uv({object_name!r}, {face_index!r}, {name!r})")

    @mcp.tool()
    async def surface_reparameterize(object_name: str, u_poles: int = 4, v_poles: int = 4,
                                     tolerance: float = 1e-6, name: str = "", face_index: int = 0) -> dict[str, Any]:
        """Refit a selected face by interpolation of a regular UV sample grid.

        Legacy u_poles/v_poles parameters specify sample counts; actual pole
        counts are returned. This approximation may change geometry. A denser
        independent grid checks tolerance in mm; it is not a global proof.
        Trim loops are mapped into the fitted UV domain. For exact affine
        reparameterization use surface_set_parameter_range instead.
        """
        return await run(f"_result_ = refit_surface({object_name!r}, {face_index!r}, {u_poles!r}, {v_poles!r}, {tolerance!r}, {name!r})")

    @mcp.tool()
    async def surface_tangent(
        object_name: str, u: float = 0.5, v: float = 0.5,
        face_index: int = 0, parameter_space: str = "normalized",
    ) -> dict[str, Any]:
        """Evaluate unit U/V tangent directions.

        face_index is zero-based. parameter_space is normalized ([0,1] in
        each face parameter range) or native; there is no automatic guessing.
        Evaluation uses the underlying surface and reports inside_face for
        the trimmed domain. Returned U/V and derivatives use native parameters.
        Curvature signs follow the oriented face normal.
        """
        return await run(f"_result_ = surface_evaluation({object_name!r}, {face_index!r}, {u!r}, {v!r}, {parameter_space!r}, 'tangent')")

    @mcp.tool()
    async def surface_derivative(
        object_name: str, u: float = 0.5, v: float = 0.5,
        u_order: int = 1, v_order: int = 0,
        face_index: int = 0, parameter_space: str = "normalized",
    ) -> dict[str, Any]:
        """Evaluate a derivative with respect to native U/V.

        face_index is zero-based. parameter_space is normalized ([0,1] in
        each face parameter range) or native; there is no automatic guessing.
        Evaluation uses the underlying surface and reports inside_face for
        the trimmed domain. Returned U/V and derivatives use native parameters.
        Curvature signs follow the oriented face normal.
        """
        return await run(f"_result_ = surface_evaluation({object_name!r}, {face_index!r}, {u!r}, {v!r}, {parameter_space!r}, 'derivative', {u_order!r}, {v_order!r})")

    @mcp.tool()
    async def surface_get_pole(object_name: str, u_index: int, v_index: int, face_index: int = 0) -> dict[str, Any]:
        """Read a document-space pole and weight; face zero-based, poles one-based."""
        return await run(f"_result_ = read_pole({object_name!r}, {face_index!r}, {u_index!r}, {v_index!r})")

    @mcp.tool()
    async def surface_set_knot(object_name: str, direction: str, index: int, value: float,
                               multiplicity: int | None = None, name: str = "", face_index: int = 0) -> dict[str, Any]:
        """Set a one-based native spline knot and optionally increase multiplicity.

        Copy the selected zero-based face with its UV trim loops. Knot movement
        can change geometry; changed parameter bounds map boundary UV affinely.
        """
        return await run(f"_result_ = edit_knot({object_name!r}, {face_index!r}, 'set', {direction!r}, {index!r}, {value!r}, {multiplicity!r}, 0.0, {name!r})")

    @mcp.tool()
    async def surface_set_periodic(object_name: str, direction: str, periodic: bool = True,
                                   origin_index: int | None = None, name: str = "", face_index: int = 0) -> dict[str, Any]:
        """Change native spline periodicity on a selected copied face.

        Closing requires a closed surface. Optional one-based origin_index
        selects a periodic knot origin. Removing periodicity segments the
        selected domain before opening it, retaining its UV trim boundaries.
        """
        return await run(f"_result_ = set_surface_periodic({object_name!r}, {face_index!r}, {direction!r}, {periodic!r}, {origin_index!r}, {name!r})")

    @mcp.tool()
    async def surface_insert_knot(object_name: str, direction: str, parameter: float,
                                  multiplicity: int = 1, tolerance: float = 0.0,
                                  name: str = "", face_index: int = 0) -> dict[str, Any]:
        """Insert a native U/V knot while retaining selected-face trim loops."""
        return await run(f"_result_ = edit_knot({object_name!r}, {face_index!r}, 'insert', {direction!r}, None, {parameter!r}, {multiplicity!r}, {tolerance!r}, {name!r})")

    @mcp.tool()
    async def surface_remove_knot(object_name: str, direction: str, index: int,
                                  multiplicity: int = 0, tolerance: float = 1e-6,
                                  name: str = "", face_index: int = 0) -> dict[str, Any]:
        """Reduce knot multiplicity; zero removes the knot entirely.

        Index is one-based, face_index zero-based. Only interior nonperiodic
        knots may be removed. Native accuracy failure creates no output.
        Selected-face UV trim loops remain on the resulting surface.
        """
        return await run(f"_result_ = edit_knot({object_name!r}, {face_index!r}, 'remove', {direction!r}, {index!r}, None, {multiplicity!r}, {tolerance!r}, {name!r})")

    @mcp.tool()
    async def surface_increase_degree(object_name: str, u_degree: int, v_degree: int,
                                      name: str = "", face_index: int = 0) -> dict[str, Any]:
        """Elevate BSpline/Bezier degree, preserving selected-face UV boundaries."""
        return await run(f"_result_ = increase_surface_degree({object_name!r}, {face_index!r}, {u_degree!r}, {v_degree!r}, {name!r})")

    @mcp.tool()
    async def surface_edit_control_net(object_name: str, edits: list[dict[str, Any]],
                                       name: str = "", face_index: int = 0) -> dict[str, Any]:
        """Edit poles/weights atomically on a copied selected face.

        Each edit has one-based u_index/v_index, document-space pole [x,y,z]
        and/or positive finite weight. face_index is zero-based. Native UV
        boundary curves retain holes and trim loops on the deformed surface.
        Returns only that face; source and other faces remain unchanged.
        """
        return await run(f"_result_ = edit_control_net({object_name!r}, {face_index!r}, {edits!r}, {name!r})")

    @mcp.tool()
    async def surface_create_sections(name: str, sections: list[dict[str, str]]) -> dict[str, Any]:
        """Create Surface::Sections from ordered {object, element: EdgeN} references."""
        if len(sections)<2: raise ValueError('At least two section curves required')
        return await run(f"""refs=[]
for ref in {sections!r}:
 o=App.ActiveDocument.getObject(ref['object']); element=ref.get('element','Edge1')
 if not o or not hasattr(o,'Shape') or o.Shape.getElement(element).ShapeType!='Edge': raise ValueError('Section edge not found')
 refs.append((o,[element]))
o=App.ActiveDocument.addObject('Surface::Sections',{name!r}); o.NSections=refs; App.ActiveDocument.recompute()
if o.Shape.isNull() or not o.Shape.isValid() or 'Invalid' in o.State: raise ValueError('Native sections failed: '+o.getStatusString())
_result_={{'name':o.Name,'type':o.TypeId,'section_count':len(refs),'faces':len(o.Shape.Faces)}}""")

    @mcp.tool()
    async def surface_create_filling(
        name: str, boundaries: list[dict[str, Any]],
        unbound_edges: list[dict[str, Any]] | None = None,
        free_faces: list[dict[str, Any]] | None = None,
        points: list[dict[str, str]] | None = None,
        initial_face: dict[str, str] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a parametric Surface::Filling with native continuity constraints.

        boundaries/unbound_edges entries: object, edge (EdgeN), optional face
        (FaceN on the same object) and continuity (C0, G1, G2). G1/G2 require
        a support face. Boundaries must be consecutive. free_faces entries:
        object, face, continuity. points entries: object, element (VertexN).
        initial_face: object, element (FaceN). settings accepts native Degree,
        PointsOnCurve, Iterations, Anisotropy, Tolerance2d/3d, TolAngular,
        TolCurvature, MaximumDegree and MaximumSegments.
        """
        if not boundaries: raise ValueError('Boundary edges required')
        return await run(f"""doc=App.ActiveDocument
orders={{'C0':0,'G1':1,'G2':3}}
def resolve(ref,kind,key,default):
 o=doc.getObject(ref['object']); sub=ref.get(key,default)
 if not o or not hasattr(o,'Shape') or o.Shape.getElement(sub).ShapeType!=kind: raise ValueError('Invalid '+kind+' reference: '+str(ref))
 return o,sub
def edge_constraints(items):
 refs=[]; faces=[]; continuity=[]
 for ref in items:
  o,edge=resolve(ref,'Edge','edge','Edge1'); face=ref.get('face',''); order=ref.get('continuity','C0').upper()
  if order not in orders: raise ValueError('Continuity must be C0, G1 or G2')
  if order!='C0' and not face: raise ValueError('G1/G2 requires a supporting face')
  if face and o.Shape.getElement(face).ShapeType!='Face': raise ValueError('Support face not found')
  refs.append((o,[edge])); faces.append(face); continuity.append(orders[order])
 return refs,faces,continuity
b,bf,bo=edge_constraints({boundaries!r}); u,uf,uo=edge_constraints({unbound_edges or []!r})
frefs=[]; forders=[]
for ref in {free_faces or []!r}:
 o,face=resolve(ref,'Face','face','Face1'); order=ref.get('continuity','C0').upper()
 if order not in orders: raise ValueError('Continuity must be C0, G1 or G2')
 frefs.append((o,[face])); forders.append(orders[order])
prefs=[]
for ref in {points or []!r}:
 o,vertex=resolve(ref,'Vertex','element','Vertex1'); prefs.append((o,[vertex]))
initial={initial_face!r}; init=None
if initial:
 o,face=resolve(initial,'Face','element','Face1'); init=(o,[face])
params={settings or {}!r}
allowed={{'Degree','PointsOnCurve','Iterations','Anisotropy','Tolerance2d','Tolerance3d','TolAngular','TolCurvature','MaximumDegree','MaximumSegments'}}
if set(params)-allowed: raise ValueError('Unknown filling settings: '+str(sorted(set(params)-allowed)))
for key,value in params.items():
 if key=='Anisotropy':
  if type(value) is not bool: raise ValueError('Anisotropy requires a boolean')
 elif value<=0: raise ValueError('Filling settings must be positive')
o=doc.addObject('Surface::Filling',{name!r}); o.BoundaryEdges=b; o.BoundaryFaces=bf; o.BoundaryOrder=bo
o.UnboundEdges=u; o.UnboundFaces=uf; o.UnboundOrder=uo; o.FreeFaces=frefs; o.FreeOrder=forders; o.Points=prefs
if init: o.InitialFace=init
for key,value in params.items(): setattr(o,key,value)
doc.recompute()
if o.Shape.isNull() or not o.Shape.isValid() or 'Invalid' in o.State: raise ValueError('Native filling failed: '+o.getStatusString())
_result_={{'name':o.Name,'type':o.TypeId,'faces':len(o.Shape.Faces),'boundary_count':len(b),'boundary_orders':bo,'area':o.Shape.Area}}""")

    @mcp.tool()
    async def surface_blend_curve(
        name: str, start_object: str, end_object: str,
        start_edge: str = 'Edge1', end_edge: str = 'Edge1',
        start_parameter: float = 1.0, end_parameter: float = 0.0,
        start_continuity: int = 2, end_continuity: int = 2,
        start_size: float = 1.0, end_size: float = 1.0,
    ) -> dict[str, Any]:
        """Join two referenced edges with a native parametric blend curve.

        Parameters are fractions of each edge's range in [0,1]. Continuity
        orders 0..25 follow native geometric continuity, with their sum <=23
        (OCC Bezier maximum degree). Signed sizes scale endpoint derivatives.
        """
        if not 0<=start_parameter<=1 or not 0<=end_parameter<=1:
            raise ValueError('Edge parameters must be in [0,1]')
        if min(start_continuity,end_continuity)<0 or start_continuity+end_continuity>23:
            raise ValueError('Continuity orders must be nonnegative and sum to at most 23')
        if not -100<=start_size<=100 or not -100<=end_size<=100:
            raise ValueError('Derivative sizes must be in [-100,100]')
        return await run(f"""doc=App.ActiveDocument; refs=[]
for obj_name,element in [({start_object!r},{start_edge!r}),({end_object!r},{end_edge!r})]:
 obj=doc.getObject(obj_name)
 if not obj or not hasattr(obj,'Shape') or obj.Shape.getElement(element).ShapeType!='Edge': raise ValueError('Blend support edge not found')
 refs.append((obj,[element]))
o=doc.addObject('Surface::FeatureBlendCurve',{name!r}); o.StartEdge=refs[0]; o.EndEdge=refs[1]
o.StartContinuity=0; o.EndContinuity=0
o.StartContinuity={start_continuity!r}; o.EndContinuity={end_continuity!r}
o.StartParameter={start_parameter!r}; o.EndParameter={end_parameter!r}; o.StartSize={start_size!r}; o.EndSize={end_size!r}
doc.recompute()
if o.Shape.isNull() or not o.Shape.isValid() or 'Invalid' in o.State: raise ValueError('Native blend failed: '+o.getStatusString())
_result_={{'name':o.Name,'type':o.TypeId,'length':o.Shape.Length,'continuity':[o.StartContinuity,o.EndContinuity]}}""")

    @mcp.tool()
    async def surface_set_parameter_range(
        object_name: str, u_min: float = 0.0, u_max: float = 1.0,
        v_min: float = 0.0, v_max: float = 1.0,
        name: str = "", face_index: int = 0,
    ) -> dict[str, Any]:
        """Affinely rescale native BSpline knots without changing geometry.

        The selected zero-based face's exact UV boundary curves are mapped
        into the new underlying-surface bounds, retaining holes. This returns
        a single face and preserves the source object. Unequal U/V scaling
        is supported. For approximation onto a sample grid, use
        surface_reparameterize instead.
        """
        return await run(f"_result_ = set_parameter_range({object_name!r}, {face_index!r}, [{u_min!r}, {u_max!r}, {v_min!r}, {v_max!r}], {name!r})")

    @mcp.tool()
    async def surface_create_cut(name: str, object_name: str, tool_name: str) -> dict[str, Any]:
        """Create a parametric native Surface::Cut linked to two Part features.

        Recomputes when either source changes. Unlike surface_trim's snapshot
        boolean, ShapeList keeps the source links. Empty/invalid results fail
        atomically. Whole shapes are used, including solids and trimmed faces.
        """
        return await run(f"""base=surface_object({object_name!r}); tool=surface_object({tool_name!r})
if base is tool: raise ValueError('Cut requires two different source objects')
if not base.isDerivedFrom('Part::Feature') or not tool.isDerivedFrom('Part::Feature'): raise ValueError('Native Surface::Cut requires Part::Feature sources')
out=App.ActiveDocument.addObject('Surface::Cut',{name!r})
out.ShapeList=[base,tool]
App.ActiveDocument.recompute()
if not out.isValid() or out.Shape.isNull() or not out.Shape.isValid() or not out.Shape.Faces: raise ValueError('Native Surface::Cut failed or has no resulting faces: '+out.getStatusString())
_result_={{'name':out.Name,'type':out.TypeId,'sources':[ref[0].Name for ref in out.ShapeList],
 'faces':len(out.Shape.Faces),'area':out.Shape.Area,'volume':out.Shape.Volume,'is_valid':out.Shape.isValid()}}""")

    @mcp.tool()
    async def surface_tessellate(
        object_name: str, linear_deflection: float = 0.1,
        angular_deflection: float = 0.5, relative: bool = False,
        face_index: int | None = None, name: str = "", include_mesh: bool = False,
    ) -> dict[str, Any]:
        """Mesh a native surface/shape with OCC through MeshPart.meshFromShape.

        linear_deflection is mm when relative=False; angular_deflection is
        radians. Relative mode scales deflection by edge sizes as OCC defines.
        Omitted face_index meshes the whole shape; otherwise select zero-based
        face. Returns a Mesh::Feature snapshot, preserving holes/placements.
        include_mesh optionally returns vertices and triangle index triples.
        """
        return await run(f"""import MeshPart
obj=surface_object({object_name!r})
shape=obj.Shape if {face_index!r} is None else surface_face({object_name!r},{face_index!r})[1]
linear=finite_number({linear_deflection!r},'linear_deflection'); angular=finite_number({angular_deflection!r},'angular_deflection')
if linear<=0 or not 0<angular<math.pi: raise ValueError('Deflection must be positive; angle must be below pi radians')
mesh=MeshPart.meshFromShape(Shape=shape,LinearDeflection=linear,AngularDeflection=angular,Relative={relative!r})
if mesh.CountFacets==0: raise ValueError('Native tessellation produced no facets')
mesh_object=App.ActiveDocument.addObject('Mesh::Feature',{name!r} or obj.Name+'_mesh');mesh_object.Mesh=mesh
App.ActiveDocument.recompute()
_result_={{'name':mesh_object.Name,'type':mesh_object.TypeId,'source':obj.Name,'face_index':{face_index!r},
 'vertices':mesh.CountPoints,'triangles':mesh.CountFacets,'area':mesh.Area,'volume':mesh.Volume,
 'is_solid':mesh.isSolid(),'linear_deflection':linear,'angular_deflection_rad':angular,'relative':{relative!r}}}
if {include_mesh!r}:
 vertices,triangles=mesh.Topology
 _result_['mesh']={{'vertices':[list(v) for v in vertices],'triangles':[list(t) for t in triangles]}}""")

    @mcp.tool()
    async def surface_from_mesh(
        mesh_name: str, tolerance: float = 0.001, make_solid: bool = False,
        name: str = "",
    ) -> dict[str, Any]:
        """Convert native mesh triangles into a faceted Part shape using OCC.

        This produces planar triangle faces, not a fitted smooth surface.
        tolerance is mm for sewing. make_solid requires a closed, manifold
        single shell; invalid/open meshes fail atomically. Source placement
        is applied to the output in document coordinates.
        """
        return await run(f"""doc=App.ActiveDocument; obj=doc.getObject({mesh_name!r}) if doc else None
if obj is None or not obj.isDerivedFrom('Mesh::Feature') or obj.Mesh.CountFacets==0: raise ValueError('Nonempty Mesh::Feature required')
tolerance=finite_number({tolerance!r},'tolerance')
if tolerance<=0: raise ValueError('tolerance must be positive')
mesh=obj.Mesh
if {make_solid!r} and not mesh.isSolid(): raise ValueError('Solid conversion requires a closed manifold mesh')
shape=Part.Shape();shape.makeShapeFromMesh(mesh.Topology,tolerance)
if {make_solid!r}:
 if len(shape.Shells)!=1 or not shape.Shells[0].isClosed(): raise ValueError('Mesh does not form a single closed shell')
 shape=Part.makeSolid(shape.Shells[0])
_result_=surface_output(shape,{name!r} or obj.Name+'_faceted')
_result_.update(source=obj.Name,faceted=True,smooth_surface_fitted=False,tolerance=tolerance)""")
