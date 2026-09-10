"""Assembly inspection helpers executed in FreeCAD's Python interpreter."""

import FreeCAD as App
import UtilsAssembly


def json_value(value):
    """Preserve native units, placements and links across the JSON boundary."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, App.Units.Quantity):
        return {"value": value.Value, "unit": str(value.Unit)}
    if isinstance(value, App.Placement):
        return {"base": list(value.Base), "rotation": list(value.Rotation.Q)}
    if isinstance(value, App.Vector):
        return list(value)
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if hasattr(value, "Document") and hasattr(value, "Name"):
        return {"document": value.Document.Name, "object": value.Name}
    return str(value)


def joint_records(assembly):
    records = []
    for joint in assembly_joint_objects(assembly):
        grounded = hasattr(joint, "ObjectToGround")
        refs = []
        if grounded:
            component = joint.ObjectToGround
            valid = component is not None and component in assembly_moving_components(
                assembly
            )
            refs.append(
                {
                    "property": "ObjectToGround",
                    "valid": valid,
                    "value": json_value(component),
                }
            )
        else:
            for index in (1, 2):
                ref = getattr(joint, "Reference" + str(index), None)
                try:
                    valid = bool(UtilsAssembly.isRefValid(ref, 1))
                    if not ref or not ref[0]:
                        valid = False
                    elif ref[0] not in assembly_moving_components(assembly):
                        valid = False
                    else:
                        for sub in ref[1]:
                            if sub:
                                resolved = ref[0].getSubObject(sub)
                                if resolved is None or (
                                    hasattr(resolved, "isNull") and resolved.isNull()
                                ):
                                    valid = False
                except (ValueError, TypeError, RuntimeError):
                    valid = False
                refs.append({"index": index, "valid": valid, "value": json_value(ref)})
        records.append(
            {
                "name": joint.Name,
                "type": "Grounded" if grounded else getattr(joint, "JointType", ""),
                "suppressed": bool(getattr(joint, "Suppressed", False)),
                "references": refs,
            }
        )
    return records


def assembly_component_paths(assembly):
    """Enumerate moving instances, descending through flexible containers only."""
    result = {}

    def visit(container, prefix, ancestors):
        if container in ancestors:
            raise ValueError("Cyclic assembly component hierarchy")
        children = (
            container.ElementList
            if UtilsAssembly.isLinkGroup(container)
            else getattr(container, "Group", [])
        )
        for obj in children:
            path = prefix + obj.Name + "."
            if obj.isDerivedFrom("Assembly::AssemblyLink") and not obj.Rigid:
                visit(obj, path, ancestors + [container])
            elif (
                UtilsAssembly.isLinkGroup(obj)
                or obj.TypeId == "App::DocumentObjectGroup"
            ):
                visit(obj, path, ancestors + [container])
            elif obj.isDerivedFrom("App::Part") or obj.isDerivedFrom("Part::Feature"):
                result[path] = obj
            elif UtilsAssembly.isLink(obj):
                source = obj.getLinkedObject()
                if source and (
                    source.isDerivedFrom("App::Part")
                    or source.isDerivedFrom("Part::Feature")
                ):
                    result[path] = obj

    visit(assembly, "", [])
    return result


def assembly_moving_components(assembly):
    return list(assembly_component_paths(assembly).values())


def assembly_joint_objects(assembly):
    """Include native instance joint copies without .Joints filtering/deletion."""
    result = []
    seen = set()

    def visit(container, ancestors):
        if container in ancestors:
            raise ValueError("Cyclic assembly joint hierarchy")
        for obj in getattr(container, "Group", []):
            if obj.isDerivedFrom("Assembly::JointGroup"):
                for joint in obj.Group:
                    if joint.Name not in seen:
                        seen.add(joint.Name)
                        result.append(joint)
            elif obj.isDerivedFrom("Assembly::AssemblyLink"):
                if not obj.Rigid:
                    visit(obj, ancestors + [container])
            elif obj.TypeId == "App::DocumentObjectGroup":
                visit(obj, ancestors + [container])

    visit(assembly, [])
    return result
