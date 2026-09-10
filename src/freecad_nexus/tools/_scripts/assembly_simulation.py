"""Native simulation lifecycle and stale-frame detection across bridge calls."""

import hashlib
import json
import math
import re
import sys
import types
from typing import TYPE_CHECKING

import FreeCAD as App
import UtilsAssembly

if TYPE_CHECKING:
    from .assembly_sync import (
        synchronize_assembly_instances,
        instance_motion_status,
        bind_instance_motion,
    )
    from .assembly_joints import (
        native_assembly,
        assembly_explicit_solve,
        assembly_preflight,
        assembly_solver_joint_copies,
    )
    from .assembly_state import json_value, assembly_joint_objects

_state_module = "_freecad_nexus_assembly_simulations"
if _state_module not in sys.modules:
    sys.modules[_state_module] = types.SimpleNamespace(records={})
_simulation_records = sys.modules[_state_module].records
# Release closed/deleted native objects on the next MCP simulation operation.
for _key, _record in list(_simulation_records.items()):
    _document = App.listDocuments().get(_key[0])
    if _document is None or _document.getObject(_key[1]) is not _record["assembly"]:
        _simulation_records.pop(_key, None)


def native_simulation(name):
    import CommandCreateSimulation

    doc = App.ActiveDocument
    simulation = doc.getObject(name) if doc else None
    if not simulation or not isinstance(
        getattr(simulation, "Proxy", None), CommandCreateSimulation.Simulation
    ):
        raise ValueError("Native Assembly simulation not found: " + name)
    assemblies = [
        obj
        for obj in simulation.InList
        if obj.isDerivedFrom("Assembly::AssemblyObject")
    ]
    if len(assemblies) != 1:
        raise ValueError("Simulation must belong to exactly one Assembly")
    return assemblies[0], simulation


def simulation_signature(assembly, simulation, include_inputs=False):
    """Record document geometry, structure, placements and simulation inputs.

    Include linked external sources too. Refresh after our frame application
    so a later user placement edit cannot silently reuse stale frames.
    """
    objects = list(assembly.Document.Objects)
    seen, records = set(), []
    while objects:
        obj = objects.pop()
        identity = (obj.Document.Name, obj.Name)
        if identity in seen:
            continue
        seen.add(identity)
        properties = {}
        for key in obj.PropertiesList:
            # ShapeMaterial is a display-material map synchronized lazily by
            # the GUI; it changes between network calls without geometry edits.
            # The installed kinematic solver does not use display appearance.
            if key in ("Proxy", "Shape", "_Part_ShapeCache", "ShapeMaterial"):
                continue
            properties[key] = json_value(getattr(obj, key))
        if hasattr(obj, "Shape"):
            shape = obj.Shape
            # OCC hashCode uses transient TShape identity. Link/Datum shapes
            # can be rebuilt between calls despite unchanged geometry.
            if shape.isNull():
                properties["shape_digest"] = None
            else:
                # Export a private topology copy with no viewer triangulation.
                stable = shape.copy(True, False).cleaned()
                brep = stable.exportBrepToString()
                # BREP TShape flags: Free, Modified, Checked are cache state;
                # retain Orientable, Closed, Infinite, Convex and all geometry.
                brep = re.sub(r"(?m)^[01]{3}([01]{4})$", r"000\1", brep)
                properties["shape_digest"] = hashlib.sha256(brep.encode()).hexdigest()
        if hasattr(obj, "getLinkedObject"):
            linked = obj.getLinkedObject()
            if linked and linked is not obj:
                objects.append(linked)
        records.append([identity, properties])
    records.sort(key=lambda item: item[0])
    encoded = json.dumps([simulation.Name, records], sort_keys=True, allow_nan=False)
    signature = hashlib.sha256(encoded.encode()).hexdigest()
    if include_inputs:
        inputs = {
            document + "#" + name: {
                key: hashlib.sha256(
                    json.dumps(value, sort_keys=True, allow_nan=False).encode()
                ).hexdigest()
                for key, value in properties.items()
            }
            for (document, name), properties in records
        }
        return signature, inputs
    return signature


