"""Checked, staged ASMT exchange using native solver serialization."""

import math
import os
from pathlib import Path
import re
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .assembly_joints import (
        native_assembly,
        assembly_preflight,
        assembly_explicit_solve,
        assembly_solver_joint_copies,
    )
    from .assembly_sync import synchronize_assembly_instances
    from .assembly_state import assembly_moving_components


ASMT_TYPES = {
    "Joints": (
        "AngleJoint",
        "GearJoint",
        "NoRotationJoint",
        "ParallelAxesJoint",
        "PerpendicularJoint",
        "RackPinionJoint",
        "ScrewJoint",
        "ConstantVelocityJoint",
        "FixedJoint",
        "RevoluteJoint",
        "SphericalJoint",
        "UniversalJoint",
        "SphSphJoint",
        "CylSphJoint",
        "RevCylJoint",
        "RevRevJoint",
        "CylindricalJoint",
        "PointInLineJoint",
        "TranslationalJoint",
        "LineInPlaneJoint",
        "PlanarJoint",
        "PointInPlaneJoint",
    ),
    "Motions": ("RotationalMotion", "TranslationalMotion", "GeneralMotion"),
    "Limits": ("RotationLimit", "TranslationLimit"),
}


def asmt_nodes(text):
    lines = text.splitlines()
    if lines[:2] != ["OndselSolver", "Assembly"]:
        raise ValueError("Expected OndselSolver ASMT Assembly header")
    root = {"value": "Assembly", "children": [], "line": 1}
    stack = [root]
    for index, line in enumerate(lines[2:], 2):
        if not line.strip():
            continue
        level = len(line) - len(line.lstrip("\t"))
        if level < 1 or level > len(stack):
            raise ValueError("Invalid ASMT indentation at line " + str(index + 1))
        stack = stack[:level]
        node = {"value": line.strip(), "children": [], "line": index}
        stack[-1]["children"].append(node)
        stack.append(node)
    return root, lines


def asmt_field(node, name):
    found = [child for child in node["children"] if child["value"] == name]
    if len(found) != 1:
        raise ValueError("Expected one ASMT field: " + name)
    return found[0]


def asmt_value(node, name):
    children = asmt_field(node, name)["children"]
    if len(children) != 1 or children[0]["children"]:
        raise ValueError("Expected one ASMT value: " + name)
    return children[0]["value"]


def distance_exchange_type(joint):
    """Disambiguate SphSph/CylSph RTTI corruption from native Distance geometry."""
    geometry = []
    for ref in (joint.Reference1, joint.Reference2):
        element = ref[0].getSubObject(ref[1][0]) if ref[1][0] else None
        kind = getattr(element, "ShapeType", "")
        if kind == "Vertex":
            geometry.append("Point")
        elif kind == "Face":
            geometry.append(type(element.Surface).__name__)
        elif kind == "Edge":
            geometry.append(type(element.Curve).__name__)
        else:
            geometry.append("Other")
    pair = sorted(geometry)
    if pair in (["Point", "Point"], ["Point", "Sphere"], ["Sphere", "Sphere"]):
        return "SphSphJoint"
    if pair in (
        ["Cylinder", "Point"],
        ["Cylinder", "Sphere"],
        ["Sphere", "Toroid"],
        ["Line", "Point"],
    ):
        return "CylSphJoint"
    return None


def inspect_asmt_text(text, repair=False, type_hints=None):
    """Validate exported records/marker links; this is not a solver or importer.

    Some Unix builds slice typeid(...).name() using MSVC-specific offsets.
    Match only the exact known Itanium ABI spelling after those same slices;
    ambiguous/unknown names are errors, never inferred from suffix similarity.
    """
    root, lines = asmt_nodes(text)
    assembly_name = asmt_value(root, "Name")
    repairs, parts, markers, constraints = [], [], set(), {}

    def add_markers(parent, prefix):
        for point in asmt_field(parent, "RefPoints")["children"]:
            for marker in asmt_field(point, "Markers")["children"]:
                name = prefix + "/" + asmt_value(marker, "Name")
                if name in markers:
                    raise ValueError("Duplicate ASMT marker: " + name)
                markers.add(name)

    add_markers(root, "/" + assembly_name)
    for part in asmt_field(root, "Parts")["children"]:
        if part["value"] != "Part":
            raise ValueError("Unknown ASMT part record")
        name = asmt_value(part, "Name")
        if name in parts:
            raise ValueError("Duplicate ASMT part: " + name)
        for field, rows in (("Position3D", 1), ("RotationMatrix", 3)):
            values = asmt_field(part, field)["children"]
            if len(values) != rows:
                raise ValueError("Invalid ASMT " + field)
            for row in values:
                numbers = [float(value) for value in row["value"].split()]
                if len(numbers) != 3 or not all(map(math.isfinite, numbers)):
                    raise ValueError("Non-finite or invalid ASMT " + field)
        parts.append(name)
        add_markers(part, "/" + assembly_name + "/" + name)
    groups = asmt_field(root, "ConstraintSets")
    for group, allowed in ASMT_TYPES.items():
        records = []
        for node in asmt_field(groups, group)["children"]:
            kind = node["value"]
            if kind not in allowed:
                candidates = [
                    label
                    for label in allowed
                    if ("N3MbD" + str(len("ASMT" + label)) + "ASMT" + label + "E")[15:]
                    == kind
                ]
                if len(candidates) > 1:
                    hint = (type_hints or {}).get(asmt_value(node, "Name"))
                    candidates = [label for label in candidates if label == hint]
                if not repair or len(candidates) != 1:
                    raise ValueError("Unknown ASMT " + group + " type: " + kind)
                kind = candidates[0]
                repairs.append(
                    {"line": node["line"] + 1, "from": node["value"], "to": kind}
                )
                lines[node["line"]] = "\t\t\t" + kind
            record = {"name": asmt_value(node, "Name"), "type": kind}
            for field in ("MarkerI", "MarkerJ"):
                reference = asmt_value(node, field)
                if reference not in markers:
                    raise ValueError("Unresolved ASMT marker: " + reference)
                record[field] = reference
            records.append(record)
        if len({r["name"] for r in records}) != len(records):
            raise ValueError("Duplicate ASMT " + group + " names")
        constraints[group.lower()] = records
    for field in ("SimulationParameters", "AnimationParameters"):
        asmt_field(root, field)
    return {
        "format": "OndselSolver ASMT",
        "assembly_name": assembly_name,
        "parts": parts,
        **constraints,
        "type_name_repairs": repairs,
        "validation_scope": "header, sections, part poses, unique names, constraint types and marker references; not native reimport or dynamics validation",
    }, "\n".join(lines) + "\n"


