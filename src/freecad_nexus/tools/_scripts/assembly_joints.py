"""Validated native Assembly joint creation, editing and solve orchestration."""

from contextlib import contextmanager
import math
from typing import TYPE_CHECKING

import FreeCAD as App
import JointObject
import UtilsAssembly

if TYPE_CHECKING:
    from .assembly_sync import synchronize_assembly_instances
    from .assembly_state import (
        json_value,
        assembly_moving_components,
        assembly_joint_objects,
    )


def native_assembly(name):
    doc = App.ActiveDocument
    obj = doc.getObject(name) if doc else None
    if not obj or not obj.isDerivedFrom("Assembly::AssemblyObject"):
        raise ValueError("Native Assembly object not found: " + name)
    return obj


def native_joint(name):
    doc = App.ActiveDocument
    obj = doc.getObject(name) if doc else None
    if not obj or not isinstance(getattr(obj, "Proxy", None), JointObject.Joint):
        raise ValueError("Native Assembly joint not found: " + name)
    return obj


def remove_native_joint(name):
    """Remove direct motions; retain semantic instance targets for repair."""
    doc = App.ActiveDocument
    joint = doc.getObject(name) if doc else None
    if not joint or not isinstance(
        getattr(joint, "Proxy", None), (JointObject.Joint, JointObject.GroundedJoint)
    ):
        raise ValueError("Native Assembly joint not found: " + name)
    # Motions on an instance store a source identity, which can live in another
    # open document. Report these without editing or deleting other documents.
    retained = []
    for document in App.listDocuments().values():
        for obj in document.Objects:
            if getattr(obj, "MCPInstanceMotion", False) and (
                getattr(obj, "MCPSourceJoint", None) is joint
                or getattr(obj, "MCPBoundJoint", None) is joint
            ):
                retained.append({"document": document.Name, "motion": obj.Name})
    motions = [
        obj
        for obj in doc.Objects
        if hasattr(obj, "MotionType")
        and getattr(obj, "Joint", None)
        and obj.Joint[0] is joint
        and not getattr(obj, "MCPInstanceMotion", False)
    ]
    for motion in motions:
        users = [
            obj.Name
            for obj in motion.InList
            if not obj.isDerivedFrom("Assembly::AssemblyObject")
            and not (
                type(getattr(obj, "Proxy", None)).__module__
                == "CommandCreateSimulation"
                and type(obj.Proxy).__name__ == "Simulation"
                and motion in obj.Group
            )
        ]
        if users:
            raise ValueError("Motion has other dependent objects: " + str(users))
    removed = [obj.Name for obj in motions]
    with assembly_explicit_solve():
        for motion_name in removed:
            doc.removeObject(motion_name)
        doc.removeObject(name)
        doc.recompute()
    return {
        "removed": name,
        "removed_motions": removed,
        "retained_instance_motions": retained,
    }


@contextmanager
def assembly_explicit_solve():
    """Prevent native property callbacks/recompute from hiding solver failures.

    Scope is synchronous on FreeCAD's main thread. Restore preference values
    and remove keys that were originally absent, even after a native exception.
    """
    prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Assembly")
    present = {entry[1] for entry in (prefs.GetContents() or [])}
    names = ("SolveInJointCreation", "SolveOnRecompute")
    saved = {name: prefs.GetBool(name, True) for name in names}
    try:
        for name in names:
            prefs.SetBool(name, False)
        yield
    finally:
        for name in names:
            if name in present:
                prefs.SetBool(name, saved[name])
            else:
                prefs.RemBool(name)


def assembly_reference(assembly, object_name, element="", vertex=""):
    obj = assembly.Document.getObject(object_name)
    if not obj:
        raise ValueError("Reference object not found: " + object_name)
    if obj is assembly:
        obj, element = UtilsAssembly.getComponentReference(assembly, obj, element)
        if vertex:
            vertex_obj, vertex = UtilsAssembly.getComponentReference(
                assembly, assembly, vertex
            )
            if vertex_obj is not obj:
                raise ValueError(
                    "Connector element and vertex must belong to the same component"
                )
    members = assembly_moving_components(assembly)
    if obj not in members:
        raise ValueError("Reference must be a component of the selected Assembly")
    for sub, is_vertex in ((element, False), (vertex, True)):
        if not sub:
            continue
        resolved = obj.getSubObject(sub)
        if resolved is None or (hasattr(resolved, "isNull") and resolved.isNull()):
            raise ValueError("Reference subelement not found: " + obj.Name + "." + sub)
        # Native references also accept edge/circle centres as point selectors.
        if is_vertex and getattr(resolved, "ShapeType", None) not in (
            "Vertex",
            "Edge",
            "Face",
        ):
            raise ValueError("Invalid connector point selector")
    return [obj, [element, vertex]]


