"""Linked Surface::Cut and OCC mesh conversion through registered adapters."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    call = tools_for(root, "surface")
    doc = App.newDocument("CutAndMesh")
    base = doc.addObject("Part::Feature", "Base")
    base.Shape = Part.makePlane(10, 10)
    cutter = doc.addObject("Part::Cylinder", "Cutter")
    cutter.Radius = 1
    cutter.Height = 2
    cutter.Placement.Base = App.Vector(5, 5, -1)
    doc.recompute()
    cut = call("surface_create_cut", name="Cut", object_name="Base", tool_name="Cutter")
    assert cut["type"] == "Surface::Cut" and cut["sources"] == ["Base", "Cutter"]
    assert abs(cut["area"] - (100 - math.pi)) < 1e-6
    cutter.Radius = 2
    doc.recompute()
    assert abs(doc.Cut.Shape.Area - (100 - 4 * math.pi)) < 1e-6
    doc.Cut.Placement = App.Placement(
        App.Vector(100, 20, 30), App.Rotation(App.Vector(0, 0, 1), 30)
    )
    doc.recompute()
    surface_mesh = call(
        "surface_tessellate",
        object_name="Cut",
        linear_deflection=0.01,
        angular_deflection=0.1,
        include_mesh=True,
    )
    assert surface_mesh["triangles"] > 0 and not surface_mesh["is_solid"]
    assert abs(surface_mesh["area"] - doc.Cut.Shape.Area) < 0.1
    for vertex in surface_mesh["mesh"]["vertices"]:
        assert abs(vertex[2] - 30) < 1e-7
    # No triangle center may lie inside the hole.
    inverse = doc.Cut.Placement.inverse()
    points = surface_mesh["mesh"]["vertices"]
    for tri in surface_mesh["mesh"]["triangles"]:
        center = sum((App.Vector(*points[i]) for i in tri), App.Vector()) / 3
        local = inverse.multVec(center)
        assert math.hypot(local.x - 5, local.y - 5) > 1.98
    mesh_obj = doc.getObject(surface_mesh["name"])
    mesh_obj.Placement.Base += App.Vector(10, 0, 0)
    doc.recompute()
    faceted = call("surface_from_mesh", mesh_name=mesh_obj.Name)
    shape = doc.getObject(faceted["name"]).Shape
    assert faceted["faceted"] and not faceted["smooth_surface_fitted"]
    assert len(shape.Faces) == mesh_obj.Mesh.CountFacets
    assert abs(shape.BoundBox.XMin - mesh_obj.Mesh.BoundBox.XMin) < 1e-7
    assert abs(shape.BoundBox.ZMin - 30) < 1e-7
    before = {obj.Name for obj in doc.Objects}
    call(
        "surface_from_mesh",
        mesh_name=mesh_obj.Name,
        make_solid=True,
        expect_success=False,
    )
    call(
        "surface_tessellate",
        object_name="Base",
        linear_deflection=0,
        expect_success=False,
    )
    call(
        "surface_create_cut",
        name="BadCut",
        object_name="Base",
        tool_name="Base",
        expect_success=False,
    )
    assert {obj.Name for obj in doc.Objects} == before
    box = doc.addObject("Part::Feature", "Box")
    box.Shape = Part.makeBox(10, 20, 30)
    box.Placement.Base = App.Vector(50, 60, 70)
    doc.recompute()
    bm = call("surface_tessellate", object_name="Box", linear_deflection=0.1)
    assert bm["is_solid"] and abs(bm["volume"] - 6000) < 1e-6
    solid = call("surface_from_mesh", mesh_name=bm["name"], make_solid=True)
    assert solid["solids"] == 1 and abs(solid["volume"] - 6000) < 1e-6
    assert doc.getObject(solid["name"]).Shape.BoundBox.ZMin == 70
    subset = call("surface_tessellate", object_name="Box", face_index=0)
    assert not subset["is_solid"] and subset["triangles"] == 2
    filename = str(Path(output) / "mesh.FCStd")
    doc.recompute()
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    assert [ref[0].Name for ref in doc.Cut.ShapeList] == ["Base", "Cutter"]
    doc.Cutter.Radius = 1
    doc.recompute()
    assert abs(doc.Cut.Shape.Area - (100 - math.pi)) < 1e-6
    assert doc.getObject(bm["name"]).Mesh.CountFacets > 0
    return {
        "native_cut_updates": True,
        "trimmed_mesh_triangles": surface_mesh["triangles"],
        "solid_volume": solid["volume"],
    }
