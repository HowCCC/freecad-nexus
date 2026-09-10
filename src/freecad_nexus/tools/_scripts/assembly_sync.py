"""Audit and synchronize native flexible instance joint parameters."""

from typing import TYPE_CHECKING

import FreeCAD as App

if TYPE_CHECKING:
    from .assembly_state import assembly_moving_components, json_value
    from .assembly_joints import assembly_explicit_solve


INSTANCE_JOINT_PROPERTIES = (
    "JointType",
    "Suppressed",
    "Distance",
    "Distance2",
    "Angle",
    "Offset1",
    "Offset2",
    "Detach1",
    "Detach2",
    "AngleMax",
    "AngleMin",
    "LengthMax",
    "LengthMin",
    "EnableAngleMax",
    "EnableAngleMin",
    "EnableLengthMax",
    "EnableLengthMin",
)


def instance_source_assembly(instance):
    source, seen = instance.LinkedObject, set()
    while source is not None and source.isDerivedFrom("Assembly::AssemblyLink"):
        identity = (source.Document.Name, source.Name)
        if identity in seen:
            raise ValueError("Cyclic AssemblyLink source chain")
        seen.add(identity)
        source = source.LinkedObject
    if source is None or not source.isDerivedFrom("Assembly::AssemblyObject"):
        raise ValueError("AssemblyLink has no native source Assembly")
    return source


def direct_instance_joints(container, active=False):
    joints = [
        j
        for group in container.Group
        if group.isDerivedFrom("Assembly::JointGroup")
        for j in group.Group
        if hasattr(j, "JointType")
    ]
    if not active:
        return joints
    # Match the native source filtering without using .Joints (it can delete).
    return [
        j
        for j in joints
        if not j.Suppressed
        and j.isValid()
        and j.Reference1
        and j.Reference2
        and j.Reference1[0]
        and j.Reference2[0]
        and j.Reference1[0] is not j.Reference2[0]
    ]


def flexible_instances(assembly):
    instances = []

    def visit(container, ancestors):
        if container in ancestors:
            raise ValueError("Cyclic assembly containment")
        for obj in container.Group:
            if obj.isDerivedFrom("Assembly::AssemblyLink") and not obj.Rigid:
                instances.append(obj)
                visit(obj, ancestors + [container])
            elif obj.TypeId == "App::DocumentObjectGroup":
                visit(obj, ancestors + [container])

    visit(assembly, [])
    return instances


def instance_joint_pairs(instance):
    source = instance_source_assembly(instance)
    originals = direct_instance_joints(source, active=True)
    copies = direct_instance_joints(instance)
    if len(originals) != len(copies):
        return (
            source,
            [],
            [
                {
                    "instance": instance.Name,
                    "kind": "joint_count_mismatch",
                    "source_count": len(originals),
                    "copy_count": len(copies),
                }
            ],
        )
    # The installed C++ synchronizer explicitly matches by source/group index.
    return source, list(zip(originals, copies)), []


def instance_parameter_differences(original, copied):
    props = list(INSTANCE_JOINT_PROPERTIES)
    props += [
        "Placement" + str(i) for i in (1, 2) if getattr(original, "Detach" + str(i))
    ]
    differences = []
    for key in props:
        one, two = getattr(original, key), getattr(copied, key)
        equal = one.isSame(two, 1e-10) if isinstance(one, App.Placement) else one == two
        if not equal:
            differences.append(
                {
                    "property": key,
                    "source_value": json_value(one),
                    "instance_value": json_value(two),
                }
            )
    return differences


def assembly_sync_status(assembly):
    records, issues = [], []
    for instance in flexible_instances(assembly):
        source, pairs, errors = instance_joint_pairs(instance)
        issues.extend(errors)
        for original, copied in pairs:
            differences = instance_parameter_differences(original, copied)
            records.append(
                {
                    "instance": instance.Name,
                    "source_assembly": source.Name,
                    "source_joint": original.Name,
                    "copy_joint": copied.Name,
                    "differences": differences,
                }
            )
    return {
        "assembly": assembly.Name,
        "current": not issues and not any(r["differences"] for r in records),
        "joints": records,
        "issues": issues,
        "motion_bindings": instance_motion_status(assembly),
        "source_matching": "native active-joint group order; tracked motions bind source identity",
        "scope": "flexible joint parameters and detached connectors; component/source topology is native-managed",
    }