def assembly_reference_issue(assembly, ref):
    try:
        if not ref or len(ref) != 2 or not ref[0] or not ref[1]:
            raise ValueError("Missing connector reference")
        assembly_reference(
            assembly, ref[0].Name, ref[1][0], ref[1][1] if len(ref[1]) > 1 else ""
        )
    except Exception as exc:
        return str(exc)
    return None


def joint_parameters(joint_type, distance=None, distance2=None, angle=None):
    values = {"Distance": distance, "Distance2": distance2, "Angle": angle}
    families = {
        "Distance": JointObject.JointUsingDistance,
        "Distance2": JointObject.JointUsingDistance2,
        "Angle": JointObject.JointUsingAngle,
    }
    for key, value in values.items():
        if value is None:
            continue
        if joint_type not in families[key]:
            raise ValueError(key + " is not used by " + joint_type)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(key + " must be a finite number")
        if key != "Angle" and joint_type in ("Gears", "Belt") and value <= 0:
            raise ValueError("Gear and belt radii must be positive")
        if key == "Distance" and joint_type in ("Screw", "RackPinion") and value == 0:
            raise ValueError("Pitch/radius must be nonzero")
    return {key: value for key, value in values.items() if value is not None}


def assembly_preflight(assembly):
    """Inspect group contents without native .Joints, which can delete bad joints."""
    joints = assembly_joint_objects(assembly)
    issues, active, grounds, graph = [], [], set(), {}
    for joint in joints:
        if hasattr(joint, "ObjectToGround"):
            component = joint.ObjectToGround
            if component in assembly_moving_components(assembly):
                grounds.add(component.Name)
            else:
                issues.append(
                    {
                        "joint": joint.Name,
                        "kind": "invalid_ground",
                        "reason": "Grounded component is missing or outside Assembly",
                    }
                )
            continue
        if not hasattr(joint, "JointType") or joint.Suppressed:
            continue
        errors = [
            assembly_reference_issue(assembly, getattr(joint, "Reference" + str(i)))
            for i in (1, 2)
        ]
        if any(errors):
            issues.append(
                {
                    "joint": joint.Name,
                    "kind": "invalid_reference",
                    "reason": str(errors),
                }
            )
            continue
        first, second = joint.Reference1[0], joint.Reference2[0]
        if first is second:
            issues.append(
                {
                    "joint": joint.Name,
                    "kind": "self_reference",
                    "reason": "Both connectors refer to the same moving component",
                }
            )
            continue
        try:
            joint_parameters(
                joint.JointType,
                joint.Distance.Value
                if joint.JointType in JointObject.JointUsingDistance
                else None,
                joint.Distance2.Value
                if joint.JointType in JointObject.JointUsingDistance2
                else None,
                joint.Angle.Value
                if joint.JointType in JointObject.JointUsingAngle
                else None,
            )
        except ValueError as exc:
            issues.append(
                {"joint": joint.Name, "kind": "invalid_parameters", "reason": str(exc)}
            )
        active.append(joint)
        if joint.JointType not in ("Gears", "Belt", "Screw", "RackPinion"):
            graph.setdefault(first.Name, set()).add(second.Name)
            graph.setdefault(second.Name, set()).add(first.Name)
    connected, queue = set(grounds), list(grounds)
    while queue:
        for name in graph.get(queue.pop(), ()):
            if name not in connected:
                connected.add(name)
                queue.append(name)
    for joint in active:
        if grounds and any(
            ref[0].Name not in connected for ref in (joint.Reference1, joint.Reference2)
        ):
            issues.append(
                {
                    "joint": joint.Name,
                    "kind": "unconnected",
                    "reason": "Native solver skips joints not connected to ground through structural joints",
                }
            )
        if joint.JointType in ("Screw", "RackPinion"):
            matched = False
            for slider in active:
                if slider.JointType != "Slider":
                    continue
                for i in (1, 2):
                    for j in (1, 2):
                        if (
                            getattr(joint, "Reference" + str(i))[0]
                            is getattr(slider, "Reference" + str(j))[0]
                        ):
                            one = getattr(
                                joint, "Placement" + str(i)
                            ).Rotation.getYawPitchRoll()
                            two = getattr(
                                slider, "Placement" + str(j)
                            ).Rotation.getYawPitchRoll()
                            if (
                                abs(one[1] - two[1]) < 1e-7
                                and abs(one[2] - two[2]) < 1e-7
                            ):
                                matched = True
            if not matched:
                issues.append(
                    {
                        "joint": joint.Name,
                        "kind": "missing_slider",
                        "reason": "Native coupling requires an active Slider with matching connector pitch/roll",
                    }
                )
    return issues, active, grounds


