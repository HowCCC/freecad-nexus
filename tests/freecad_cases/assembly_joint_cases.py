"""Native mechanisms and invalid-input regression tests via registered adapters."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    import UtilsAssembly
    from workflows import tools_for

    call = tools_for(root, "assembly")
    prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Assembly")
    prefs.SetBool("SolveOnRecompute", False)
    evidence = {}

    def components(name, names=("Base", "Moving")):
        doc = App.newDocument(name)
        call("assembly_create", name="Asm")
        source = doc.addObject("Part::Feature", "Source")
        source.Shape = Part.makeBox(2, 2, 2)
        for n in names:
            call(
                "assembly_insert_component",
                assembly_name="Asm",
                source_name="Source",
                instance_name=n,
            )
        return doc

    def connector(joint, index):
        return UtilsAssembly.getJcsGlobalPlc(
            getattr(joint, "Placement" + str(index)),
            getattr(joint, "Reference" + str(index)),
        )

    def check(kind, joint):
        first, second = connector(joint, 1), connector(joint, 2)
        relative = first.inverse() * second
        z = relative.Rotation.multVec(App.Vector(0, 0, 1))
        if kind in ("Fixed", "Revolute", "Ball"):
            assert relative.Base.Length < 1e-6, (kind, relative)
        if kind in ("Fixed", "Slider"):
            assert abs(relative.Rotation.Angle) < 1e-6, (kind, relative)
        if kind in ("Revolute", "Cylindrical", "Parallel"):
            assert abs(abs(z.z) - 1) < 1e-6, (kind, relative)
        if kind in ("Cylindrical", "Slider"):
            assert math.hypot(relative.Base.x, relative.Base.y) < 1e-6, (kind, relative)
        if kind == "Distance":
            assert abs(relative.Base.Length - 5) < 1e-6, (kind, relative)
        if kind == "Perpendicular":
            assert abs(z.z) < 1e-6, (kind, relative)
        if kind == "Angle":
            assert abs(z.z - math.cos(math.radians(40))) < 1e-6, (kind, relative)
        return {"relative_position": list(relative.Base), "relative_z_axis": list(z)}

    for kind in (
        "Fixed",
        "Revolute",
        "Cylindrical",
        "Slider",
        "Ball",
        "Distance",
        "Parallel",
        "Perpendicular",
        "Angle",
    ):
        doc = components(kind)
        call("assembly_ground_component", assembly_name="Asm", component_name="Base")
        doc.Moving.Placement = App.Placement(
            App.Vector(7, 3, 9), App.Rotation(App.Vector(1, 2, 3), 23)
        )
        kwargs = (
            {"value": 5, "first_element": "Vertex1", "second_element": "Vertex1"}
            if kind == "Distance"
            else {"value": 40}
            if kind == "Angle"
            else {}
        )
        result = call(
            "assembly_add_constraint",
            assembly_name="Asm",
            constraint_type=kind,
            first="Base",
            second="Moving",
            **kwargs,
        )
        assert result["solver"]["solved"], result
        joint = doc.getObject(result["name"])
        if kind in ("Angle", "Perpendicular"):
            grounded_pose = doc.Base.Placement.copy()
            for initial_angle in (0, 180):
                doc.Moving.Placement = App.Placement(
                    App.Vector(4, 5, 6),
                    App.Rotation(App.Vector(1, 0, 0), initial_angle),
                )
                singular_pose = doc.Moving.Placement.copy()
                solved = call(
                    "assembly_solve", assembly_name="Asm", store_previous=True
                )
                assert solved["solved"], solved
                check(kind, joint)
                assert doc.Base.Placement.isSame(grounded_pose, 1e-8)
                call("assembly_undo_solve", assembly_name="Asm")
                assert doc.Moving.Placement.isSame(singular_pose, 1e-8)
            # The pre-positioner must also choose correctly when Reference2 is fixed.
            call(
                "assembly_set_joint_references",
                joint_name=joint.Name,
                first="Moving",
                second="Base",
                solve=False,
            )
            doc.Moving.Placement = App.Placement()
            solved = call("assembly_solve", assembly_name="Asm")
            assert solved["solved"], solved
            check(kind, joint)
            assert doc.Base.Placement.isSame(grounded_pose, 1e-8)
            call(
                "assembly_set_joint_references",
                joint_name=joint.Name,
                first="Base",
                second="Moving",
                solve=False,
            )
        # Deliberately disturb placement after creation. The solve, not the
        # native pre-positioning callback, must restore geometric invariants.
        doc.Moving.Placement = App.Placement(
            App.Vector(5, 2, 8), App.Rotation(App.Vector(1, 1, 1), 15)
        )
        solved = call("assembly_solve", assembly_name="Asm", store_previous=True)
        assert solved["solved"], solved
        evidence[kind] = check(kind, joint)
        exchange = call(
            "assembly_export_asmt",
            assembly_name="Asm",
            file_path=str(Path(output) / (kind + ".asmt")),
        )
        assert exchange["all_components_exported"], exchange
        exported = next(
            row
            for row in exchange["content"]["joints"]
            if row["name"] == doc.Name + "#" + joint.Name
        )
        expected_type = {
            "Ball": "Spherical",
            "Slider": "Translational",
            "Parallel": "ParallelAxes",
            "Distance": "SphSph",
        }.get(kind, kind)
        assert exported["type"] == expected_type + "Joint", exported
        filename = str(Path(output) / (kind + ".FCStd"))
        doc.recompute()
        doc.saveAs(filename)
        App.closeDocument(doc.Name)
        doc = App.openDocument(filename)
        assert call("assembly_solve", assembly_name="Asm")["solved"]
        check(kind, doc.getObject(result["name"]))
        App.closeDocument(doc.Name)

    doc = components("InvalidInputs")
    # A no-ground model may be constructed, but it is never reported solved.
    result = call(
        "assembly_add_constraint",
        assembly_name="Asm",
        constraint_type="Slider",
        first="Base",
        second="Moving",
    )
    assert result["solver"]["status"] == "no_grounded_component"
    ground = call(
        "assembly_ground_component", assembly_name="Asm", component_name="Base"
    )
    assert (
        call("assembly_ground_component", assembly_name="Asm", component_name="Base")[
            "joint"
        ]
        == ground["joint"]
    )
    joint = doc.getObject(result["name"])
    # Native runPreDrag returns 0 outside configured limits in this build.
    # Never misreport that as a fully satisfied mechanism.
    limits = call(
        "assembly_set_joint_limits",
        joint_name=joint.Name,
        enable_length_min=True,
        length_min=-2,
        enable_length_max=True,
        length_max=3,
    )
    assert limits["solver"]["solved"], limits
    for start, target in ((-9, -2), (9, 3)):
        doc.Moving.Placement.Base = App.Vector(0, 0, start)
        solved = call("assembly_solve", assembly_name="Asm")
        assert (
            solved["native_solve_succeeded"]
            and solved["status"] == "joint_limit_violation"
        ), solved
        relative = connector(joint, 1).inverse() * connector(joint, 2)
        assert abs(relative.Base.z - start) < 1e-6, relative
    doc.Moving.Placement.Base = App.Vector(0, 0, 0)
    snapshot = (joint.LengthMin.Value, joint.LengthMax.Value, joint.Suppressed)
    call(
        "assembly_set_joint_limits",
        joint_name=joint.Name,
        enable_length_min=True,
        length_min=5,
        enable_length_max=True,
        length_max=1,
        expect_success=False,
    )
    call(
        "assembly_set_joint_limits",
        joint_name=joint.Name,
        enable_angle_max=True,
        angle_max=20,
        expect_success=False,
    )
    call(
        "assembly_set_joint_state",
        joint_name=joint.Name,
        angle=30,
        expect_success=False,
    )
    call(
        "assembly_set_joint_property",
        joint_name=joint.Name,
        property_name="Suppressed",
        value="false",
        expect_success=False,
    )
    assert snapshot == (joint.LengthMin.Value, joint.LengthMax.Value, joint.Suppressed)
    call("assembly_set_joint_state", joint_name=joint.Name, suppressed=True)
    assert joint.Suppressed is True
    doc.Moving.Placement.Base = App.Vector(4, 5, 6)
    assert call("assembly_solve", assembly_name="Asm")["solved"]
    assert (doc.Moving.Placement.Base - App.Vector(4, 5, 6)).Length < 1e-7
    doc.Moving.Placement.Base = App.Vector(0, 0, 0)
    call("assembly_set_joint_state", joint_name=joint.Name, suppressed=False)
    call("assembly_set_joint_limits", joint_name=joint.Name)
    doc.Moving.Placement = App.Placement(
        App.Vector(5, 2, 8), App.Rotation(App.Vector(1, 1, 1), 15)
    )
    disturbed = doc.Moving.Placement.copy()
    assert call("assembly_solve", assembly_name="Asm", store_previous=True)["solved"]
    call("assembly_undo_solve", assembly_name="Asm")
    assert (doc.Moving.Placement.Base - disturbed.Base).Length < 1e-7
    assert doc.Moving.Placement.Rotation.isSame(disturbed.Rotation, 1e-7)
    before = [o.Name for o in doc.Objects]
    for kwargs in (
        {"first": "Base", "second": "Base"},
        {"first": "Source", "second": "Moving"},
        {"first": "Base", "second": "Moving", "first_element": "Face999"},
        {"first": "Base", "second": "Moving", "value": 2},
    ):
        call(
            "assembly_add_constraint",
            assembly_name="Asm",
            constraint_type="Fixed",
            expect_success=False,
            **kwargs,
        )
        assert before == [o.Name for o in doc.Objects]
    for kind, kwargs in [
        ("Gears", {"value": 0, "distance2": 2}),
        ("Screw", {"value": 5}),
        ("RackPinion", {"value": 3}),
    ]:
        # Screw against a Slider may be allowed, so use two newly unsupported parts.
        for n in ("U", "V"):
            if not doc.getObject(n):
                call(
                    "assembly_insert_component",
                    assembly_name="Asm",
                    source_name="Source",
                    instance_name=n,
                )
        snapshot = [o.Name for o in doc.Objects]
        call(
            "assembly_add_constraint",
            assembly_name="Asm",
            constraint_type=kind,
            first="U",
            second="V",
            expect_success=False,
            **kwargs,
        )
        assert snapshot == [o.Name for o in doc.Objects]
    # Native .Joints can delete incoherent joints; validation must preserve them.
    joint.Reference2 = (doc.Moving, ["Face999", ""])
    validation = call("assembly_validate_references", assembly_name="Asm")
    assert not validation["valid"], validation
    solved = call("assembly_solve", assembly_name="Asm")
    assert solved["status"] == "invalid_input", solved
    assert doc.getObject(joint.Name) is joint
    inspection = call("assembly_inspect", assembly_name="Asm")
    graph = call("assembly_connection_graph", assembly_name="Asm")
    assert joint.Name in [j["name"] for j in inspection["joints"]]
    assert joint.Name in [edge["joint"] for edge in graph["edges"]]
    assert doc.getObject(joint.Name) is joint
    repaired = call(
        "assembly_set_joint_references",
        joint_name=joint.Name,
        first="Base",
        second="Moving",
    )
    assert repaired["solver"]["solved"], repaired
    assert call("assembly_validate_references", assembly_name="Asm")["valid"]
    swapped = call(
        "assembly_set_joint_references",
        joint_name=joint.Name,
        first="Moving",
        second="Base",
    )
    assert swapped["solver"]["solved"] and joint.Reference1[0] is doc.Moving
    call(
        "assembly_set_joint_references",
        joint_name=joint.Name,
        first="Base",
        second="Moving",
    )
    offset = call(
        "assembly_set_joint_offset", joint_name=joint.Name, connector=1, x=2, y=3, z=4
    )
    assert offset["offset"]["base"] == [2, 3, 4] and not joint.Detach1
    relative = connector(joint, 1).inverse() * connector(joint, 2)
    assert math.hypot(relative.Base.x, relative.Base.y) < 1e-7
    # Failed reconnect leaves the original reference pair and offset intact.
    call(
        "assembly_set_joint_references",
        joint_name=joint.Name,
        first="Base",
        second="Moving",
        second_element="Face999",
        expect_success=False,
    )
    assert joint.Reference2[1][0] == "" and list(joint.Offset1.Base) == [2, 3, 4]
    assert prefs.GetBool("SolveOnRecompute", True) is False
    return evidence
