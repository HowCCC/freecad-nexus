"""Native split postprocessing, settings and SetupSheet workflows."""

import re
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from Path.Tool.camassets import user_asset_store
    from workflows import tools_for

    user_asset_store.set_dir(Path(output) / "assets")
    doc = App.newDocument("PostProcessing")
    call = tools_for(root, "cam")
    model = doc.addObject("Part::Feature", "Model")
    model.Shape = Part.makeBox(20, 20, 10)
    call("cam_create_job", name="Job", model_names=["Model"])
    first = call(
        "cam_add_tool_controller",
        job_name="Job",
        tool_number=1,
        spindle_speed=12000,
        horizontal_feed=300,
        vertical_feed=100,
        tool={"Diameter": "3 mm"},
    )
    second = call(
        "cam_add_tool_controller",
        job_name="Job",
        tool_number=2,
        spindle_speed=10000,
        horizontal_feed=400,
        vertical_feed=100,
        tool={"Diameter": "4 mm"},
        create_new=True,
    )
    operations = []
    for tc in (first, second):
        operations.append(
            call(
                "cam_add_operation",
                job_name="Job",
                operation="Profile",
                tool_controller_name=tc["name"],
            )
        )
    assert call("cam_validate_job", job_name="Job", recompute=True)["valid"]
    inspect = call("cam_inspect_job", job_name="Job")
    assert inspect["tools"][0]["horizontal_feed"] == 300
    info = call(
        "cam_get_post_processor_info",
        job_name="Job",
        post_processor="refactored_linuxcnc",
    )
    assert "--no-show-editor" in info["argument_help"]
    assert info["legacy_wrapper"] is False
    # SetupSheet rapid speeds explicitly use mm/min; expression-linked heights update.
    before = doc.getObject(operations[0]["name"]).SafeHeight.Value
    sheet = call(
        "cam_set_setup_sheet",
        job_name="Job",
        parameters={"SafeHeightOffset": 8, "HorizRapid": 6000},
    )
    assert doc.Job.SetupSheet.HorizRapid.getValueAs("mm/min").Value == 6000
    assert doc.Job.SetupSheet.SafeHeightOffset.Value == 8
    assert doc.getObject(operations[0]["name"]).SafeHeight.Value != before
    old = doc.Job.SetupSheet.SafeHeightOffset.Value
    call(
        "cam_set_setup_sheet",
        job_name="Job",
        parameters={"SafeHeightOffset": 9, "Unknown": 1},
        expect_success=False,
    )
    assert doc.Job.SetupSheet.SafeHeightOffset.Value == old
    call(
        "cam_set_job_settings",
        job_name="Job",
        post_processor_args="--no-header",
        output_file=str(Path(output) / "single.nc"),
        order_output_by="Operation",
    )
    output1 = call(
        "cam_generate_gcode",
        job_name="Job",
        post_processor="refactored_linuxcnc",
        include_gcode=True,
    )
    assert output1["written_files"] == 1 and output1["sections"] == 1
    code = Path(output1["path"]).read_text()
    assert (
        "G21" in code and "T1" in code and "T2" in code and re.search(r"G0?1\b", code)
    )
    assert code == output1["files"][0]["gcode"]
    assert "--no-show-editor" in output1["post_args"]
    assert doc.Job.PostProcessorArgs == "--no-header"
    snapshot = Path(output1["path"]).read_bytes()
    call(
        "cam_generate_gcode",
        job_name="Job",
        post_processor="refactored_linuxcnc",
        expect_success=False,
    )
    assert Path(output1["path"]).read_bytes() == snapshot
    call(
        "cam_generate_gcode",
        job_name="Job",
        post_processor="refactored_linuxcnc",
        overwrite=True,
    )
    call("cam_set_job_settings", job_name="Job", post_processor_args="")
    assert doc.Job.PostProcessorArgs == ""
    call("cam_set_fixture", job_name="Job", fixture_names=["G54", "G55"])
    splits = {}
    for order in ("Operation", "Tool", "Fixture"):
        folder = Path(output) / order
        folder.mkdir()
        call(
            "cam_set_job_settings",
            job_name="Job",
            split_output=True,
            order_output_by=order,
        )
        result = call(
            "cam_generate_gcode",
            job_name="Job",
            file_path=str(folder / "part.nc"),
            post_processor="refactored_linuxcnc",
            post_processor_args="--no-header",
            include_gcode=True,
        )
        assert (
            result["sections"] == 2
            and result["written_files"] == 2
            and result["path"] is None
        ), result
        assert len({f["path"] for f in result["files"]}) == 2
        for item in result["files"]:
            gcode = Path(item["path"]).read_text()
            assert re.search(r"G0?1\b", gcode) and re.search(r"M0?2\b", gcode)
            assert "T" in gcode, gcode
        splits[order] = [Path(f["path"]).name for f in result["files"]]
    # Each Fixture output must be self-contained with a tool selection.
    assert any("G55" in item["gcode"] for item in result["files"])
    # A single tool across fixtures must still initialize EVERY split program.
    call("cam_set_operation_state", operation_name=operations[1]["name"], active=False)
    folder = Path(output) / "one_tool"
    folder.mkdir()
    one_tool = call(
        "cam_generate_gcode",
        job_name="Job",
        file_path=str(folder / "part.nc"),
        post_processor="refactored_linuxcnc",
        post_processor_args="--no-header",
        include_gcode=True,
    )
    for item in one_tool["files"]:
        lines = item["gcode"].splitlines()
        tool_line = next(
            (i for i, line in enumerate(lines) if re.search(r"\bT1\b", line)), None
        )
        motion_line = next(
            i
            for i, line in enumerate(lines)
            if re.search(r"\bG0?[0123]\b", line) and re.search(r"[XYZ][-+0-9.]", line)
        )
        assert tool_line is not None and tool_line < motion_line, item["gcode"]
        assert not re.search(r"\bT2\b", item["gcode"])
    call("cam_set_operation_state", operation_name=operations[1]["name"], active=True)
    missing = Path(output) / "missing"
    call(
        "cam_generate_gcode",
        job_name="Job",
        file_path=str(missing / "bad.nc"),
        post_processor="refactored_linuxcnc",
        expect_success=False,
    )
    assert not missing.exists()
    # Invalid native args/help must not be mistaken for successful G-code.
    for args in ("--bogus-option", "--help"):
        path = Path(output) / ("bad" + str(len(args)) + ".nc")
        call(
            "cam_generate_gcode",
            job_name="Job",
            file_path=str(path),
            post_processor="refactored_linuxcnc",
            post_processor_args=args,
            expect_success=False,
        )
        assert not path.exists()
    call(
        "cam_set_job_settings",
        job_name="Job",
        split_output=False,
        order_output_by="Operation",
    )
    legacy = call(
        "cam_generate_gcode",
        job_name="Job",
        file_path=str(Path(output) / "legacy.nc"),
        post_processor="linuxcnc",
        post_processor_args="--no-header",
    )
    assert legacy["written_files"] == 1 and Path(legacy["path"]).stat().st_size > 100
    # CRLF selected by native postprocessor must survive byte-for-byte.
    crlf = call(
        "cam_generate_gcode",
        job_name="Job",
        file_path=str(Path(output) / "crlf.nc"),
        post_processor="refactored_linuxcnc",
        post_processor_args=r"--end_of_line_characters '\r\n'",
    )
    assert b"\r\n" in Path(crlf["path"]).read_bytes()
    # Native setup report returns real tool/run summaries and embedded images.
    if App.GuiUp:
        import FreeCADGui as Gui
        from PySide import QtGui

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Model, "Face1")
        visibility = {obj.Name: obj.Visibility for obj in doc.Objects}
        main = Gui.getMainWindow()
        windows = list(main.getWindows())
        active = main.findChild(QtGui.QMdiArea).activeSubWindow()
        camera = Gui.activeDocument().activeView().getCamera()
    report = call(
        "cam_generate_setup_report",
        job_name="Job",
        file_path=str(Path(output) / "setup.html"),
        include_html=True,
    )
    assert report["native_report"] and report["validation"]["valid"]
    assert report["data"]["toolData"]["1"]["spindlespeed"] == "12000 rpm"
    assert len(report["data"]["runData"]["operations"]) == 2
    assert report["data"]["stockData"]["xLen"]
    assert report["html"] == Path(report["path"]).read_text()
    assert "<html" in report["html"].lower() and "Profile" in report["html"]
    assert report["images_included"] == bool(App.GuiUp)
    if App.GuiUp:
        assert "data:image/png;base64," in report["html"]
        assert {obj.Name: obj.Visibility for obj in doc.Objects} == visibility
        assert list(main.getWindows()) == windows
        assert main.findChild(QtGui.QMdiArea).activeSubWindow() == active
        assert Gui.activeDocument().activeView().getCamera() == camera
        selected = Gui.Selection.getSelectionEx()
        assert (
            len(selected) == 1
            and selected[0].ObjectName == "Model"
            and selected[0].SubElementNames == ("Face1",)
        )
    snapshot = Path(report["path"]).read_bytes()
    call(
        "cam_generate_setup_report",
        job_name="Job",
        file_path=report["path"],
        expect_success=False,
    )
    assert Path(report["path"]).read_bytes() == snapshot
    report_noimages = call(
        "cam_generate_setup_report",
        job_name="Job",
        include_images=False,
        include_html=True,
    )
    assert (
        report_noimages["path"] is None and report_noimages["images_included"] is False
    )
    assert "data:image/png;base64," not in report_noimages["html"]
    filename = str(Path(output) / "post.FCStd")
    doc.recompute()
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    assert call("cam_validate_job", job_name="Job", recompute=True)["valid"]
    return {
        "split_files": splits,
        "legacy_post": legacy["written_files"],
        "single_bytes": output1["bytes"],
        "rapid_mm_min": 6000,
    }