def assembly_solve_result(assembly, store_previous=False, requested=True, strict=False):
    synchronization = synchronize_assembly_instances(assembly) if requested else None
    issues, active, grounds = assembly_preflight(assembly)
    result = {
        "assembly": assembly.Name,
        "solver_code": None,
        "solved": False,
        "solver_requested": requested,
        "issues": issues,
        "instance_synchronization": synchronization,
        "joints": [
            {"name": j.Name, "type": j.JointType, "suppressed": False} for j in active
        ],
    }
    if not requested:
        result["status"] = "recompute_pending"
        return result
    if issues:
        result["status"] = "invalid_input"
        if strict:
            raise ValueError("Assembly cannot be solved: " + str(issues))
        return result
    if not grounds:
        result.update(status="no_grounded_component", solver_code=-6)
        return result
    with assembly_explicit_solve():
        assembly.Document.recompute()
        with assembly_solver_joint_copies(assembly) as promoted:
            code = assembly.solve(store_previous)
            # Native runPreDrag can return zero without escaping the parallel
            # singularity of Angle/Perpendicular. Seed only unsatisfied joints
            # using the same native pre-positioner as the GUI. The first solve
            # saved the user's original pose; the retry must not overwrite it.
            seeded = []
            for joint in active:
                if joint.JointType not in ("Angle", "Perpendicular"):
                    continue
                if assembly_geometry_status([joint])[0]["satisfied"]:
                    continue
                candidate = promoted.get(joint.Name, joint)
                if candidate.Proxy.areJcsZParallel(candidate):
                    candidate.Proxy.preventParallel(candidate)
                    if not candidate.Proxy.areJcsZParallel(candidate):
                        seeded.append(joint.Name)
            if seeded:
                result["initial_solver_code"] = code
                code = assembly.solve(False)
            result["singularity_prepositioned_joints"] = seeded
        result["promoted_nested_joints"] = list(promoted)
        result.update(
            solver_code=code,
            solved=code == 0,
            native_solve_succeeded=code == 0,
            status="solved" if code == 0 else "solver_failed",
        )
        if code != 0 and strict:
            raise RuntimeError("Native Assembly solver failed with code " + str(code))
        geometry = assembly_geometry_status(active)
        result["geometry_checks"] = geometry
        result["geometry_violations"] = [
            item for item in geometry if not item["satisfied"]
        ]
        if result["geometry_violations"]:
            result.update(solved=False, status="joint_geometry_violation")
            if strict:
                raise ValueError(
                    "Native solve left unsatisfied joint geometry: "
                    + str(result["geometry_violations"])
                )
        limits = assembly_limit_status(active)
        result["limit_checks"] = limits
        result["limit_violations"] = [
            item for item in limits if not item["within_limits"]
        ]
        if result["limit_violations"]:
            result.update(solved=False, status="joint_limit_violation")
            if strict:
                raise ValueError(
                    "Native solve left violated joint limits: "
                    + str(result["limit_violations"])
                )
        # Do not recompute again: it would discard stored Undo and simulation frames.
    return result