def simulation_configuration_issues(assembly, simulation):
    """Inspect every member so broken simulations can be repaired incrementally."""
    import CommandCreateSimulation

    issues = []
    bindings = {r["motion"]: r for r in instance_motion_status(assembly)}
    if not simulation.Group:
        issues.append(
            {
                "kind": "empty_simulation",
                "reason": "Simulation requires at least one motion",
            }
        )
    seen = {}
    joints = assembly_joint_objects(assembly)
    for motion in simulation.Group:

        def issue(kind, reason, **details):
            issues.append(
                {"motion": motion.Name, "kind": kind, "reason": reason, **details}
            )

        if not isinstance(
            getattr(motion, "Proxy", None), CommandCreateSimulation.Motion
        ):
            issue("invalid_member", "Simulation contains a non-Motion object")
            continue
        try:
            owner, parent, _ = native_motion(motion.Name)
            if owner is not assembly or parent is not simulation:
                raise ValueError("Motion belongs to another Simulation or Assembly")
        except ValueError as exc:
            issue("invalid_ownership", str(exc))
        binding = bindings.get(motion.Name)
        if binding and binding["status"] != "current":
            issue(
                "instance_target",
                "Instance motion target unavailable; use assembly_edit_motion(joint_name=...) to establish an explicit target",
                binding=binding,
            )
        joint = motion.Joint[0] if motion.Joint else None
        if joint not in joints or getattr(joint, "Suppressed", False):
            issue(
                "invalid_joint",
                "Motion must reference an active joint of this Assembly",
            )
        allowed = {
            "Revolute": ("Angular",),
            "Slider": ("Linear",),
            "Cylindrical": ("Angular", "Linear"),
        }
        if joint:
            if motion.MotionType not in allowed.get(
                getattr(joint, "JointType", None), ()
            ):
                issue(
                    "invalid_coordinate", "Motion coordinate does not match joint type"
                )
            coordinate = joint.Document.Name, joint.Name, motion.MotionType
            if coordinate in seen:
                # Both members must be attributed so editing either one cannot
                # introduce a duplicate just because it occurs first in Group.
                issue(
                    "duplicate_coordinate",
                    "Duplicate motion for joint coordinate",
                    other_motion=seen[coordinate],
                )
                issues.append(
                    {
                        "motion": seen[coordinate],
                        "kind": "duplicate_coordinate",
                        "reason": "Duplicate motion for joint coordinate",
                        "other_motion": motion.Name,
                    }
                )
            seen[coordinate] = motion.Name
        if not motion.Formula.strip():
            issue("empty_formula", "Motion formula cannot be empty")
    start, end, step = (
        simulation.aTimeStart.Value,
        simulation.bTimeEnd.Value,
        simulation.cTimeStepOutput.Value,
    )
    tolerance, fps = simulation.fGlobalErrorTolerance, simulation.jFramesPerSecond
    if (
        not all(math.isfinite(x) for x in (start, end, step, tolerance, fps))
        or start >= end
        or step <= 0
        or tolerance <= 0
        or fps <= 0
    ):
        issues.append(
            {
                "kind": "invalid_timing",
                "reason": "Simulation requires finite start < end and positive step, tolerance and frame rate",
            }
        )
    return issues


def simulation_motion_checks(assembly, simulation, edited_motion=None):
    issues = simulation_configuration_issues(assembly, simulation)
    if edited_motion is not None:
        # Repair the selected Motion even if another member is still broken.
        # Generation continues to require the entire configuration to be valid.
        issues = [item for item in issues if item.get("motion") == edited_motion.Name]
    if issues:
        raise ValueError("Simulation configuration invalid: " + str(issues))


