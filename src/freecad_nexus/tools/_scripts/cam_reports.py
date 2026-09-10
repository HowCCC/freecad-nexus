"""Native CAM setup reports with isolated rendering and explicit diagnostics."""

from contextlib import contextmanager
import copy
from pathlib import Path as FilePath
import tempfile
from typing import TYPE_CHECKING

import FreeCAD as App
from Path.Main.Sanity import ImageBuilder
from Path.Main.Sanity.Sanity import CAMSanity

if TYPE_CHECKING:
    from .cam_jobs import cam_json_value, job_validation
    from .cam_operations import operation_job
    from .cam_postprocess import write_output_files


@contextmanager
def report_view_state():
    """Restore selection, visibility and active MDI view after native rendering."""
    if not App.GuiUp:
        yield
        return
    import FreeCADGui as Gui
    from PySide import QtGui

    main = Gui.getMainWindow()
    windows = list(main.getWindows())
    mdi = main.findChild(QtGui.QMdiArea)
    active = mdi.activeSubWindow()
    active_geometry = active.saveGeometry() if active is not None else None
    view = Gui.activeDocument().activeView()
    camera = view.getCamera()
    selection = [
        (item.DocumentName, item.ObjectName, list(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    ]
    visibility = [
        (obj, obj.Visibility)
        for doc in App.listDocuments().values()
        for obj in doc.Objects
        if hasattr(obj, "Visibility")
    ]
    try:
        yield
    finally:
        for window in list(main.getWindows()):
            if window not in windows:
                main.removeWindow(window)
        for obj, visible in visibility:
            obj.Visibility = visible
        if active is not None:
            mdi.setActiveSubWindow(active)
            active.restoreGeometry(active_geometry)
        Gui.updateGui()
        view.setCamera(camera)
        Gui.Selection.clearSelection()
        for doc, obj, subs in selection:
            for sub in subs or [""]:
                Gui.Selection.addSelection(doc, obj, sub)


class ReportImageBuilder(ImageBuilder.GuiImageBuilder):
    def build_image(self, obj, image_name, as_bytes=False, view="default"):
        with report_view_state():
            return super().build_image(obj, image_name, as_bytes=as_bytes, view=view)

    def __del__(self):
        # The native destructor restores its last snapshot, which may belong
        # to an earlier image. Each image is already restored above.
        pass


class NativeSetupReport(CAMSanity):
    def __init__(self, job, output_file, images):
        self.job = job
        self.output_file = str(output_file)
        self.filelocation = str(output_file.parent)
        builder = ReportImageBuilder if images else ImageBuilder.DummyImageBuilder
        self.image_builder = builder(self.filelocation)
        self.data = self.summarize()
        # Native headless reporting leaves paths to PNG files it never writes.
        for key, tool in self.data["toolData"].items():
            if key != "squawkData" and not tool.get("imagebytes"):
                tool["imagepath"] = ""


def report_json(value):
    if isinstance(value, bytes):
        return {"bytes": len(value), "embedded_in_html": bool(value)}
    if isinstance(value, dict):
        return {str(k): report_json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [report_json(v) for v in value]
    return cam_json_value(value)


def generate_setup_report(job_name, file_path, include_images, include_html, overwrite):
    job = operation_job(job_name)
    validation = job_validation(job, recompute=True)
    images = bool(include_images and App.GuiUp)
    path = FilePath(file_path).expanduser().absolute() if file_path else None
    if path and not path.parent.is_dir():
        raise FileNotFoundError("Output directory does not exist: " + str(path.parent))
    if path and path.exists() and not overwrite:
        raise FileExistsError("Output already exists: " + str(path))
    with tempfile.TemporaryDirectory(prefix="mcp-cam-report-") as directory:
        destination = path or FilePath(directory) / "setup.html"
        with report_view_state():
            report = NativeSetupReport(job, destination, images)
            data = report_json(copy.deepcopy(report.data))
            html = report.get_output_report()
            del report
    if not isinstance(html, str) or "<html" not in html.lower():
        raise ValueError("Native CAM report generator returned invalid HTML")
    content = html.encode("utf-8")
    if path:
        write_output_files([(path, content)], overwrite)
    warnings = [
        entry
        for section in data.values()
        if isinstance(section, dict)
        for entry in section.get("squawkData", [])
        if entry is not None
    ]
    result = {
        "job": job.Name,
        "path": str(path) if path else None,
        "bytes": len(content),
        "images_included": images,
        "native_report": True,
        "data": data,
        "squawks": warnings,
        "validation": validation,
        "scope": "native setup summary and warnings; not a machining or collision proof",
    }
    if include_html:
        result["html"] = html
    return result