@contextmanager
def assembly_solver_joint_copies(assembly, simulation=None):
    """Expose deeper native joints at root for the installed nonrecursive solver.

    Native getSubAssemblies only finds directly contained AssemblyLinks. Copy
    deeper active instance joints temporarily into the root group, retaining
    native proxies/references. Restore Motion references and remove copies
    after solving, without a recompute that would discard solver frame/Undo.
    """
    deep = []

    def visit(container, depth):
        for obj in getattr(container, "Group", []):
            if obj.isDerivedFrom("Assembly::JointGroup") and depth >= 2:
                deep.extend(
                    j for j in obj.Group if hasattr(j, "JointType") and not j.Suppressed
                )
            elif obj.isDerivedFrom("Assembly::AssemblyLink") and not obj.Rigid:
                visit(obj, depth + 1)
            elif obj.TypeId == "App::DocumentObjectGroup":
                visit(obj, depth)

    visit(assembly, 0)
    copies = {}
    motion_refs = []
    try:
        group = UtilsAssembly.getJointGroup(assembly)
        for joint in deep:
            copied = assembly.Document.copyObject(joint)
            group.addObject(copied)
            copies[joint.Name] = copied
        if simulation:
            for motion in simulation.Group:
                ref = motion.Joint
                if ref and ref[0] and ref[0].Name in copies:
                    motion_refs.append((motion, ref))
                    motion.Joint = (copies[ref[0].Name], list(ref[1]))
        if copies:
            assembly.Document.recompute()
        yield copies
    finally:
        for motion, ref in motion_refs:
            motion.Joint = ref
        for copied in copies.values():
            if assembly.Document.getObject(copied.Name):
                assembly.Document.removeObject(copied.Name)


def assembly_geometry_status(joints):
    """Check observable structural invariants independently of solver code."""
    results = []
    for joint in joints:
        kind = joint.JointType
        if kind not in (
            "Fixed",
            "Revolute",
            "Cylindrical",
            "Slider",
            "Ball",
            "Parallel",
            "Perpendicular",
            "Angle",
        ):
            continue
        one = UtilsAssembly.getJcsGlobalPlc(joint.Placement1, joint.Reference1)
        two = UtilsAssembly.getJcsGlobalPlc(joint.Placement2, joint.Reference2)
        relative = one.inverse() * two
        axis = relative.Rotation.multVec(App.Vector(0, 0, 1))
        checks = {}
        if kind in ("Fixed", "Revolute", "Ball"):
            checks["connector_distance_mm"] = relative.Base.Length
        if kind in ("Cylindrical", "Slider"):
            checks["transverse_distance_mm"] = math.hypot(
                relative.Base.x, relative.Base.y
            )
        if kind in ("Fixed", "Slider"):
            checks["rotation_radians"] = abs(relative.Rotation.Angle)
        if kind in ("Revolute", "Cylindrical", "Parallel"):
            checks["axis_alignment_error"] = abs(abs(axis.z) - 1)
        if kind == "Perpendicular":
            checks["axis_dot_error"] = abs(axis.z)
        if kind == "Angle":
            checks["angle_cosine_error"] = abs(
                axis.z - math.cos(math.radians(joint.Angle.Value))
            )
        results.append(
            {
                "joint": joint.Name,
                "type": kind,
                "residuals": checks,
                "tolerance": 1e-6,
                "satisfied": all(value <= 1e-6 for value in checks.values()),
            }
        )
    return results


def assembly_limit_status(joints):
    """Measure native connector coordinates; runPreDrag can leave limits violated."""
    checks = []
    for joint in joints:
        first = UtilsAssembly.getJcsGlobalPlc(joint.Placement1, joint.Reference1)
        second = UtilsAssembly.getJcsGlobalPlc(joint.Placement2, joint.Reference2)
        relative = first.inverse() * second
        x_axis = relative.Rotation.multVec(App.Vector(1, 0, 0))
        coordinates = {
            "Length": relative.Base.z,
            "Angle": math.degrees(math.atan2(x_axis.y, x_axis.x)),
        }
        for coordinate, families in (
            ("Length", JointObject.JointUsingLimitLength),
            ("Angle", JointObject.JointUsingLimitAngle),
        ):
            if joint.JointType not in families:
                continue
            for side in ("Min", "Max"):
                if not getattr(joint, "Enable" + coordinate + side):
                    continue
                actual = coordinates[coordinate]
                limit = getattr(joint, coordinate + side).Value
                within = (
                    actual >= limit - 1e-7 if side == "Min" else actual <= limit + 1e-7
                )
                checks.append(
                    {
                        "joint": joint.Name,
                        "property": coordinate + side,
                        "actual": actual,
                        "limit": limit,
                        "within_limits": within,
                        "unit": "mm" if coordinate == "Length" else "deg",
                        "measurement": "connector Z displacement"
                        if coordinate == "Length"
                        else "connector XY angle in [-180,180]",
                    }
                )
    return checks