def export_assembly_asmt(assembly_name, file_path, overwrite=False):
    assembly = native_assembly(assembly_name)
    path = Path(file_path).expanduser().absolute()
    if path.suffix.lower() != ".asmt":
        raise ValueError("ASMT output must have .asmt extension")
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("ASMT destination must be a regular file")
    if not path.parent.is_dir():
        raise FileNotFoundError(str(path.parent))
    issues, _, _ = assembly_preflight(assembly)
    if issues:
        raise ValueError("Repair Assembly before ASMT export: " + str(issues))
    sync = synchronize_assembly_instances(assembly)
    issues, active, grounds = assembly_preflight(assembly)
    if issues:
        raise ValueError(
            "Repair synchronized Assembly before ASMT export: " + str(issues)
        )
    poses = {obj: obj.Placement.copy() for obj in assembly_moving_components(assembly)}
    # Native Screw/Distance export may swap connector order while building MbD.
    joint_values = {
        joint: {
            key: getattr(joint, key)
            for key in (
                "Reference1",
                "Reference2",
                "Placement1",
                "Placement2",
                "Offset1",
                "Offset2",
                "Detach1",
                "Detach2",
            )
        }
        for joint in active
    }
    handle, filename = tempfile.mkstemp(
        prefix=".mcp-asmt-", suffix=".asmt", dir=path.parent
    )
    os.close(handle)
    staged = Path(filename)
    try:
        with assembly_explicit_solve():
            try:
                with assembly_solver_joint_copies(assembly) as promoted:
                    assembly.exportAsASMT(str(staged))
                    raw = staged.read_text(encoding="utf-8")
                    # Temporary promoted names must not leak into the exchange file.
                    for name, copied in promoted.items():
                        original = assembly.Document.Name + "#" + name
                        temporary = assembly.Document.Name + "#" + copied.Name
                        raw = re.sub(
                            re.escape(temporary) + r"(?=$|[/\r\n-])",
                            lambda match: original,
                            raw,
                        )
                    hints = {
                        j.Document.Name + "#" + j.Name: distance_exchange_type(j)
                        for j in active
                        if j.JointType == "Distance"
                    }
                    data, normalized = inspect_asmt_text(
                        raw, repair=True, type_hints=hints
                    )
                    names = {record["name"] for record in data["joints"]}
                    missing = [
                        j.Name
                        for j in active
                        if j.Document.Name + "#" + j.Name not in names
                    ]
                    if missing:
                        raise ValueError(
                            "Native exporter omitted active joints: " + str(missing)
                        )
            finally:
                for joint, values in joint_values.items():
                    for key, value in values.items():
                        if getattr(joint, key) != value:
                            setattr(joint, key, value)
                for obj, pose in poses.items():
                    if not obj.Placement.isSame(pose, 1e-10):
                        obj.Placement = pose
        staged.write_text(normalized, encoding="utf-8")
        # Validate normalized bytes again; never publish an unknown native type.
        inspect_asmt_text(normalized)
        missing_parts = [
            obj.Name
            for obj in poses
            if obj.Document.Name + "#" + obj.Name not in data["parts"]
        ]
        result = {
            "assembly": assembly.Name,
            "path": str(path),
            "exists": True,
            "size": staged.stat().st_size,
            "status": "exported",
            "content": data,
            "instance_synchronization": sync,
            "promoted_nested_joints": list(promoted),
            "omitted_components": missing_parts,
            "all_components_exported": not missing_parts,
            "grounded_components": sorted(grounds),
            "scope": "native solver model; CAD shapes, physical mass/inertia and FreeCAD Simulation motions are not exported",
        }
        if overwrite:
            os.replace(staged, path)
        else:
            # Atomic publication without a race that overwrites a new file.
            os.link(staged, path)
        return result
    finally:
        staged.unlink(missing_ok=True)
