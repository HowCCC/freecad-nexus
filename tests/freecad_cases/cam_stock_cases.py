"""Native stock sizing, references, cleanup and persistence through MCP tools."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from Path.Tool.camassets import user_asset_store
    from workflows import tools_for

    user_asset_store.set_dir(Path(output) / "assets")
    doc = App.newDocument("StockSetup")
    call = tools_for(root, "cam")
    model = doc.addObject("Part::Feature", "Model")
    model.Shape = Part.makeBox(20, 10, 5, App.Vector(10, 20, 30))
    doc.recompute()
    before = {obj.Name for obj in doc.Objects}
    for arguments in [
        dict(model_names=["Model", "Missing"]),
        dict(model_names=["Model", "Model"]),
        dict(model_names=["Model"], stock_type="unsupported"),
        dict(model_names=["Model"], stock_type="Box", stock_params={"Radius": 2}),
        dict(model_names=["Model"], stock_type="Box", stock_params={"Height": 0}),
    ]:
        call("cam_create_job", name="Invalid", expect_success=False, **arguments)
        assert {obj.Name for obj in doc.Objects} == before, (
            arguments,
            sorted({obj.Name for obj in doc.Objects} - before),
            doc.UndoMode,
            doc.UndoCount,
        )
    result = call(
        "cam_create_job",
        name="Job",
        model_names=["Model"],
        stock_type="Box",
        stock_params={"Length": "3 cm"},
    )
    assert result["bounds"] == {"min": [10, 20, 30], "max": [40, 30, 35]}, result
    assert result["removed_stock"] and doc.getObject(result["removed_stock"]) is None
    name = doc.Job.Stock.Name
    result = call("cam_set_stock", job_name="Job", dimensions={"Width": 15})
    assert result["stock"] == name and doc.Job.Stock.Length.Value == 30
    assert math.isclose(result["volume"], 30 * 15 * 5)
    for dims in ({"Height": 0.0001}, {"Length": "2 s"}, {"Unknown": 1}):
        before = doc.Job.Stock.Shape.Volume
        call("cam_set_stock", job_name="Job", dimensions=dims, expect_success=False)
        assert doc.Job.Stock.Shape.Volume == before and doc.Job.Stock.Name == name
    # Preserve independent users when replacing old stock.
    link = doc.addObject("App::Link", "IndependentStockReference")
    link.LinkedObject = doc.Job.Stock
    result = call(
        "cam_set_stock",
        job_name="Job",
        stock_type="Cylinder",
        dimensions={"Radius": 20},
    )
    assert (
        result["retained_referenced_stock"] == name and link.LinkedObject.Name == name
    )
    assert doc.Job.Stock.Radius.Value == 20 and doc.Job.Stock.Height.Value == 5
    assert math.isclose(result["volume"], math.pi * 20**2 * 5)
    cylinder = doc.Job.Stock.Name
    result = call(
        "cam_set_stock",
        job_name="Job",
        stock_type="FromBase",
        dimensions={
            "ExtXneg": 2,
            "ExtXpos": 3,
            "ExtYneg": 0,
            "ExtYpos": 0,
            "ExtZneg": 0,
            "ExtZpos": 0,
        },
    )
    assert result["bounds"] == {"min": [8, 20, 30], "max": [33, 30, 35]}, result
    assert doc.getObject(cylinder) is None
    before = doc.Job.Stock.Shape.Volume
    call(
        "cam_set_stock",
        job_name="Job",
        stock_type="FromBase",
        dimensions={"ExtZneg": -10},
        expect_success=False,
    )
    assert doc.Job.Stock.Shape.Volume == before
    result = call(
        "cam_set_stock",
        job_name="Job",
        stock_type="FromBase",
        placement={"base": [100, 200, 300]},
    )
    assert result["bounds"]["min"] == [98, 200, 300]
    source = doc.addObject("Part::Feature", "RawStock")
    source.Shape = Part.makeCylinder(25, 50)
    source.Placement.Base = App.Vector(1, 2, 3)
    doc.recompute()
    result = call(
        "cam_set_stock",
        job_name="Job",
        stock_type="Existing",
        stock_object_name="RawStock",
    )
    assert result["source_objects"] == ["RawStock"]
    assert math.isclose(result["volume"], math.pi * 25**2 * 50)
    assert result["bounds"]["min"] == [-24, -23, 3], result
    source.Shape = Part.makeCylinder(20, 50)
    doc.recompute()
    assert math.isclose(doc.Job.Stock.Shape.Volume, math.pi * 20**2 * 50)
    before = doc.Job.Stock.Name
    call(
        "cam_set_stock",
        job_name="Job",
        stock_type="Existing",
        stock_object_name=before,
        expect_success=False,
    )
    assert doc.Job.Stock.Name == before
    file_path = str(Path(output) / "stock.FCStd")
    doc.recompute()
    doc.saveAs(file_path)
    App.closeDocument(doc.Name)
    doc = App.openDocument(file_path)
    doc.recompute()
    assert doc.Job.Stock.Objects[0].Name == "RawStock"
    assert math.isclose(doc.Job.Stock.Shape.Volume, math.pi * 20**2 * 50)
    return {
        "existing_stock_volume": doc.Job.Stock.Shape.Volume,
        "same_type_edit": True,
        "invalid_inputs_rollback": True,
    }
