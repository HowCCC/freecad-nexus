"""Native projection, shape topology and placement regression workflows."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    doc = App.newDocument("SurfaceShapes")
    call = tools_for(root, "surface")

    def feature(name, shape):
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = shape
        return obj

    def shape(result):
        return doc.getObject(result["name"]).Shape

    def close(actual, expected):
        assert abs(actual - expected) < 1e-6, (actual, expected)

    plane = feature("Plane", Part.makePlane(10, 10))
    vertical = feature("Vertical", Part.makePlane(10, 10))
    vertical.Placement = App.Placement(
        App.Vector(0, 5, -5), App.Rotation(App.Vector(1, 0, 0), 90)
    )
    intersection = call(
        "surface_intersection", first_name=plane.Name, second_name=vertical.Name
    )
    assert intersection["operation"] == "section" and intersection["faces"] == 0
    close(shape(intersection).Length, 10)
    parallel = feature("Parallel", Part.makePlane(10, 10, App.Vector(0, 0, 5)))
    before = [o.Name for o in doc.Objects]
    call(
        "surface_intersection",
        first_name=plane.Name,
        second_name=parallel.Name,
        expect_success=False,
    )
    assert before == [o.Name for o in doc.Objects]
    projected_edge = feature(
        "ProjectMe", Part.makeLine(App.Vector(1, 2, 5), App.Vector(4, 2, 5))
    )
    projection = call(
        "surface_project", object_name=projected_edge.Name, target_name=plane.Name
    )
    close(shape(projection).Length, 3)
    close(shape(projection).BoundBox.ZMax, 0)
    perspective = call(
        "surface_project",
        object_name=projected_edge.Name,
        target_name=plane.Name,
        perspective=True,
        eye_point=[0, 0, 10],
    )
    close(shape(perspective).Length, 6)
    close(shape(perspective).BoundBox.YMin, 4)
    call(
        "surface_project",
        object_name=projected_edge.Name,
        target_name=plane.Name,
        perspective=True,
        expect_success=False,
    )
    call(
        "surface_project",
        object_name=projected_edge.Name,
        target_name=plane.Name,
        direction_z=0,
        expect_success=False,
    )
    target_pair = feature(
        "TargetPair", Part.makeCompound([plane.Shape, parallel.Shape])
    )
    elevated = feature(
        "ElevatedEdge", Part.makeLine(App.Vector(1, 2, 8), App.Vector(4, 2, 8))
    )
    selected = call(
        "surface_project",
        object_name=elevated.Name,
        target_name=target_pair.Name,
        face_index=1,
    )
    close(shape(selected).BoundBox.ZMin, 5)

    plane.Placement = App.Placement(
        App.Vector(20, 0, 3), App.Rotation(App.Vector(1, 0, 0), 30)
    )
    source_point = plane.Shape.Faces[0].valueAt(2, 3)
    transformed = call(
        "surface_transform", object_name=plane.Name, tx=1, ty=2, tz=4, angle=90
    )
    delta = App.Placement(App.Vector(1, 2, 4), App.Rotation(App.Vector(0, 0, 1), 90))
    actual_point = shape(transformed).Faces[0].valueAt(2, 3)
    assert (actual_point - delta.multVec(source_point)).Length < 1e-7
    assert (plane.Shape.Faces[0].valueAt(2, 3) - source_point).Length < 1e-7
    offset = call("surface_offset", object_name=plane.Name, distance=2)
    offset_point = shape(offset).Faces[0].valueAt(2, 3)
    assert (
        offset_point - source_point - plane.Shape.Faces[0].normalAt(2, 3) * 2
    ).Length < 1e-7

    # Whole shape conversions retain disconnected compounds and solid shells.
    solid = feature("Box", Part.makeBox(2, 3, 4))
    nurbs = call("surface_to_nurbs", object_name=solid.Name)
    assert nurbs["shape_type"] == "Solid" and nurbs["solids"] == 1
    close(shape(nurbs).Volume, 24)
    disconnected = feature(
        "Disconnected", Part.makeCompound([plane.Shape, parallel.Shape])
    )
    reverse = call("surface_reverse", object_name=disconnected.Name)
    assert reverse["shape_type"] == "Compound"
    original_normal = disconnected.Shape.Faces[0].normalAt(0.5, 0.5)
    reverse_normal = shape(reverse).Faces[0].normalAt(0.5, 0.5)
    close(original_normal.dot(reverse_normal), -1)
    circle1 = feature("Circle1", Part.Wire(Part.makeCircle(2)))
    circle2 = feature("Circle2", Part.Wire(Part.makeCircle(2, App.Vector(0, 0, 10))))
    loft = call(
        "surface_loft",
        name="Loft",
        section_names=[circle1.Name, circle2.Name],
        solid=True,
    )
    close(shape(loft).Volume, 40 * math.pi)
    # Explicit wire selection must avoid silently selecting the first profile.
    two_wires = feature(
        "TwoWires",
        Part.makeCompound(
            [Part.Wire(Part.makeCircle(1, App.Vector(20, 0, 0))), circle1.Shape]
        ),
    )
    selected_loft = call(
        "surface_loft",
        name="SelectedLoft",
        section_names=[two_wires.Name, circle2.Name],
        wire_indices=[1, 0],
        solid=True,
    )
    close(shape(selected_loft).Volume, 40 * math.pi)
    path = feature("SweepPath", Part.makeLine(App.Vector(), App.Vector(0, 0, 10)))
    sweep = call(
        "surface_sweep",
        name="Sweep",
        profile_name=circle1.Name,
        path_name=path.Name,
        make_solid=True,
    )
    close(shape(sweep).Volume, 40 * math.pi)
    call(
        "surface_loft",
        name="BadLoft",
        section_names=[circle1.Name],
        expect_success=False,
    )
    call(
        "surface_sweep",
        name="BadSweep",
        profile_name=circle1.Name,
        path_name=path.Name,
        transition=3,
        expect_success=False,
    )
    axial = feature(
        "AxialLine", Part.makeLine(App.Vector(12, 0, 0), App.Vector(12, 0, 10))
    )
    revolved = call("surface_revolve", object_name=axial.Name, center_x=10)
    close(shape(revolved).Area, 40 * math.pi)
    extruded = call("surface_extrude", object_name=parallel.Name, dz=2)
    close(shape(extruded).Volume, 200)
    filename = str(Path(output) / "surface_shapes.FCStd")
    doc.recompute()
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    close(shape(loft).Volume, 40 * math.pi)
    close(shape(projection).Length, 3)
    return {
        "section_length": 10,
        "perspective_length": 6,
        "loft_volume": shape(loft).Volume,
        "sweep_volume": shape(sweep).Volume,
        "nurbs_solid_volume": shape(nurbs).Volume,
    }
