"""Require a completion report from FreeCAD, even when its console exits zero."""

import json
from pathlib import Path
import subprocess


def run_freecad_case(binary, directory, code, *, gui=False):
    report = Path(directory) / "report.json"
    runner = Path(directory) / ("runner.FCMacro" if gui else "runner.py")
    runner.write_text(
        f"""import json, traceback
try:
    scope = {{}}
    exec(compile({code!r}, '<native test>', 'exec'), scope)
    report = {{'success': True, 'details': scope.get('_details_')}}
except BaseException:
    report = {{'success': False, 'traceback': traceback.format_exc()}}
with open({str(report)!r}, 'w') as stream:
    json.dump(report, stream)
""",
        encoding="utf-8",
    )
    if gui:
        with runner.open("a") as stream:
            stream.write("""import FreeCAD as App, FreeCADGui as Gui
for doc_name in list(App.listDocuments()):
    App.closeDocument(doc_name)
Gui.getMainWindow().close()
""")
    command = [
        binary,
        "-u",
        str(Path(directory) / "user.cfg"),
        "-s",
        str(Path(directory) / "system.cfg"),
    ]
    if not gui:
        command.append("--console")
    command.append(str(runner))
    # The launcher may print environment variables, including credentials.
    # Never include raw stdout/stderr in test results or timeout exceptions.
    try:
        process = subprocess.run(command, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise AssertionError("FreeCAD did not finish within 60 seconds") from None
    assert report.is_file(), (
        f"FreeCAD did not write a completion report; exit={process.returncode}"
    )
    data = json.loads(report.read_text())
    assert data["success"], data.get("traceback")
    assert process.returncode == 0, f"FreeCAD exit={process.returncode}"
    return data.get("details")