def assembly_create_joint(
    assembly_name,
    joint_type,
    first,
    second,
    first_element,
    second_element,
    first_vertex,
    second_vertex,
    value,
    distance2,
    reverse,
    first_offset,
    second_offset,
    solve,
):
    assembly = native_assembly(assembly_name)
    if joint_type not in JointObject.JointTypes:
        raise ValueError("Unsupported native joint type: " + joint_type)
    refs = [
        assembly_reference(assembly, first, first_element, first_vertex),
        assembly_reference(assembly, second, second_element, second_vertex),
    ]
    if refs[0][0] is refs[1][0]:
        raise ValueError("Joint requires two different moving components")
    params = joint_parameters(
        joint_type,
        value if joint_type != "Angle" else None,
        distance2,
        value if joint_type == "Angle" else None,
    )
    if any(not math.isfinite(x) for x in (first_offset, second_offset)):
        raise ValueError("Offsets must be finite millimeters")
    if reverse and joint_type not in JointObject.JointUsingReverse:
        raise ValueError("Native reverse is not supported for " + joint_type)
    existing_issues, _, _ = assembly_preflight(assembly)
    invalid = [
        i
        for i in existing_issues
        if i["kind"] in ("invalid_reference", "invalid_ground", "self_reference")
    ]
    if invalid:
        raise ValueError(
            "Repair existing references before creating a joint: " + str(invalid)
        )
    with assembly_explicit_solve():
        joint = UtilsAssembly.getJointGroup(assembly).newObject(
            "App::FeaturePython", "Joint"
        )
        JointObject.Joint(joint, JointObject.JointTypes.index(joint_type))
        for key, param in params.items():
            setattr(joint, key, param)
        joint.Offset1 = App.Placement(App.Vector(0, 0, first_offset), App.Rotation())
        joint.Offset2 = App.Placement(App.Vector(0, 0, second_offset), App.Rotation())
        joint.Proxy.setJointConnectors(joint, refs)
        if App.GuiUp:
            JointObject.ViewProviderJoint(joint.ViewObject)
        if reverse:
            joint.Proxy.flipOnePart(joint)
        assembly.Document.recompute()
        solver = assembly_solve_result(assembly, requested=solve, strict=True)
    return {
        "name": joint.Name,
        "type": joint.JointType,
        "object1": refs[0][0].Name,
        "object2": refs[1][0].Name,
        "reference1": list(joint.Reference1[1]),
        "reference2": list(joint.Reference2[1]),
        "distance": joint.Distance.Value,
        "distance2": joint.Distance2.Value,
        "angle": joint.Angle.Value,
        "length_unit": "mm",
        "angle_unit": "deg",
        "reversed": reverse,
        "solver": solver,
    }


def assembly_joint_limits(joint_name, values, solve):
    joint = native_joint(joint_name)
    for field in (
        "EnableLengthMin",
        "EnableLengthMax",
        "EnableAngleMin",
        "EnableAngleMax",
    ):
        if type(values[field]) is not bool:
            raise ValueError(field + " requires a boolean")
    for coordinate, supported in (
        ("Length", JointObject.JointUsingLimitLength),
        ("Angle", JointObject.JointUsingLimitAngle),
    ):
        enabled = [values["Enable" + coordinate + side] for side in ("Min", "Max")]
        limits = [values[coordinate + side] for side in ("Min", "Max")]
        if any(not math.isfinite(value) for value in limits):
            raise ValueError("Limits must be finite")
        if any(enabled) and joint.JointType not in supported:
            raise ValueError(
                coordinate + " limits are not supported by " + joint.JointType
            )
        if all(enabled) and limits[0] > limits[1]:
            raise ValueError(coordinate + " minimum cannot exceed maximum")
    with assembly_explicit_solve():
        for field, value in values.items():
            joint.setExpression(field, None)
            setattr(joint, field, value)
        assembly = joint.Proxy.getAssembly(joint)
        assembly.Document.recompute()
        result = assembly_solve_result(assembly, requested=solve, strict=True)
    return {
        "name": joint.Name,
        "length_min": joint.LengthMin.Value,
        "length_max": joint.LengthMax.Value,
        "angle_min": joint.AngleMin.Value,
        "angle_max": joint.AngleMax.Value,
        "length_unit": "mm",
        "angle_unit": "deg",
        "enabled": {
            field: getattr(joint, field)
            for field in values
            if field.startswith("Enable")
        },
        "solver": result,
        "applies_to": "native solver limits; measured after static solve; simulation omits limits",
    }