def run_assembly_simulation(simulation_name):
    assembly, simulation = native_simulation(simulation_name)
    key = assembly.Document.Name, assembly.Name
    # Cache invalidation is not rolled back by a document transaction: partial
    # solver output must never become usable again after a failed rerun.
    _simulation_records.pop(key, None)
    synchronization = synchronize_assembly_instances(assembly)
    simulation_motion_checks(assembly, simulation)
    issues, active, grounds = assembly_preflight(assembly)
    if issues or not grounds:
        raise ValueError(
            "Simulation prerequisites failed: " + str(issues or "No grounded component")
        )
    with assembly_explicit_solve():
        assembly.Document.recompute()
        with assembly_solver_joint_copies(assembly, simulation) as promoted:
            code = assembly.generateSimulation(simulation)
        count = assembly.numberOfFrames()
        if code != 0 or count <= 0:
            raise RuntimeError(
                "Native simulation failed with code "
                + str(code)
                + "; partial frames are invalid"
            )
    signature, inputs = simulation_signature(assembly, simulation, True)
    _simulation_records[key] = {
        "assembly": assembly,
        "simulation": simulation,
        "frames": count,
        "signature": signature,
        "inputs": inputs,
    }
    return {
        "simulation": simulation.Name,
        "frames": count,
        "native_result": code,
        "step": simulation.cTimeStepOutput.Value,
        "time_unit": "s",
        "joint_limits_applied": False,
        "frame_status": "current",
        "instance_synchronization": synchronization,
        "promoted_nested_joints": list(promoted),
    }


def assembly_frame_state(assembly):
    key = assembly.Document.Name, assembly.Name
    record = _simulation_records.get(key)
    if record is None or record["assembly"] is not assembly:
        return {
            "assembly": assembly.Name,
            "valid": False,
            "status": "not_generated",
            "reason": "Generate simulation through MCP in the current document session",
        }
    changes, native_count = [], None
    try:
        simulation = record["simulation"]
        current = assembly.Document.getObject(simulation.Name) is simulation
        if current:
            signature, inputs = simulation_signature(assembly, simulation, True)
            old = record.get("inputs", {})
            for name in sorted(old.keys() | inputs.keys()):
                before, after = old.get(name, {}), inputs.get(name, {})
                fields = sorted(
                    k
                    for k in before.keys() | after.keys()
                    if before.get(k) != after.get(k)
                )
                if fields:
                    changes.append({"object": name, "properties": fields})
            current = signature == record["signature"]
        native_count = assembly.numberOfFrames()
        current = current and native_count == record["frames"]
    except (ReferenceError, RuntimeError, AttributeError):
        current = False
    if not current:
        return {
            "assembly": assembly.Name,
            "valid": False,
            "status": "stale",
            "reason": "Document, motion inputs or native frame storage changed; rerun simulation",
            "changed_inputs": changes,
            "native_frame_count": native_count,
            "expected_frame_count": record["frames"],
        }
    return {
        "assembly": assembly.Name,
        "valid": True,
        "status": "current",
        "simulation": simulation.Name,
        "frame_count": record["frames"],
    }


def apply_assembly_frame(assembly, frame):
    status = assembly_frame_state(assembly)
    if not status["valid"]:
        raise ValueError(str(status))
    if type(frame) is not int or not 0 <= frame < status["frame_count"]:
        raise IndexError("Frame index outside available simulation frames")
    key = assembly.Document.Name, assembly.Name
    record = _simulation_records.pop(key)
    with assembly_explicit_solve():
        assembly.updateForFrame(frame)
    record["signature"], record["inputs"] = simulation_signature(
        assembly, record["simulation"], True
    )
    _simulation_records[key] = record
    return {"assembly": assembly.Name, "frame": frame, "frame_count": record["frames"]}


def native_motion(name):
    import CommandCreateSimulation

    doc = App.ActiveDocument
    motion = doc.getObject(name) if doc else None
    if not motion or not isinstance(
        getattr(motion, "Proxy", None), CommandCreateSimulation.Motion
    ):
        raise ValueError("Native Assembly motion not found: " + name)
    simulations = [
        obj
        for obj in doc.Objects
        if isinstance(getattr(obj, "Proxy", None), CommandCreateSimulation.Simulation)
        and motion in obj.Group
    ]
    if len(simulations) != 1:
        raise ValueError("Motion must belong to exactly one Simulation")
    assembly, simulation = native_simulation(simulations[0].Name)
    return assembly, simulation, motion


