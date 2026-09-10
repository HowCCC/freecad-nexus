"""Optional FreeCAD 1.1 integration smoke test.

Run with: FREECAD_BIN=/Applications/FreeCAD.app/Contents/MacOS/FreeCAD pytest -m freecad
"""
import os
from pathlib import Path

import pytest
from freecad_runner import run_freecad_case

pytestmark = pytest.mark.freecad


@pytest.mark.skipif(not os.environ.get("FREECAD_BIN"), reason="set FREECAD_BIN to run FreeCAD integration")
def test_freecad_surface_and_cam_smoke(tmp_path: Path):
    run_freecad_case(os.environ["FREECAD_BIN"], tmp_path, """import FreeCAD as App, Part
App.newDocument('Smoke')
b=App.ActiveDocument.addObject('Part::Box','Box'); b.Shape=Part.makeBox(20,20,10)
import Path.Main.Job as J; job=J.Create('Job',[b])
import Path.Op.Profile as P; op=P.Create('Profile',None,job); job.Proxy.addOperation(op); App.ActiveDocument.recompute()
assert len(op.Path.Commands)>0
p=[[App.Vector(0,0,0),App.Vector(0,10,0)],[App.Vector(10,0,0),App.Vector(10,10,0)]]
s=Part.BSplineSurface(); s.interpolate(p); assert s.toShape().isValid()
""")
