"""Native API smoke checks; these do not exercise registered MCP adapters."""
import os
from pathlib import Path

import pytest
from freecad_runner import run_freecad_case

pytestmark = pytest.mark.freecad


@pytest.mark.skipif(not os.environ.get("FREECAD_BIN"), reason="set FREECAD_BIN to run FreeCAD integration")
def test_native_assembly_cam_surface_paths(tmp_path: Path):
    run_freecad_case(os.environ["FREECAD_BIN"], tmp_path, """import FreeCAD as App, Part, importlib
App.newDocument('Nexus')
# Surface: evaluate, extract an isocurve and segment the native parameter domain.
s=Part.BSplineSurface()
s.interpolate([[App.Vector(0,0,0),App.Vector(0,10,0),App.Vector(0,20,0)], [App.Vector(10,0,0),App.Vector(10,10,2),App.Vector(10,20,0)], [App.Vector(20,0,0),App.Vector(20,10,0),App.Vector(20,20,0)]])
surf=App.ActiveDocument.addObject('Part::Feature','Surface'); surf.Shape=s.toShape(); face=surf.Shape.Faces[0]
assert face.valueAt(0.5,0.5).z > 1.0
assert face.Surface.uIso(0.5).toShape().Length > 0
trimmed=face.Surface.copy(); trimmed.segment(0.2,0.8,0.2,0.8); assert trimmed.bounds() == (0.2,0.8,0.2,0.8)
assert surf.Shape.extrude(App.Vector(0,0,5)).isValid()
assert surf.Shape.revolve(App.Vector(0,0,0),App.Vector(0,0,1),90).isValid()
advanced=face.Surface.copy(); advanced.setPole(1,1,App.Vector(1,1,1)); advanced.setWeight(1,1,1.2)
assert advanced.getPole(1,1).x == 1.0 and advanced.getWeight(1,1) == 1.2
assert advanced.normal(0.5,0.5).Length > 0 and advanced.tangent(0.5,0.5)[0].Length > 0
assert advanced.getDN(0.5,0.5,1,0).Length > 0
assert advanced.projectPoint(App.Vector(1,2,0),"LowerDistanceParameters")
assert advanced.reparametrize(4,4,1e-6).bounds() == (0.0,1.0,0.0,1.0)
advanced.setUKnot(1,0.0); advanced.insertUKnot(0.25,1,0.0)
# Assembly: create native links and a real Distance joint.
asm=App.ActiveDocument.addObject('Assembly::AssemblyObject','Asm'); asm.Type='Assembly'; asm.newObject('Assembly::JointGroup','Joints')
a=App.ActiveDocument.addObject('Part::Feature','A'); a.Shape=Part.makeBox(10,10,10)
b=App.ActiveDocument.addObject('Part::Feature','B'); b.Shape=Part.makeBox(10,10,10)
la=asm.newObject('App::Link','LA'); la.LinkedObject=a; lb=asm.newObject('App::Link','LB'); lb.LinkedObject=b
import JointObject, UtilsAssembly
g=UtilsAssembly.getJointGroup(asm); joint=g.newObject('App::FeaturePython','Joint'); JointObject.Joint(joint,JointObject.JointTypes.index('Distance')); joint.Proxy.setJointConnectors(joint, [[la,['Face1','']], [lb,['Face1','']]]); joint.Distance=5; joint.Distance2=2; assert asm.solve(False) == 0
# CAM: create stock and every operation exposed by FreeCAD 1.1.
model=App.ActiveDocument.addObject('Part::Feature','Model'); model.Shape=Part.makeBox(20,20,10)
import Path.Main.Job as Job, Path.Main.Stock as Stock
job=Job.Create('Job',[model]); stock=Stock.CreateBox(job,App.Vector(30,40,50)); assert (stock.Length,stock.Width,stock.Height) == (30,40,50)
for n in ['Profile','Pocket','Drilling','Adaptive','Helix','Engrave','Surface','Deburr','Probe','Slot','Tapping','ThreadMilling','Vcarve','Waterline','MillFace','PocketShape','Custom']:
    job.Proxy.addOperation(importlib.import_module('Path.Op.'+n).Create(n,None,job))
profile=job.Operations.Group[0]; profile.Base=[(model,())]; App.ActiveDocument.recompute(); assert len(profile.Path.Commands) > 0 and profile.Path.Length > 0
dressups=[]
for n in ['Boundary','Array','Tags','DogboneII']:
    dressups.append(importlib.import_module('Path.Dressup.'+n).Create(profile))
assert len(dressups) == 4 and all(d.TypeId == 'Path::FeaturePython' for d in dressups) and all(d.Name in [x.Name for x in App.ActiveDocument.Objects] for d in dressups)
_details_={'operation_objects':len(job.Operations.Group),'profile_commands':len(profile.Path.Commands),'dressup_objects':len(dressups)}
""")
