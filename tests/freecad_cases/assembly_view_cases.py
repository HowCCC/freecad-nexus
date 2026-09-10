"""Native exploded-step lifecycle with nested instances and world coordinates."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    import UtilsAssembly
    import CommandCreateView
    from workflows import tools_for

    call = tools_for(root, "assembly")
    doc = App.newDocument("ExplodedLifecycle")
    parent = doc.addObject("App::Part", "Parent")
    parent.Placement = App.Placement(
        App.Vector(100, 20, 30), App.Rotation(App.Vector(0, 0, 1), 30)
    )
    call("assembly_create", name="Asm")
    parent.addObject(doc.Asm)
    doc.Asm.Placement.Base = App.Vector(10, 0, 0)
    source = doc.addObject("Part::Feature", "Source")
    source.Shape = Part.makeBox(2, 4, 6)
    source.Placement.Base = App.Vector(9, 0, 0)
    for name, x in [("Left", -10), ("Right", 10)]:
        call(
            "assembly_insert_component",
            assembly_name="Asm",
            source_name="Source",
            instance_name=name,
        )
        doc.getObject(name).Placement.Base = App.Vector(x, 0, 0)
    call("assembly_create", name="NestedSource")
    call(
        "assembly_insert_component",
        assembly_name="NestedSource",
        source_name="Source",
        instance_name="Inner",
    )
    doc.Inner.Placement.Base = App.Vector(5, 0, 0)
    call(
        "assembly_insert_component",
        assembly_name="Asm",
        source_name="NestedSource",
        instance_name="Nested",
    )
    doc.Nested.Placement.Base = App.Vector(20, 0, 0)
    doc.recompute()
    view = call("assembly_create_exploded_view", assembly_name="Asm", name="View")
    assert isinstance(doc.View.Proxy, CommandCreateView.ExplodedView)
    inspected = call("assembly_inspect_exploded_view", view_name="View")
    nested_paths = [
        item["path"]
        for item in inspected["components"]
        if item["path"].startswith("Nested.") and item["has_shape"]
    ]
    assert len(nested_paths) == 1, inspected
    nested_path = nested_paths[0]
    assert nested_path == "Nested." + doc.Nested.Group[0].Name + "."
    baseline = {
        obj.Name: obj.Placement.copy()
        for obj in doc.Objects
        if hasattr(obj, "Placement")
    }
    global_placement = doc.Asm.getGlobalPlacement()

    def position(result, path):
        row = next(item for item in result["components"] if item["path"] == path)
        return App.Vector(*row["placement"]["base"])

    def same(actual, expected):
        assert (actual - expected).Length < 1e-7, (actual, expected)

    def unchanged():
        for name, placement in baseline.items():
            assert doc.getObject(name).Placement.isSame(placement, 1e-10), name

    initial = call(
        "assembly_export_exploded_shape",
        view_name="View",
        progress=0,
        include_lines=False,
        name="Initial",
    )
    same(position(initial, "Left."), global_placement.multVec(App.Vector(-10, 0, 0)))
    same(position(initial, nested_path), global_placement.multVec(App.Vector(25, 0, 0)))
    first = call(
        "assembly_add_exploded_step",
        view_name="View",
        component_name="Right",
        dx=10,
        dy=0,
        dz=0,
        name="Translate",
    )
    second = call(
        "assembly_add_exploded_step",
        view_name="View",
        component_name="Right",
        dx=0,
        dy=0,
        dz=0,
        move_type="Rotate",
        rotation_angle=90,
        name="Rotate",
    )
    assert first["move_type"] == "Normal" and second["move_type"] == "Normal"
    result = call("assembly_export_exploded_shape", view_name="View", name="Final")
    same(position(result, "Right."), global_placement.multVec(App.Vector(0, 20, 0)))
    # Second guide starts at the first guide's endpoint (not original geometry).
    same(
        App.Vector(*result["lines"][0]["end"]), App.Vector(*result["lines"][1]["start"])
    )
    frames = call("assembly_exploded_frames", view_name="View", frames_per_step=4)
    assert frames["frame_count"] == 9
    same(
        position(frames["frames"][2], "Right."),
        global_placement.multVec(App.Vector(15, 0, 0)),
    )
    same(position(frames["frames"][-1], "Right."), position(result, "Right."))
    unchanged()
    call(
        "assembly_reorder_exploded_steps",
        view_name="View",
        step_names=["Rotate", "Translate"],
    )
    reordered = call(
        "assembly_export_exploded_shape", view_name="View", name="Reordered"
    )
    same(position(reordered, "Right."), global_placement.multVec(App.Vector(10, 10, 0)))
    call(
        "assembly_edit_exploded_step",
        step_name="Translate",
        label="Translated component",
    )
    assert (
        doc.Translate.MovementTransform.Base.x == 10
        and doc.Translate.Label == "Translated component"
    )
    call(
        "assembly_edit_exploded_step",
        step_name="Translate",
        transform={"translation": [3, 0, 0]},
    )
    assert doc.Translate.MovementTransform.Base.x == 3
    # A nested child step addresses only the generated instance copy.
    nested = call(
        "assembly_add_exploded_step",
        view_name="View",
        component_paths=[nested_path],
        dx=0,
        dy=0,
        dz=7,
        name="NestedMove",
    )
    assert UtilsAssembly.getObject([doc.Asm, [nested_path]]) is doc.Nested.Group[0]
    final_nested = call(
        "assembly_export_exploded_shape", view_name="View", name="NestedExport"
    )
    same(
        position(final_nested, nested_path),
        global_placement.multVec(App.Vector(25, 0, 7)),
    )
    assert doc.Inner.Placement.Base == App.Vector(5, 0, 0)
    unchanged()
    # Radial moves act on current component centers after previous moves.
    radial = call(
        "assembly_add_exploded_step",
        view_name="View",
        component_paths=["Left.", nested_path],
        dx=5,
        dy=0,
        dz=0,
        move_type="Radial",
        name="Radial",
    )
    radial_result = call(
        "assembly_export_exploded_shape", view_name="View", name="RadialExport"
    )
    line = next(
        line for line in radial_result["lines"] if line["component_path"] == "Left."
    )
    # Initial assembly bounds are x [-10,27], y [0,4], z [0,6].
    center = App.Vector(8.5, 2, 3)
    size = math.sqrt(37**2 + 4**2 + 6**2)
    left_center = App.Vector(-9, 2, 3)
    expected = left_center + (left_center - center) * (20 / size)
    same(App.Vector(*line["end"]), global_placement.multVec(expected))
    unchanged()
    # Hidden parts omitted by default but explicitly included on request.
    doc.Left.Visibility = False
    hidden = call(
        "assembly_export_exploded_shape",
        view_name="View",
        progress=0,
        include_lines=False,
    )
    assert "Left." not in [item["path"] for item in hidden["components"]]
    allparts = call(
        "assembly_export_exploded_shape",
        view_name="View",
        progress=0,
        include_hidden=True,
        include_lines=False,
    )
    assert "Left." in [item["path"] for item in allparts["components"]]
    doc.Left.Visibility = True
    previous = {obj.Name for obj in doc.Objects}
    order = [s.Name for s in doc.View.Group]
    for tool, args in [
        ("assembly_add_exploded_step", dict(view_name="View", component_name="Source")),
        (
            "assembly_add_exploded_step",
            dict(view_name="View", component_paths=["Nested.", nested_path]),
        ),
        (
            "assembly_edit_exploded_step",
            dict(step_name="Translate", component_paths=["Missing."], label="Bad"),
        ),
        ("assembly_edit_exploded_step", dict(step_name="Rotate", move_type="Radial")),
        (
            "assembly_reorder_exploded_steps",
            dict(view_name="View", step_names=["Translate"] * 4),
        ),
        ("assembly_export_exploded_shape", dict(view_name="View", progress=-1)),
    ]:
        call(tool, expect_success=False, **args)
        assert {obj.Name for obj in doc.Objects} == previous
        assert [s.Name for s in doc.View.Group] == order
    # Imported/broken references remain inspectable, export must reject them.
    doc.Translate.References = [doc.Asm, ["Missing."]]
    assert not call("assembly_inspect_exploded_view", view_name="View")["valid"]
    call("assembly_export_exploded_shape", view_name="View", expect_success=False)
    call(
        "assembly_edit_exploded_step", step_name="Translate", component_paths=["Right."]
    )
    filename = str(Path(output) / "exploded.FCStd")
    doc.recompute()
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    assert call("assembly_inspect_exploded_view", view_name="View")["valid"]
    assert doc.Translate.Label == "Translated component"
    saved = call("assembly_export_exploded_shape", view_name="View", name="Reopened")
    same(position(saved, "Left."), position(radial_result, "Left."))
    call("assembly_remove_exploded_step", step_name=nested["step"])
    assert doc.getObject(nested["step"]) is None
    removed = call("assembly_remove_exploded_view", view_name="View")
    assert set(removed["removed_steps"]) == {"Translate", "Rotate", "Radial"}
    assert (
        doc.getObject("View") is None
        and doc.Right
        and doc.Left
        and doc.Nested
        and doc.Source
    )
    # A nonzero pivot requires circular intermediate motion; interpolating the
    # encoded Placement.Base would instead move the part toward the pivot.
    call("assembly_create_exploded_view", assembly_name="Asm", name="PivotView")
    call(
        "assembly_add_exploded_step",
        view_name="PivotView",
        component_name="Right",
        dx=4,
        dy=0,
        dz=0,
        rotation_angle=90,
        rotation_center=[5, 0, 0],
        name="PivotStep",
    )
    half = call(
        "assembly_export_exploded_shape",
        view_name="PivotView",
        progress=0.5,
        include_lines=False,
    )
    expected = App.Vector(7 + 5 / math.sqrt(2), 5 / math.sqrt(2), 0)
    same(position(half, "Right."), global_placement.multVec(expected))
    filename = str(Path(output) / "pivot.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    reopened = call(
        "assembly_export_exploded_shape",
        view_name="PivotView",
        progress=0.5,
        include_lines=False,
    )
    same(position(reopened, "Right."), global_placement.multVec(expected))
    doc.Left.Visibility = False
    doc.Right.Visibility = False
    doc.Nested.Visibility = False
    call("assembly_export_exploded_shape", view_name="PivotView", expect_success=False)
    all_hidden = call(
        "assembly_export_exploded_shape", view_name="PivotView", include_hidden=True
    )
    assert len(all_hidden["components"]) == 3
    return {
        "nested_instance": nested_path,
        "frames": frames["frame_count"],
        "rotation_order_verified": True,
        "sources_preserved": True,
    }
