"""Native rigid/flexible AssemblyLink lifecycle and reference migration."""

from typing import TYPE_CHECKING

import FreeCAD as App

if TYPE_CHECKING:
    from .assembly_state import (
        json_value,
        assembly_component_paths,
        assembly_joint_objects,
    )
    from .assembly_joints import assembly_explicit_solve, assembly_solve_result


def assembly_instance(name):
    doc = App.ActiveDocument
    instance = doc.getObject(name) if doc else None
    if instance is None or not instance.isDerivedFrom("Assembly::AssemblyLink"):
        raise ValueError("Native AssemblyLink required")
    current = instance
    seen = set()
    while not current.isDerivedFrom("Assembly::AssemblyObject"):
        if current.Name in seen:
            raise ValueError("Cyclic subassembly ownership")
        seen.add(current.Name)
        parents = [
            obj
            for obj in current.InList
            if current in getattr(obj, "Group", [])
            and (
                obj.isDerivedFrom("Assembly::AssemblyObject")
                or obj.isDerivedFrom("Assembly::AssemblyLink")
                or obj.TypeId == "App::DocumentObjectGroup"
            )
        ]
        if len(parents) != 1:
            raise ValueError(
                "AssemblyLink must have one containing native Assembly path"
            )
        current = parents[0]
    if instance.LinkedObject is None:
        raise ValueError("AssemblyLink source is missing")
    return instance, current


def instance_descendants(instance):
    result = {}

    def visit(container, prefix, ancestors):
        if container in ancestors:
            raise ValueError("Cyclic subassembly hierarchy")
        for obj in getattr(container, "Group", []):
            if not hasattr(obj, "Placement"):
                continue
            path = prefix + obj.Name + "."
            result[path] = obj
            if obj.isDerivedFrom("Assembly::AssemblyLink") or obj.TypeId == "App::Part":
                visit(obj, path, ancestors + [container])

    visit(instance, "", [])
    return result


def instance_data(instance, parent):
    children = instance_descendants(instance)

    def find_prefix(container, prefix):
        for obj in getattr(container, "Group", []):
            path = prefix + obj.Name + "."
            if obj is instance:
                return path
            if (
                obj.isDerivedFrom("Assembly::AssemblyLink")
                or obj.TypeId == "App::DocumentObjectGroup"
            ):
                found = find_prefix(obj, path)
                if found:
                    return found
        return None

    root_path = find_prefix(parent, "")
    return {
        "name": instance.Name,
        "parent_assembly": parent.Name,
        "instance_path": root_path,
        "rigid": instance.Rigid,
        "source": json_value(instance.LinkedObject),
        "placement": json_value(instance.Placement),
        "components": [
            {
                "path": root_path + path,
                "name": obj.Name,
                "placement": json_value(obj.Placement),
                "source": json_value(getattr(obj, "LinkedObject", None)),
            }
            for path, obj in children.items()
        ],
        "joint_copies": [
            {
                "name": j.Name,
                "type": getattr(j, "JointType", "Grounded"),
                "reference1": json_value(getattr(j, "Reference1", None)),
                "reference2": json_value(getattr(j, "Reference2", None)),
            }
            for j in assembly_joint_objects(instance)
        ],
        "parent_moving_paths": [
            path
            for path in assembly_component_paths(parent)
            if path.startswith(root_path)
        ],
        "mode_behavior": "rigid synchronizes source placements and removes joint copies; flexible keeps independent instance placements and copies source joints",
    }


