"""ASMT native serialization, content, failure preservation and model isolation."""

import math
from pathlib import Path


def run(root, output):
    import FreeCAD as App
    from workflows import tools_for

    call = tools_for(root, "assembly")
    doc = App.newDocument("ExchangeLifecycle")
    prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Assembly")
    prefs.SetBool("SolveOnRecompute", False)
    doc.addObject("Part::Box", "Box")
    call("assembly_create", name="Asm")
    for name in ("Base", "Moving", "Loose"):
        call(
            "assembly_insert_component",
            assembly_name="Asm",
            source_name="Box",
            instance_name=name,
        )
    call("assembly_ground_component", assembly_name="Asm", component_name="Base")
    created = call(
        "assembly_add_constraint",
        assembly_name="Asm",
        constraint_type="Cylindrical",
        first="Base",
        second="Moving",
    )
    joint = doc.getObject(created["name"])
    call(
        "assembly_set_joint_limits",
        joint_name=joint.Name,
        enable_length_min=True,
        length_min=-3,
        enable_angle_max=True,
        angle_max=25,
        solve=False,
    )
    doc.Moving.Placement = App.Placement(
        App.Vector(2, 4, 6), App.Rotation(App.Vector(1, 2, 3), 20)
    )
    names = {obj.Name for obj in doc.Objects}
    poses = {
        obj.Name: obj.Placement.copy()
        for obj in doc.Objects
        if hasattr(obj, "Placement")
    }
    path = Path(output) / "assembly.asmt"
    exported = call("assembly_export_asmt", assembly_name="Asm", file_path=str(path))
    assert exported["omitted_components"] == ["Loose"], exported
    assert not exported["all_components_exported"]
    assert {row["type"] for row in exported["content"]["limits"]} == {
        "TranslationLimit",
        "RotationLimit",
    }, exported
    assert names == {obj.Name for obj in doc.Objects}
    assert all(
        doc.getObject(name).Placement.isSame(pose, 1e-8) for name, pose in poses.items()
    )
    data = path.read_bytes()
    call(
        "assembly_export_asmt",
        assembly_name="Asm",
        file_path=str(path),
        expect_success=False,
    )
    assert path.read_bytes() == data
    # Invalid native geometry references must preserve the prior destination.
    joint.Reference2 = (doc.Moving, ["Face999", ""])
    call(
        "assembly_export_asmt",
        assembly_name="Asm",
        file_path=str(path),
        overwrite=True,
        expect_success=False,
    )
    assert path.read_bytes() == data and doc.getObject(joint.Name) is joint
    call(
        "assembly_set_joint_references",
        joint_name=joint.Name,
        first="Base",
        second="Moving",
        solve=False,
    )
    doc.Moving.Placement.Base = App.Vector(9, 8, 7)
    call(
        "assembly_export_asmt", assembly_name="Asm", file_path=str(path), overwrite=True
    )
    assert path.read_bytes() != data
    assert not list(Path(output).glob(".mcp-asmt-*"))
    # Point/line Distance uses CylSph, which shares damaged RTTI with SphSph.
    call(
        "assembly_set_joint_state", joint_name=joint.Name, suppressed=True, solve=False
    )
    distance = call(
        "assembly_add_constraint",
        assembly_name="Asm",
        constraint_type="Distance",
        first="Base",
        second="Moving",
        first_element="Vertex1",
        second_element="Edge1",
        value=2,
        solve=False,
    )
    dist = doc.getObject(distance["name"])
    refs = dist.Reference1, dist.Reference2
    exported = call(
        "assembly_export_asmt", assembly_name="Asm", file_path=str(path), overwrite=True
    )
    record = next(
        row
        for row in exported["content"]["joints"]
        if row["name"] == doc.Name + "#" + dist.Name
    )
    assert record["type"] == "CylSphJoint", record
    assert (dist.Reference1, dist.Reference2) == refs
    assert doc.Name + "#" + joint.Name not in {
        row["name"] for row in exported["content"]["joints"]
    }
    # Validate independent numeric content rather than trusting returned records.
    lines = path.read_text().splitlines()
    index = lines.index("\t\t\t\tdistanceIJ")
    assert math.isclose(float(lines[index + 1]), 2, abs_tol=1e-9)
    filename = str(Path(output) / "exchange.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    exported = call(
        "assembly_export_asmt", assembly_name="Asm", file_path=str(path), overwrite=True
    )
    assert exported["omitted_components"] == ["Loose"]
    return {
        "limits": True,
        "distance_type_disambiguation": True,
        "source_references_and_poses_preserved": True,
        "omissions_reported": True,
        "overwrite_and_invalid_reference_preservation": True,
    }