def simulation_data(simulation_name):
    import CommandCreateSimulation

    assembly, simulation = native_simulation(simulation_name)
    bindings = {row["motion"]: row for row in instance_motion_status(assembly)}
    issues = simulation_configuration_issues(assembly, simulation)
    return {
        "name": simulation.Name,
        "assembly": assembly.Name,
        "start": simulation.aTimeStart.Value,
        "end": simulation.bTimeEnd.Value,
        "step": simulation.cTimeStepOutput.Value,
        "error_tolerance": simulation.fGlobalErrorTolerance,
        "frames_per_second": simulation.jFramesPerSecond,
        "motions": [
            {
                "name": m.Name,
                "is_motion": isinstance(
                    getattr(m, "Proxy", None), CommandCreateSimulation.Motion
                ),
                "joint": m.Joint[0].Name
                if getattr(m, "Joint", None) and m.Joint[0]
                else None,
                "motion_type": getattr(m, "MotionType", None),
                "formula": getattr(m, "Formula", None),
                "binding": bindings.get(m.Name),
            }
            for m in simulation.Group
        ],
        "configuration_valid": not issues,
        "issues": issues,
        "frame_state": assembly_frame_state(assembly),
    }


def edit_assembly_motion(name, joint_name=None, motion_type=None, formula=None):
    assembly, simulation, motion = native_motion(name)
    with assembly_explicit_solve():
        if joint_name is not None:
            synchronize_assembly_instances(assembly)
            joint = assembly.Document.getObject(joint_name)
            if joint not in assembly_joint_objects(assembly) or joint.Suppressed:
                raise ValueError("Select an active joint belonging to this Assembly")
            motion.Joint = joint
            bind_instance_motion(assembly, motion, joint)
        if motion_type is not None:
            if motion_type not in ("Angular", "Linear"):
                raise ValueError("Motion type must be Angular or Linear")
            motion.MotionType = motion_type
        if formula is not None:
            if not formula.strip():
                raise ValueError("Motion formula cannot be empty")
            motion.setExpression("Formula", None)
            motion.Formula = formula
        synchronize_assembly_instances(assembly)
        simulation_motion_checks(assembly, simulation, edited_motion=motion)
        assembly.Document.recompute()
    _simulation_records.pop((assembly.Document.Name, assembly.Name), None)
    return simulation_data(simulation.Name)


def edit_assembly_simulation(
    name, start, end, step, error_tolerance, frames_per_second
):
    assembly, simulation = native_simulation(name)
    values = {
        "aTimeStart": simulation.aTimeStart.Value if start is None else start,
        "bTimeEnd": simulation.bTimeEnd.Value if end is None else end,
        "cTimeStepOutput": simulation.cTimeStepOutput.Value if step is None else step,
        "fGlobalErrorTolerance": simulation.fGlobalErrorTolerance
        if error_tolerance is None
        else error_tolerance,
        "jFramesPerSecond": simulation.jFramesPerSecond
        if frames_per_second is None
        else frames_per_second,
    }
    if not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        for v in values.values()
    ):
        raise ValueError("Simulation inputs must be finite numbers")
    if (
        values["aTimeStart"] >= values["bTimeEnd"]
        or values["cTimeStepOutput"] <= 0
        or values["fGlobalErrorTolerance"] <= 0
        or values["jFramesPerSecond"] <= 0
        or int(values["jFramesPerSecond"]) != values["jFramesPerSecond"]
    ):
        raise ValueError(
            "Require start < end, positive step/tolerance and positive integer frame rate"
        )
    with assembly_explicit_solve():
        supplied = dict(
            zip(values, (start, end, step, error_tolerance, frames_per_second))
        )
        for key, value in values.items():
            if supplied[key] is None:
                continue
            simulation.setExpression(key, None)
            setattr(simulation, key, int(value) if key == "jFramesPerSecond" else value)
        assembly.Document.recompute()
    _simulation_records.pop((assembly.Document.Name, assembly.Name), None)
    return simulation_data(name)