def assembly_edit_joint(joint_name, suppressed, distance, distance2, angle, solve):
    joint = native_joint(joint_name)
    if suppressed is not None and type(suppressed) is not bool:
        raise ValueError("suppressed requires a boolean")
    params = joint_parameters(joint.JointType, distance, distance2, angle)
    with assembly_explicit_solve():
        if suppressed is not None:
            joint.Suppressed = suppressed
        for key, value in params.items():
            joint.setExpression(key, None)
            setattr(joint, key, value)
        assembly = joint.Proxy.getAssembly(joint)
        assembly.Document.recompute()
        solver = assembly_solve_result(assembly, requested=solve, strict=True)
    return {
        "name": joint.Name,
        "type": joint.JointType,
        "suppressed": joint.Suppressed,
        "distance": joint.Distance.Value,
        "distance2": joint.Distance2.Value,
        "angle": joint.Angle.Value,
        "length_unit": "mm",
        "angle_unit": "deg",
        "solver": solver,
    }


def assembly_reconnect_joint(
    joint_name,
    first,
    second,
    first_element,
    second_element,
    first_vertex,
    second_vertex,
    solve,
):
    joint = native_joint(joint_name)
    assembly = joint.Proxy.getAssembly(joint)
    refs = [
        assembly_reference(assembly, first, first_element, first_vertex),
        assembly_reference(assembly, second, second_element, second_vertex),
    ]
    if refs[0][0] is refs[1][0]:
        raise ValueError("Joint requires two different moving components")
    with assembly_explicit_solve():
        # Set references directly: the native selection helper invokes
        # pre-solve before the second reference is repaired and may delete a
        # temporarily self-referencing joint. Execute the same placement
        # update only once the pair is valid.
        joint.Detach1 = False
        joint.Detach2 = False
        joint.Reference1 = refs[0]
        joint.Reference2 = refs[1]
        joint.Proxy.updateJCSPlacements(joint)
        assembly.Document.recompute()
        solver = assembly_solve_result(assembly, requested=solve, strict=True)
    return {
        "name": joint.Name,
        "reference1": json_value(joint.Reference1),
        "reference2": json_value(joint.Reference2),
        "solver": solver,
    }


def assembly_joint_offset(joint_name, connector, xyz, axis, angle, solve):
    joint = native_joint(joint_name)
    if connector not in (1, 2):
        raise ValueError("connector must be 1 or 2")
    if any(not math.isfinite(v) for v in xyz + axis + [angle]):
        raise ValueError("Offset coordinates and angle must be finite")
    if App.Vector(*axis).Length < 1e-14:
        raise ValueError("Rotation axis must be nonzero")
    with assembly_explicit_solve():
        # Offset onChanged triggers native preSolve (and can move components)
        # but automatic native solves are disabled until all edits are done.
        setattr(joint, "Detach" + str(connector), False)
        setattr(
            joint,
            "Offset" + str(connector),
            App.Placement(App.Vector(*xyz), App.Rotation(App.Vector(*axis), angle)),
        )
        joint.Proxy.updateJCSPlacements(joint)
        assembly = joint.Proxy.getAssembly(joint)
        assembly.Document.recompute()
        solver = assembly_solve_result(assembly, requested=solve, strict=True)
    return {
        "name": joint.Name,
        "connector": connector,
        "offset": json_value(getattr(joint, "Offset" + str(connector))),
        "placement": json_value(getattr(joint, "Placement" + str(connector))),
        "solver": solver,
    }
