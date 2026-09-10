"""Native Assembly BOM lifecycle, spreadsheet persistence and CSV publication."""

import csv
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    call = tools_for(root, "assembly")
    doc = App.newDocument("BomLifecycle")
    call("assembly_create", name="Asm")

    plate = doc.addObject("Part::Feature", "Plate")
    plate.Label = "Base plate"
    plate.addProperty("App::PropertyLength", "Thickness")
    plate.Thickness = "6 mm"
    plate.Shape = Part.makeBox(40, 30, 6)
    for instance in ("PlateOne", "PlateTwo"):
        call(
            "assembly_insert_component",
            assembly_name="Asm",
            source_name="Plate",
            instance_name=instance,
        )

    bracket = doc.addObject("Part::Feature", "Bracket")
    bracket.Label = "Angle bracket"
    bracket.Shape = Part.makeBox(10, 10, 20)
    call(
        "assembly_insert_component",
        assembly_name="Asm",
        source_name="Bracket",
        instance_name="BracketOne",
    )
    doc.recompute()

    created = call(
        "assembly_create_bom",
        assembly_name="Asm",
        name="Bom",
        columns=["Index", "Name", "Description", ".Thickness", "Quantity", "Supplier"],
    )
    assert created["columns"] == [
        "Index",
        "Name",
        "Description",
        ".Thickness",
        "Quantity",
        "Supplier",
    ]
    assert created["rows"] == 2, created
    plate_row = next(
        row for row in created["data"] if row["values"]["Name"] == "Base plate"
    )
    bracket_row = next(
        row for row in created["data"] if row["values"]["Name"] == "Angle bracket"
    )
    assert plate_row["values"]["Quantity"] == 2, created
    thickness = plate_row["values"][".Thickness"]
    assert thickness["value"] == 6.0 and "mm" in thickness["unit"], created
    assert bracket_row["values"]["Quantity"] == 1, created

    edits = [
        {
            "row": plate_row["sheet_row"],
            "column": "Description",
            "value": 'Laser, "deburr"',
        },
        {"row": plate_row["sheet_row"], "column": "Supplier", "value": "ACME"},
        {"row": bracket_row["sheet_row"], "column": "Supplier", "value": "Northworks"},
    ]
    edited = call("assembly_edit_bom_cells", bom_name="Bom", edits=edits)
    new_plate = next(
        row for row in edited["data"] if row["values"]["Name"] == "Base plate"
    )
    assert new_plate["values"]["Description"] == 'Laser, "deburr"', edited
    assert new_plate["values"]["Supplier"] == "ACME", edited

    # Changing column indexes exercises the native custom-data save defect.
    reordered_columns = [
        "Name",
        "Supplier",
        "Quantity",
        "Index",
        ".Thickness",
        "Description",
    ]
    configured = call(
        "assembly_configure_bom",
        bom_name="Bom",
        columns=reordered_columns,
        detail_parts=False,
        detail_subassemblies=False,
    )
    assert configured["columns"] == reordered_columns, configured
    new_plate = next(
        row for row in configured["data"] if row["values"]["Name"] == "Base plate"
    )
    assert new_plate["values"]["Description"] == 'Laser, "deburr"', configured
    assert new_plate["values"]["Supplier"] == "ACME", configured
    assert not configured["detail_parts"] and not configured["detail_subassemblies"]
    without_quantity = call(
        "assembly_create_bom", assembly_name="Asm", name="NoQuantity", columns=["Name"]
    )
    assert (
        without_quantity["rows"] == 3
        and without_quantity["structure_validation"]["valid"]
    ), without_quantity

    # Invalid updates must not replace native data or generated cells.
    before = call("assembly_inspect_bom", bom_name="Bom")
    call(
        "assembly_edit_bom_cells",
        bom_name="Bom",
        expect_success=False,
        edits=[{"row": new_plate["sheet_row"], "column": "Quantity", "value": "99"}],
    )
    call(
        "assembly_configure_bom",
        bom_name="Bom",
        expect_success=False,
        columns=["Index", "Name", "Name"],
    )
    after = call("assembly_inspect_bom", bom_name="Bom")
    assert after["data"] == before["data"], {"before": before, "after": after}

    path = Path(output) / "bom.csv"
    exported = call("assembly_export_bom_csv", bom_name="Bom", file_path=str(path))
    assert exported["native_export_attempted"] and path.is_file(), exported
    with path.open(newline="", encoding="utf-8") as stream:
        csv_rows = list(csv.reader(stream))
    assert csv_rows[0] == reordered_columns, csv_rows
    output_plate = next(row for row in csv_rows[1:] if row[0] == "Base plate")
    assert output_plate[-1] == 'Laser, "deburr"', csv_rows
    call(
        "assembly_export_bom_csv",
        bom_name="Bom",
        file_path=str(path),
        expect_success=False,
    )
    replacement = call(
        "assembly_export_bom_csv",
        bom_name="Bom",
        file_path=str(path),
        overwrite=True,
        delimiter=";",
    )
    assert replacement["delimiter"] == ";"

    filename = str(Path(output) / "bom.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    persisted = call("assembly_inspect_bom", bom_name="Bom")
    persisted_plate = next(
        row for row in persisted["data"] if row["values"]["Name"] == "Base plate"
    )
    assert persisted_plate["values"]["Supplier"] == "ACME", persisted
    assert persisted_plate["values"]["Description"] == 'Laser, "deburr"', persisted
    # Literal newlines, quotes and backslashes survive CSV normalization.
    special = '加工说明\nquote "here" and C:\\parts'
    special_result = call(
        "assembly_edit_bom_cells",
        bom_name="Bom",
        edits=[
            {
                "row": persisted_plate["sheet_row"],
                "column": "Description",
                "value": special,
            }
        ],
    )
    assert (
        special_result["data"][persisted_plate["sheet_row"] - 2]["values"][
            "Description"
        ]
        == special
    )
    call("assembly_export_bom_csv", bom_name="Bom", file_path=str(path), overwrite=True)
    with path.open(newline="", encoding="utf-8") as stream:
        special_rows = list(csv.reader(stream))
    assert (
        next(row for row in special_rows[1:] if row[0] == "Base plate")[-1] == special
    )
    assert all(len(row) == len(reordered_columns) for row in special_rows)
    # Native regeneration interprets numeric/formula-like text: fail and
    # retain existing custom values rather than silently coercing literal text.
    for value in ("0012", "=1+1", "6 mm"):
        call(
            "assembly_edit_bom_cells",
            bom_name="Bom",
            expect_success=False,
            edits=[
                {
                    "row": persisted_plate["sheet_row"],
                    "column": "Description",
                    "value": value,
                }
            ],
        )
        checked = call("assembly_inspect_bom", bom_name="Bom")
        assert (
            checked["data"][persisted_plate["sheet_row"] - 2]["values"]["Description"]
            == special
        )
    # Shrinking the columns must keep custom values formerly at the far right.
    shrunk = call(
        "assembly_configure_bom",
        bom_name="Bom",
        columns=["Name", "Description", "Quantity"],
    )
    assert (
        shrunk["data"][persisted_plate["sheet_row"] - 2]["values"]["Description"]
        == special
    )
    # Separate source objects with duplicate labels are distinct native rows.
    duplicate = doc.addObject("Part::Feature", "Duplicate")
    duplicate.Shape = Part.makeBox(1, 1, 1)
    prefs = App.ParamGet("User parameter:BaseApp/Preferences/Document")
    previous_duplicate_labels = prefs.GetBool("DuplicateLabels", False)
    prefs.SetBool("DuplicateLabels", True)
    duplicate.Label = "Base plate"
    prefs.SetBool("DuplicateLabels", previous_duplicate_labels)
    call(
        "assembly_insert_component",
        assembly_name="Asm",
        source_name="Duplicate",
        instance_name="DuplicateLink",
    )
    duplicates = call("assembly_inspect_bom", bom_name="Bom")
    assert duplicates["rows"] == 3 and duplicates["warnings"], duplicates
    call(
        "assembly_edit_bom_cells",
        bom_name="Bom",
        expect_success=False,
        edits=[
            {
                "row": persisted_plate["sheet_row"],
                "column": "Description",
                "value": "Ambiguous",
            }
        ],
    )
    # Nested quantities are local to each containing assembly.
    call("assembly_create", name="Top")
    call("assembly_create", name="Sub")
    bolt = doc.addObject("Part::Feature", "Bolt")
    bolt.Shape = Part.makeCylinder(2, 8)
    for instance in ("BoltA", "BoltB", "BoltC"):
        call(
            "assembly_insert_component",
            assembly_name="Sub",
            source_name="Bolt",
            instance_name=instance,
        )
    for instance in ("SubA", "SubB"):
        call(
            "assembly_insert_component",
            assembly_name="Top",
            source_name="Sub",
            instance_name=instance,
        )
    call(
        "assembly_insert_component",
        assembly_name="Top",
        source_name="Bolt",
        instance_name="TopBolt",
    )
    nested = call("assembly_create_bom", assembly_name="Top", name="NestedBom")
    assert not nested["structure_validation"]["valid"], nested
    expected = nested["structure_validation"]["expected_hierarchy"]
    assert [
        (row["index"], row["quantity"], row["total_quantity"]) for row in expected
    ] == [("1", 2, 2), ("1.1", 3, 6), ("2", 1, 1)], expected
    call(
        "assembly_export_bom_csv",
        bom_name="NestedBom",
        file_path=str(Path(output) / "bad.csv"),
        expect_success=False,
    )
    assert not (Path(output) / "bad.csv").exists()
    parts_path = Path(output) / "parts.csv"
    parts = call(
        "assembly_parts_list",
        assembly_name="Top",
        layout="flat",
        file_path=str(parts_path),
    )
    assert len(parts["parts"]) == 1 and parts["parts"][0]["quantity"] == 7, parts
    with parts_path.open(newline="", encoding="utf-8") as stream:
        exported_parts = list(csv.DictReader(stream))
    assert (
        exported_parts[0]["object"] == "Bolt" and exported_parts[0]["quantity"] == "7"
    )
    call(
        "assembly_parts_list",
        assembly_name="Top",
        layout="flat",
        file_path=str(parts_path),
        expect_success=False,
    )
    shallow = call(
        "assembly_configure_bom", bom_name="NestedBom", detail_subassemblies=False
    )
    assert shallow["rows"] == 2, shallow
    only_parts = call("assembly_configure_bom", bom_name="NestedBom", only_parts=True)
    assert (
        only_parts["rows"] == 1 and only_parts["data"][0]["values"]["Name"] == "Sub"
    ), only_parts
    return {
        "rows": persisted["rows"],
        "merged_quantity": plate_row["values"]["Quantity"],
        "custom_cells_saved": True,
        "csv_exported": True,
    }