def synchronize_assembly_instances(assembly):
    """Run native component synchronization, then fill missing joint fields."""
    before = assembly_sync_status(assembly)
    placements = {
        obj: obj.Placement.copy() for obj in assembly_moving_components(assembly)
    }
    corrected = []
    with assembly_explicit_solve():
        assembly.Document.recompute()
        for instance in flexible_instances(assembly):
            instance.touch()
            instance.recompute()
            source, pairs, errors = instance_joint_pairs(instance)
            if errors:
                raise ValueError(
                    "Native instance joint synchronization failed: " + str(errors)
                )
            for original, copied in pairs:
                for difference in instance_parameter_differences(original, copied):
                    key = difference["property"]
                    value = getattr(original, key)
                    setattr(
                        copied,
                        key,
                        value.copy() if isinstance(value, App.Placement) else value,
                    )
                    corrected.append(
                        {
                            "instance": instance.Name,
                            "source_joint": original.Name,
                            "copy_joint": copied.Name,
                            "property": key,
                        }
                    )
                copied.Proxy.updateJCSPlacements(copied)
                copied.recompute()
        # Angle/offset callbacks can pre-position components even when automatic
        # solve is disabled. Synchronizing metadata alone must preserve poses.
        for obj, placement in placements.items():
            if (
                obj.Document
                and obj.Document.getObject(obj.Name) is obj
                and not obj.Placement.isSame(placement, 1e-10)
            ):
                obj.Placement = placement
        motion_changes = synchronize_instance_motions(assembly)
        status = assembly_sync_status(assembly)
        if not status["current"]:
            raise ValueError(
                "Instance parameters remain stale after synchronization: " + str(status)
            )
    return status | {
        "corrected": corrected,
        "was_current": before["current"],
        "motion_binding_changes": motion_changes,
    }


def bind_instance_motion(assembly, motion, joint):
    """Persist semantic motion targets instead of native reusable copy slots."""
    properties = {
        "MCPSourceJoint": "App::PropertyXLink",
        "MCPJointInstance": "App::PropertyLink",
        "MCPBoundJoint": "App::PropertyLink",
    }
    for key, kind in properties.items():
        if key not in motion.PropertiesList:
            motion.addProperty(
                kind, key, "MCP", "Stable flexible-instance motion target"
            )
            motion.setEditorMode(key, 1)
    source_joint = owner = None
    for instance in flexible_instances(assembly):
        _, pairs, errors = instance_joint_pairs(instance)
        if joint in direct_instance_joints(instance):
            if errors:
                raise ValueError("Synchronize the instance before binding its motion")
            source_joint = next(
                original for original, copied in pairs if copied is joint
            )
            owner = instance
            break
    motion.MCPSourceJoint = source_joint
    motion.MCPJointInstance = owner
    motion.MCPBoundJoint = joint if owner else None
    if "MCPInstanceMotion" not in motion.PropertiesList:
        motion.addProperty("App::PropertyBool", "MCPInstanceMotion", "MCP")
        motion.setEditorMode("MCPInstanceMotion", 1)
    motion.MCPInstanceMotion = owner is not None


def instance_motion_status(assembly):
    """Resolve tracked targets without modifying document links."""
    instances = flexible_instances(assembly)
    copied_owners = {
        joint: instance
        for instance in instances
        for joint in direct_instance_joints(instance)
    }
    records = []
    for motion in assembly.Document.Objects:
        # Native Motion belongs to an Assembly directly, even inside Simulation.
        if motion not in assembly.Group or not hasattr(motion, "MotionType"):
            continue
        current = motion.Joint[0] if motion.Joint else None
        if not getattr(motion, "MCPInstanceMotion", False):
            if current in copied_owners:
                records.append(
                    {
                        "motion": motion.Name,
                        "instance": copied_owners[current].Name,
                        "source_joint": None,
                        "current_joint": current.Name,
                        "target_joint": None,
                        "status": "untracked_instance_target",
                    }
                )
            continue
        instance, source = motion.MCPJointInstance, motion.MCPSourceJoint
        target, status = None, "current"
        if current is not motion.MCPBoundJoint:
            status = "manual_reference_change"
        elif instance not in instances:
            status = "instance_unavailable"
        elif source is None:
            status = "source_deleted"
        elif source.Suppressed:
            status = "source_suppressed"
        else:
            _, pairs, errors = instance_joint_pairs(instance)
            target = next(
                (copied for original, copied in pairs if original is source), None
            )
            if errors or target is None:
                status = "source_unavailable"
            elif target is not current:
                status = "rebind_required"
        records.append(
            {
                "motion": motion.Name,
                "instance": instance.Name if instance else None,
                "source_joint": source.Name if source else None,
                "current_joint": current.Name if current else None,
                "target_joint": target.Name if target else None,
                "status": status,
            }
        )
    return records


def synchronize_instance_motions(assembly):
    records = instance_motion_status(assembly)
    for record in records:
        status = record["status"]
        if status in ("manual_reference_change", "untracked_instance_target"):
            # Preserve explicit UI edits. The selected Simulation rejects this
            # unresolved binding; unrelated static solves can still proceed.
            continue
        motion = assembly.Document.getObject(record["motion"])
        target = (
            assembly.Document.getObject(record["target_joint"])
            if record["target_joint"]
            else None
        )
        if status == "rebind_required" or (status != "current" and motion.Joint):
            motion.Joint = target
            motion.MCPBoundJoint = target
    return records