def remove_assembly_motion(name):
    assembly, simulation, motion = native_motion(name)
    allowed = {assembly, simulation}
    users = [obj.Name for obj in motion.InList if obj not in allowed]
    if users:
        raise ValueError("Motion has other dependent objects: " + str(users))
    with assembly_explicit_solve():
        assembly.Document.removeObject(motion.Name)
        assembly.Document.recompute()
    _simulation_records.pop((assembly.Document.Name, assembly.Name), None)
    return {
        "removed": name,
        "simulation": simulation.Name,
        "remaining_motions": [m.Name for m in simulation.Group],
    }


def remove_assembly_simulation(name):
    assembly, simulation = native_simulation(name)
    motions = list(simulation.Group)
    for motion in motions:
        native_motion(motion.Name)  # Reject shared Motion ownership.
        if any(obj not in (assembly, simulation) for obj in motion.InList):
            raise ValueError(
                "Simulation motion has other dependent objects: " + motion.Name
            )
    if any(
        obj is not assembly
        and not (
            obj.isDerivedFrom("Assembly::SimulationGroup")
            and simulation in obj.Group
            and obj in assembly.Group
        )
        for obj in simulation.InList
    ):
        raise ValueError("Simulation has other dependent objects")
    removed = [m.Name for m in motions]
    with assembly_explicit_solve():
        for motion in motions:
            assembly.Document.removeObject(motion.Name)
        assembly.Document.removeObject(name)
        assembly.Document.recompute()
    _simulation_records.pop((assembly.Document.Name, assembly.Name), None)
    return {"removed": name, "removed_motions": removed, "assembly": assembly.Name}


def create_assembly_simulation(assembly_name, start, end, step, tolerance, fps):
    import CommandCreateSimulation

    assembly = native_assembly(assembly_name)
    with assembly_explicit_solve():
        group = UtilsAssembly.getSimulationGroup(assembly)
        simulation = group.newObject("App::FeaturePython", "Simulation")
        CommandCreateSimulation.Simulation(simulation)
        if App.GuiUp:
            CommandCreateSimulation.ViewProviderSimulation(simulation.ViewObject)
        data = edit_assembly_simulation(
            simulation.Name, start, end, step, tolerance, fps
        )
    return data | {"time_unit": "s"}


def add_assembly_motion(simulation_name, joint_name, motion_type, formula):
    import CommandCreateSimulation

    assembly, simulation = native_simulation(simulation_name)
    # Binding is established against the current native source/group order.
    # Synchronize before interpreting the chosen copy's present slot identity.
    synchronize_assembly_instances(assembly)
    joint = assembly.Document.getObject(joint_name)
    if joint not in assembly_joint_objects(assembly) or joint.Suppressed:
        raise ValueError("Select an active joint belonging to this Assembly")
    if motion_type not in ("Angular", "Linear"):
        raise ValueError("Motion type must be Angular or Linear")
    if not isinstance(formula, str) or not formula.strip():
        raise ValueError("Motion formula cannot be empty")
    with assembly_explicit_solve():
        motion = assembly.newObject("App::FeaturePython", "Motion")
        CommandCreateSimulation.Motion(motion, motion_type, joint, formula)
        bind_instance_motion(assembly, motion, joint)
        if App.GuiUp:
            CommandCreateSimulation.ViewProviderMotion(motion.ViewObject)
        simulation.Group = list(simulation.Group) + [motion]
        simulation_motion_checks(assembly, simulation, edited_motion=motion)
        assembly.Document.recompute()
    _simulation_records.pop((assembly.Document.Name, assembly.Name), None)
    return {
        "name": motion.Name,
        "motion_type": motion.MotionType,
        "formula": motion.Formula,
        "joint": joint.Name,
        "simulation": simulation.Name,
        "motion_count": len(simulation.Group),
    }