def set_instance_rigid(name, rigid, solve):
    if type(rigid) is not bool:
        raise TypeError("rigid requires a boolean")
    instance, parent = assembly_instance(name)
    if instance.Rigid == rigid:
        solver = assembly_solve_result(parent, requested=solve, strict=True)
        return instance_data(instance, parent) | {
            "changed": False,
            "removed_grounding": [],
            "migrated_references": [],
            "removed_motions": [],
            "solver": solver,
        }
    descendants = instance_descendants(instance)
    reverse = {obj: path for path, obj in descendants.items()}
    # Capture external references before native callbacks synchronize/delete
    # copied joints. Endpoints rooted at a flexible child become instance paths.
    migrations = []
    instance_joints = set(assembly_joint_objects(instance))
    dependent_motions = (
        [
            obj
            for obj in parent.Document.Objects
            if (getattr(obj, "Joint", None) and obj.Joint[0] in instance_joints)
            or (
                getattr(obj, "MCPInstanceMotion", False)
                and (
                    obj.MCPJointInstance is instance or obj.MCPJointInstance in reverse
                )
            )
        ]
        if rigid
        else []
    )
    joints = assembly_joint_objects(parent)
    grounded = {
        j.Name: getattr(j, "ObjectToGround", None)
        for j in joints
        if hasattr(j, "ObjectToGround")
    }
    for joint in joints:
        if joint in instance_joints:
            continue
        for property_name in ("Reference1", "Reference2"):
            ref = getattr(joint, property_name, None)
            if not ref or not ref[0]:
                continue
            if rigid and ref[0] in reverse:
                migrations.append(
                    (
                        joint,
                        property_name,
                        instance,
                        [reverse[ref[0]] + sub for sub in ref[1]],
                    )
                )
            elif not rigid and ref[0] is instance:
                paths = list(ref[1])
                first = paths[0] if paths else ""
                candidate = next(
                    (
                        obj
                        for path, obj in descendants.items()
                        if first.startswith(path)
                        and not (
                            obj.isDerivedFrom("Assembly::AssemblyLink")
                            and not obj.Rigid
                        )
                    ),
                    None,
                )
                if candidate is None:
                    raise ValueError(
                        "Joint "
                        + joint.Name
                        + " needs a child instance subelement before switching to flexible"
                    )
                prefix = reverse[candidate]
                if any(sub and not sub.startswith(prefix) for sub in paths):
                    raise ValueError(
                        "Joint connector selectors refer to different child components"
                    )
                migrations.append(
                    (
                        joint,
                        property_name,
                        candidate,
                        [sub[len(prefix) :] if sub else "" for sub in paths],
                    )
                )
    with assembly_explicit_solve():
        removed_motions = [obj.Name for obj in dependent_motions]
        for obj in dependent_motions:
            parent.Document.removeObject(obj.Name)
        instance.Rigid = rigid
        # Nested native callbacks can fail to find a direct Assembly parent.
        # Enforce the same incompatible-ground removal at the resolved root.
        for joint_name, component in grounded.items():
            if (rigid and component in reverse) or (
                not rigid and component is instance
            ):
                if parent.Document.getObject(joint_name):
                    parent.Document.removeObject(joint_name)
        for joint, prop, target, subs in migrations:
            setattr(joint, prop, (target, subs))
            joint.Proxy.updateJCSPlacements(joint)
        parent.Document.recompute()
        if not instance.isValid():
            raise ValueError(
                "Native subassembly update failed: " + instance.getStatusString()
            )
        result = instance_data(instance, parent)
        result.update(
            changed=True,
            removed_motions=removed_motions,
            removed_grounding=[
                name for name in grounded if parent.Document.getObject(name) is None
            ],
            migrated_references=[
                {
                    "joint": j.Name,
                    "property": prop,
                    "target": target.Name,
                    "subelements": subs,
                }
                for j, prop, target, subs in migrations
            ],
            solver=assembly_solve_result(parent, requested=solve, strict=True),
        )
    return result


