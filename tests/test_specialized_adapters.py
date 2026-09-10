"""Exercise the registered MCP adapters against an actual FreeCAD process.

A file report, written only after every assertion, is mandatory: FreeCAD's
console may exit zero even when a script raised an exception. No mock geometry
or copied implementation code is used by the scenarios.
"""

import os
from pathlib import Path

import pytest
from freecad_runner import run_freecad_case

ROOT = Path(__file__).parents[1]
CASES = Path(__file__).parent / "freecad_cases"


@pytest.mark.freecad
@pytest.mark.skipif(
    not os.environ.get("FREECAD_BIN"), reason="set FREECAD_BIN for native adapter tests"
)
@pytest.mark.parametrize(
    "scenario",
    [
        "surface_construction",
        "surface_editing",
        "surface_spline_editing",
        "surface_seams",
        "surface_cut_mesh",
        "surface_geometry",
        "surface_shapes",
        "surface_face_replacement",
        "cam_templates",
        "cam_library_workflow",
        "cam_machine_workflow",
        "cam_operation_families",
        "cam_postprocessing",
        "cam_stock_setup",
        "cam_rotation_center",
        "cam_setup_lifecycle",
        "cam_setup_alignment",
        "cam_model_replacement",
        "assembly_connectors",
        "assembly_joint_families",
        "assembly_bom_lifecycle",
        "assembly_flexible_instances",
        "assembly_instance_sync",
        "assembly_exchange",
        "surface_features",
        "cam_path_statistics",
        "cam_arc_geometry",
        "cam_canned_cycles",
        "assembly_external_links",
    ],
)
def test_native_adapter_workflow(tmp_path, scenario):
    run_freecad_case(
        os.environ["FREECAD_BIN"],
        tmp_path,
        f"""import sys
sys.path.insert(0, {str(CASES)!r})
import workflows
_details_ = getattr(workflows, {scenario!r})({str(ROOT)!r}, {str(tmp_path)!r})
""",
    )


@pytest.mark.freecad
@pytest.mark.skipif(
    not os.environ.get("FREECAD_BIN") or not os.environ.get("FREECAD_GUI_TESTS"),
    reason="set FREECAD_BIN and FREECAD_GUI_TESTS=1 for isolated GUI workflows",
)
@pytest.mark.parametrize(
    "scenario",
    [
        "assembly_motion_and_view",
        "assembly_motion_bindings",
        "assembly_exploded_lifecycle",
        "assembly_bom_lifecycle",
        "assembly_flexible_instances",
        "assembly_instance_sync",
        "assembly_exchange",
        "assembly_joint_families",
        "assembly_external_links",
        "assembly_coupled_motion",
        "assembly_simulation_lifecycle",
        "cam_stock_simulation",
        "cam_arc_geometry",
        "cam_canned_cycles",
        "cam_stock_setup",
        "cam_rotation_center",
        "cam_setup_lifecycle",
        "cam_setup_alignment",
        "cam_model_replacement",
        "cam_postprocessing",
        "cam_gui_dressups",
        "cam_machine_workflow",
        "cam_operation_families",
        "surface_geometry",
        "surface_spline_editing",
        "surface_seams",
        "surface_cut_mesh",
        "surface_face_replacement",
    ],
)
def test_native_gui_workflow(tmp_path, scenario):
    run_freecad_case(
        os.environ["FREECAD_BIN"],
        tmp_path,
        f"""import sys
sys.path.insert(0, {str(CASES)!r})
import workflows
_details_ = getattr(workflows, {scenario!r})({str(ROOT)!r}, {str(tmp_path)!r})
""",
        gui=True,
    )
