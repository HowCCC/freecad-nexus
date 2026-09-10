"""Native geometry setup plus dedicated Assembly, CAM and Surface MCP workflows."""
from pathlib import Path


async def generate(call, output: Path):
    async def code(source):
        return await call('execute_python', code=source)

    async def finish(stem, visible, exports):
        """Save, reopen and check exported solids; capture a clean native viewport."""
        await code(f'''
import FreeCADGui as Gui
from pathlib import Path
out = Path({str(output)!r})
doc = App.ActiveDocument
for obj in doc.Objects:
    if hasattr(obj, 'ViewObject'):
        obj.ViewObject.Visibility = False
for name in {visible!r}:
    doc.getObject(name).ViewObject.Visibility = True
for obj in doc.Objects:
    if 'Deviation' in obj.ViewObject.PropertiesList:
        obj.ViewObject.Deviation = 0.05
Gui.activeDocument().activeView().viewAxonometric()
Gui.activeDocument().activeView().fitAll()
Gui.updateGui()
_result_=True
''')
        return await code(f'''
import Part, FreeCADGui as Gui
from pathlib import Path
out = Path({str(output)!r})
doc = App.ActiveDocument
Gui.activeDocument().activeView().fitAll()
Gui.updateGui()
Gui.activeDocument().activeView().saveImage(str(out / {stem + '.png'!r}), 1600, 1000, 'White')
exported = [doc.getObject(n) for n in {exports!r}]
assert all(o.Shape.isValid() and len(o.Shape.Solids) > 0 for o in exported)
Part.export(exported, str(out / {stem + '.step'!r}))
expected = {{o.Name: o.Shape.Volume for o in exported}}
filename = str(out / {stem + '.FCStd'!r})
doc.recompute()
doc.saveAs(filename)
App.closeDocument(doc.Name)
doc = App.openDocument(filename)
for name, volume in expected.items():
    obj = doc.getObject(name)
    assert obj.Shape.isValid() and abs(obj.Shape.Volume - volume) < max(1e-5, volume * 1e-8)
_result_ = {{'cad': {stem + '.FCStd'!r}, 'step': {stem + '.step'!r}, 'image': {stem + '.png'!r}, 'saved_reopened': True, 'volumes_mm3': expected}}
''')

    # Rotary/linear inspection head: guide frame + translating, rotating ram.
    await code('''
import Part, math
App.newDocument('InspectionActuator')
doc=App.ActiveDocument
V=App.Vector
base=Part.makeBox(100,80,12,V(-50,-40,0))
for x in (-38,38):
 for y in (-28,28):
  base=base.cut(Part.makeCylinder(4.5,12,V(x,y,0)))
frame=base.fuse(Part.makeBox(100,80,10,V(-50,-40,252)))
for x in (-30,30):
 for y in (-20,20):
  frame=frame.fuse(Part.makeCylinder(5,240,V(x,y,12)))
frame=frame.fuse(Part.makeCylinder(18,26,V(0,0,12)))
frame=frame.cut(Part.makeCylinder(9,38,V(0,0,0)))
f=doc.addObject('Part::Feature','FrameSource'); f.Shape=frame
f.Label='Guide frame · 100 × 80 × 262 mm'
f.ViewObject.ShapeColor=(0.65,0.69,0.74)
ram=Part.makeCylinder(8,150,V(0,0,25)).fuse(Part.makeCylinder(22,18,V(0,0,60)))
ram=ram.fuse(Part.makeBox(64,22,10,V(-32,-11,78)))
for x in (-25,25):
 ram=ram.cut(Part.makeCylinder(3.3,10,V(x,0,78)))
f=doc.addObject('Part::Feature','RamSource'); f.Shape=ram
f.Label='Rotary inspection head · 60 mm stroke'
f.ViewObject.ShapeColor=(0.08,0.43,0.65)
doc.recompute()
_result_=True
''')
    await call('assembly_create', name='Actuator')
    for source, instance in [('FrameSource', 'Frame'), ('RamSource', 'Ram')]:
        await call('assembly_insert_component', assembly_name='Actuator', source_name=source, instance_name=instance)
    await call('assembly_ground_component', assembly_name='Actuator', component_name='Frame')
    joint = await call('assembly_add_constraint', assembly_name='Actuator', constraint_type='Cylindrical', first='Frame', second='Ram')
    sim = await call('assembly_create_simulation', assembly_name='Actuator', end=2, step=.05)
    for kind, formula in [('Linear', '30*time'), ('Angular', 'pi/4*time')]:
        await call('assembly_add_motion', simulation_name=sim['name'], joint_name=joint['name'], motion_type=kind, formula=formula)
    frames = await call('assembly_run_simulation', simulation_name=sim['name'])
    await call('assembly_get_frame', assembly_name='Actuator', frame=0)
    await code('App._nexus_demo_start=App.ActiveDocument.Ram.Placement.copy()\n_result_=True')
    await call('assembly_get_frame', assembly_name='Actuator', frame=frames['frames'] - 1)
    measured = await code('''
p=App.ActiveDocument.Ram.Placement
stroke=p.Base.z-App._nexus_demo_start.Base.z
angle=(App._nexus_demo_start.Rotation.inverted()*p.Rotation).getYawPitchRoll()[0]
assert abs(stroke-60)<1e-5 and abs(angle-90)<1e-5
_result_={'stroke_mm':stroke,'rotation_degrees':angle}
''')
    bom = await call('assembly_create_bom', assembly_name='Actuator', name='ActuatorBOM')
    await call('assembly_export_bom_csv', bom_name=bom['name'], file_path=str(output/'actuator-bom.csv'))
    await call('assembly_create_exploded_view', assembly_name='Actuator', name='ServiceView')
    await call('assembly_add_exploded_step', view_name='ServiceView', component_name='Ram', dx=125, dy=0, dz=0)
    exploded = await call('assembly_export_exploded_shape', view_name='ServiceView', name='ServiceGeometry')
    # Capture exploded service geometry separately; save the native assembly at midstroke.
    await code(f'''
import FreeCADGui as Gui
for obj in App.ActiveDocument.Objects:
 obj.ViewObject.Visibility = False
App.ActiveDocument.getObject({exploded['name']!r}).ViewObject.Visibility = True
Gui.activeDocument().activeView().viewAxonometric(); Gui.activeDocument().activeView().fitAll(); Gui.updateGui()
_result_=True
''')
    await code(f"import FreeCADGui as Gui\nGui.activeDocument().activeView().saveImage({str(output/'actuator-service.png')!r},1600,1000,'White')\n_result_=True")
    frames = await call('assembly_run_simulation', simulation_name=sim['name'])
    await call('assembly_get_frame', assembly_name='Actuator', frame=frames['frames']//2)
    actuator = await finish('inspection-actuator', ['Actuator','Frame','Ram'], ['Frame','Ram'])
    actuator.update(measured, simulation=sim['name'], frames=frames['frames'], bom='actuator-bom.csv', service_image='actuator-service.png')

    # Aluminium robot/camera mounting plate, with two pockets and four clearance holes.
    faces = await code('''
import Part
from pathlib import Path
from Path.Tool.camassets import user_asset_store
App.newDocument('MountingPlate')
doc=App.ActiveDocument
V=App.Vector
shape=Part.makeBox(120,80,12)
for x in (24,70):
 shape=shape.cut(Part.makeBox(26,40,7,V(x,20,6)))
for x in (10,110):
 for y in (10,70):
  shape=shape.cut(Part.makeCylinder(3.3,12,V(x,y,0)))
f=doc.addObject('Part::Feature','Plate'); f.Shape=shape
f.Label='Aluminium mounting plate · 120 × 80 × 12 mm'
f.ViewObject.ShapeColor=(0.72,0.76,0.81)
doc.recompute()
_result_={'floors':['Face'+str(i) for i,f in enumerate(shape.Faces,1) if abs(f.CenterOfMass.z-6)<1e-7 and type(f.Surface).__name__=='Plane' and abs(f.normalAt(0,0).z)>.99], 'holes':['Face'+str(i) for i,f in enumerate(shape.Faces,1) if type(f.Surface).__name__=='Cylinder']}
''')
    await code(f'from pathlib import Path\nfrom Path.Tool.camassets import user_asset_store\nuser_asset_store.set_dir(Path({str(output / "tool-assets")!r}))\n_result_=True')
    await call('cam_create_job', name='PlateJob', model_names=['Plate'])
    await call('cam_set_stock', job_name='PlateJob', stock_type='FromBase', dimensions={'ExtXneg': 2, 'ExtXpos': 2, 'ExtYneg': 2, 'ExtYpos': 2, 'ExtZneg': 0, 'ExtZpos': 1})
    endmill = await call('cam_add_tool_controller', job_name='PlateJob', spindle_speed=12000, horizontal_feed=650, vertical_feed=180, tool={'Diameter': '6 mm'})
    drill = await call('cam_add_tool_controller', job_name='PlateJob', tool_number=2, create_new=True, tool_asset='drill.fcstd', spindle_speed=4500, horizontal_feed=250, vertical_feed=180, tool={'Diameter': '6.6 mm'})
    operations=[]
    for kind, base, tool, params in [
        ('PocketShape', faces['floors'], endmill['name'], {'StartDepth':'12 mm','FinalDepth':'6 mm','StepDown':'2 mm'}),
        ('Drilling', faces['holes'], drill['name'], {'StartDepth':'12 mm','FinalDepth':'-0.5 mm'}),
        ('Profile', [], endmill['name'], {'StartDepth':'12 mm','FinalDepth':'0 mm','StepDown':'3 mm'}),
    ]:
        op=await call('cam_add_operation', job_name='PlateJob', operation=kind, base_object='Plate' if base else '', base_subelements=base, tool_controller_name=tool, parameters=params)
        assert op['path_ready']
        stats=await call('cam_simulate_toolpath', operation_name=op['name'])
        assert stats['cutting_command_count']>0
        operations.append({'name':op['name'],'kind':kind,'statistics':stats})
    valid=await call('cam_validate_job', job_name='PlateJob', recompute=True)
    assert valid['valid']
    await call('cam_generate_gcode', job_name='PlateJob', file_path=str(output/'mounting-plate.nc'), post_processor='refactored_linuxcnc', post_processor_args='--no-header')
    report=await call('cam_generate_setup_report', job_name='PlateJob', file_path=str(output/'mounting-plate-setup.html'), include_images=False)
    plate=await finish('mounting-plate', ['Plate'] + [op['name'] for op in operations], ['Plate'])
    plate.update(operations=operations, job_valid=True, gcode='mounting-plate.nc', setup_report='mounting-plate-setup.html')

    # Hollow sensor housing: deform interior NURBS poles, keep perimeter fixed,
    # then sew the patch back into the solid with ports/cavity retained.
    source=await code('''
import Part
App.newDocument('SensorHousing')
doc=App.ActiveDocument
V=App.Vector
shape=Part.makeBox(120,80,24).cut(Part.makeBox(114,74,21,V(3,3,-1)))
# Three circular connector ports in the front wall; vent slots at the rear.
for x in (26,60,94):
 shape=shape.cut(Part.makeCylinder(4,5,V(x,-1,11),V(0,1,0)))
for x in range(22,100,12):
 shape=shape.cut(Part.makeBox(6,5,3,V(x,77,10)))
o=doc.addObject('Part::Feature','HousingBlank');o.Shape=shape
assert shape.isValid() and len(shape.Solids)==1
top=next(i for i,f in enumerate(shape.Faces) if abs(f.CenterOfMass.z-24)<1e-7)
_result_={'top':top,'volume':shape.Volume}
''')
    nurbs=await call('surface_to_nurbs', object_name='HousingBlank')
    elevated=await call('surface_increase_degree', object_name=nurbs['name'], face_index=source['top'], u_degree=3, v_degree=3)
    edits=await code(f'''
f=App.ActiveDocument.getObject({elevated['name']!r}).Shape.Faces[0]
_result_=[{{'u_index':u,'v_index':v,'pole':[f.Surface.getPole(u,v).x,f.Surface.getPole(u,v).y,f.Surface.getPole(u,v).z+12]}} for u in (2,3) for v in (2,3)]
''')
    patch=await call('surface_edit_control_net', object_name=elevated['name'], edits=edits)
    center=await call('surface_evaluate', object_name=patch['name'], u=.5, v=.5)
    body=await call('surface_replace_faces', object_name='HousingBlank', replacements=[{'face_index':source['top'],'object':patch['name']}], name='CrownedHousing')
    assert body['is_valid'] and body['solids']==1
    assert abs(body['volume']-source['volume']-28800)<.01
    # Add mounting bosses and a detached removable bottom plate as industrial detail.
    await code(f'''
import Part
V=App.Vector
doc=App.ActiveDocument
h=doc.getObject({body['name']!r})
h.Label='Crowned sensor housing · preserved perimeter and ports'
h.ViewObject.ShapeColor=(0.08,0.4,0.55)
for x in (10,110):
 for y in (10,70):
  boss=Part.makeCylinder(5,17,V(x,y,3)).cut(Part.makeCylinder(1.7,17,V(x,y,3)))
  h.Shape=h.Shape.fuse(boss)
assert h.Shape.isValid() and len(h.Shape.Solids)==1
lid=Part.makeBox(120,80,3,V(0,100,0))
for x in (10,110):
 for y in (110,170):
  lid=lid.cut(Part.makeCylinder(1.8,3,V(x,y,0)))
o=doc.addObject('Part::Feature','BottomPlate');o.Shape=lid
o.Label='Removable bottom plate · M3 screw clearance'
o.ViewObject.ShapeColor=(0.68,0.72,0.77)
doc.recompute()
_result_=True
''')
    isolines=[]
    for direction in ('U','V'):
        for parameter in (.2,.4,.6,.8):
            curve=await call('surface_extract_isocurve', object_name=patch['name'], direction=direction, parameter=parameter)
            isolines.append(curve['name'])
    await code(f'''
for name in {isolines!r}:
 o=App.ActiveDocument.getObject(name)
 o.ViewObject.LineColor=(0.65,0.88,0.92)
 o.ViewObject.LineWidth=1.5
_result_=True
''')
    housing=await finish('sensor-housing', [body['name'],'BottomPlate']+isolines, [body['name'],'BottomPlate'])
    housing.update(patch_volume_increase_mm3=28800, crown_center=center, original_perimeter_preserved=True)
    return {'inspection_actuator':actuator,'mounting_plate':plate,'sensor_housing':housing}