def remove_assembly_component(assembly_name, component_name):
    """Delete instance-owned objects and joints, never linked source objects."""
    parent = App.ActiveDocument.getObject(assembly_name) if App.ActiveDocument else None
    component = parent.Document.getObject(component_name) if parent else None
    if parent is None or not parent.isDerivedFrom("Assembly::AssemblyObject"):
        raise ValueError("Native Assembly required")
    if (
        component is None
        or component not in parent.Group
        or not (
            component.isDerivedFrom("App::Link")
            or component.isDerivedFrom("Assembly::AssemblyLink")
        )
    ):
        raise ValueError("Component must be a direct App::Link or AssemblyLink member")
    owned = []
    seen = set()
    native_owned_names = set()

    def collect(obj):
        if obj.Name in seen:
            return
        seen.add(obj.Name)
        origin = getattr(obj, "Origin", None)
        if obj.isDerivedFrom("Assembly::AssemblyLink") and origin:
            native_owned_names.add(origin.Name)
            native_owned_names.update(feature.Name for feature in origin.OriginFeatures)
        # App::Link.Group can expose source children. Only descend real
        # instance-owned AssemblyLink/group objects, not ordinary linked parts.
        if obj.isDerivedFrom("Assembly::AssemblyLink") or obj.isDerivedFrom(
            "Assembly::JointGroup"
        ):
            for child in getattr(obj, "Group", []):
                collect(child)
        owned.append(obj)

    collect(component)
    owned_names = {obj.Name for obj in owned}
    joints = []
    for joint in assembly_joint_objects(parent):
        refs = [getattr(joint, "Reference1", None), getattr(joint, "Reference2", None)]
        if (
            joint.Name in owned_names
            or getattr(getattr(joint, "ObjectToGround", None), "Name", None)
            in owned_names
            or any(
                ref
                and ref[0]
                and (
                    ref[0].Name in owned_names
                    or ref[0] is parent
                    and any(sub.startswith(component_name + ".") for sub in ref[1])
                )
                for ref in refs
            )
        ):
            joints.append(joint)
    joint_names = {joint.Name for joint in joints}
    motions = [
        obj
        for obj in parent.Document.Objects
        if (
            getattr(obj, "Joint", None)
            and getattr(obj.Joint[0], "Name", None) in joint_names
        )
        or (
            getattr(obj, "MCPInstanceMotion", False)
            and getattr(obj.MCPJointInstance, "Name", None) in owned_names
        )
    ]
    # Keep multi-component exploded steps usable; remove only references to
    # this instance, deleting a step only when it would become empty.
    step_edits = []
    removed_steps = []
    for obj in parent.Document.Objects:
        ref = getattr(obj, "References", None)
        if not hasattr(obj, "MovementTransform") or not ref or ref[0] is not parent:
            continue
        paths = [path for path in ref[1] if not path.startswith(component_name + ".")]
        if len(paths) != len(ref[1]):
            if paths:
                step_edits.append((obj, paths))
            else:
                removed_steps.append(obj)
    deletion = owned_names | joint_names | {obj.Name for obj in motions + removed_steps}
    allowed_parents = {parent.Name} | deletion
    # Native containment groups own deleted joints/motions but are retained.
    for obj in joints + motions + removed_steps:
        allowed_parents.update(
            container.Name
            for container in obj.InList
            if obj in getattr(container, "Group", [])
        )
    unexpected = []
    for obj in owned:
        for user in obj.InList:
            if user.Name not in allowed_parents:
                unexpected.append(user.Name + " -> " + obj.Name)
    if unexpected:
        raise ValueError(
            "Instance has additional dependent objects: " + ", ".join(unexpected)
        )
    removed_motions = [obj.Name for obj in motions]
    removed_joints = [obj.Name for obj in joints]
    removed_step_names = [obj.Name for obj in removed_steps]
    retained_names = (
        {obj.Name for obj in parent.Document.Objects} - deletion - native_owned_names
    )
    with assembly_explicit_solve():
        for step, paths in step_edits:
            step.References = [parent, paths]
        for name in (
            removed_motions
            + removed_step_names
            + removed_joints
            + [obj.Name for obj in owned]
        ):
            if parent.Document.getObject(name):
                parent.Document.removeObject(name)
        parent.Document.recompute()
    missing = retained_names - {obj.Name for obj in parent.Document.Objects}
    if missing:
        raise ValueError(
            "Native deletion removed unrelated objects: " + ", ".join(missing)
        )
    return {
        "assembly": parent.Name,
        "removed_component": component_name,
        "removed_instance_objects": sorted(owned_names),
        "removed_joints": removed_joints,
        "removed_motions": removed_motions,
        "removed_exploded_steps": removed_step_names,
        "updated_exploded_steps": [step.Name for step, _ in step_edits],
    }
