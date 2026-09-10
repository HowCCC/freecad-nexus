"""Model-source replacement retaining Job clone identity and explicit references."""

import re
from typing import TYPE_CHECKING

import FreeCAD as App
import Path.Main.Job as PathJob
from PathScripts import PathUtils

if TYPE_CHECKING:
    from .cam_operations import operation_job, operation_path_status, operation_model
    from .cam_jobs import cam_json_value, job_path_objects
    from .cam_stock import job_models, stock_kind
    from .cam_setup import setup_data, regenerate_setup, refit_setup_stock


def setup_model(job, name):
    model = job.Document.getObject(name)
    if (
        model is None
        or model not in job.Model.Group
        or not PathJob.isResourceClone(job, model, "Model")
    ):
        raise ValueError("Select a native model clone inside this Job")
    if len(model.Objects) != 1:
        raise ValueError("Model replacement requires a single-source native clone")
    return model


def model_references(job, model):
    """Find real document properties; preserve Link references to the same clone."""
    refs = []
    path_objects = set(job_path_objects(job))
    owned = path_objects | {job, job.Model, job.Stock, job.SetupSheet}
    blocked = []
    for dependent in model.InList:
        if dependent not in owned:
            blocked.append(
                {"object": dependent.Name, "reason": "dependent outside this Job"}
            )
        for prop in dependent.PropertiesList:
            kind = dependent.getTypeIdOfProperty(prop)
            if "Link" not in kind:
                continue
            value = getattr(dependent, prop)
            entries = []
            normalized = kind.removesuffix("Global")
            if normalized in ("App::PropertyLinkSub", "App::PropertyXLinkSub"):
                entries = [value] if value else []
            elif normalized in (
                "App::PropertyLinkSubList",
                "App::PropertyXLinkSubList",
            ):
                entries = value
            elif normalized in ("App::PropertyLink", "App::PropertyXLink"):
                entries = [(value, [])]
            elif normalized in ("App::PropertyLinkList", "App::PropertyXLinkList"):
                entries = [(obj, []) for obj in value]
            elif model in getattr(dependent, "OutList", []):
                blocked.append(
                    {
                        "object": dependent.Name,
                        "property": prop,
                        "reason": "unsupported native link property " + kind,
                    }
                )
            for index, entry in enumerate(entries):
                if entry and entry[0] is model:
                    subs = list(entry[1])
                    refs.append(
                        {
                            "object": dependent.Name,
                            "property": prop,
                            "type": kind,
                            "entry": index,
                            "subelements": subs,
                        }
                    )
                    if subs and (
                        "ReadOnly" in dependent.getPropertyStatus(prop)
                        or any(
                            path == prop or path.startswith(prop + ".")
                            for path, _ in dependent.ExpressionEngine
                        )
                    ):
                        blocked.append(
                            {
                                "object": dependent.Name,
                                "property": prop,
                                "reason": "subelement link is read-only or expression-controlled",
                            }
                        )
        for prop, expression in getattr(dependent, "ExpressionEngine", []):
            if (
                re.search(r"\b" + re.escape(model.Name) + r"\b", expression)
                or ("<<" + model.Label + ">>") in expression
            ):
                blocked.append(
                    {
                        "object": dependent.Name,
                        "property": prop,
                        "reason": "expression references the model; explicit expression migration required",
                    }
                )
    return refs, blocked


def model_replacement_info(job_name, model_name):
    job = operation_job(job_name)
    model = setup_model(job, model_name)
    refs, blocked = model_references(job, model)
    source = job.Proxy.baseObject(job, model)
    return {
        "job": job.Name,
        "model": model.Name,
        "source": source.Name,
        "source_document": source.Document.Name,
        "references": refs,
        "blocked_dependencies": blocked,
        "required_subelements": sorted(
            {sub for ref in refs for sub in ref["subelements"] if sub}
        ),
        "placement": cam_json_value(model.Placement),
        "replacement_strategy": "retain clone identity, replace its native Objects source, explicitly remap subelements",
    }


def set_operation_bases(operation_name, bases):
    """Assign native multi-model Base references and regenerate dressed paths."""
    obj = App.ActiveDocument.getObject(operation_name) if App.ActiveDocument else None
    if (
        obj is None
        or not obj.isDerivedFrom("Path::Feature")
        or not hasattr(obj, "Base")
        or "LinkSubList" not in obj.getTypeIdOfProperty("Base")
    ):
        raise ValueError("Select a native operation with a Base LinkSubList")
    job = PathUtils.findParentJob(obj)
    if job is None:
        raise ValueError("Operation has no native Job")
    if not isinstance(bases, list):
        raise ValueError("bases requires a list of object/subelement references")
    references = []
    reported = []
    seen = set()
    for entry in bases:
        if not isinstance(entry, dict) or set(entry) - {"object", "subelements"}:
            raise ValueError("Each base requires object and optional subelements")
        model = operation_model(job, entry.get("object"))
        if model.Name in seen:
            raise ValueError("Group each model's subelements in one Base entry")
        seen.add(model.Name)
        subs = entry.get("subelements", [])
        if (
            not isinstance(subs, list)
            or any(not isinstance(sub, str) for sub in subs)
            or len(set(subs)) != len(subs)
        ):
            raise ValueError("subelements requires unique native names")
        for sub in subs:
            if (
                not re.fullmatch(r"(?:Face|Edge|Vertex)[1-9][0-9]*", sub)
                or model.Shape.getElement(sub).isNull()
            ):
                raise ValueError("Invalid Base subelement: " + sub)
        references.append((model, subs))
        reported.append(
            {
                "object": model.Name,
                "subelements": subs,
                "source": job.Proxy.baseObject(job, model).Name,
            }
        )
    obj.Base = references
    regenerate_setup(job)
    # Assignment can trigger topology tracking; verify exact resolved names.
    if [(base.Name, list(subs)) for base, subs in obj.Base] != [
        (base.Name, list(subs)) for base, subs in references
    ]:
        raise ValueError("Native Base did not retain the requested geometry")
    return {
        "operation": obj.Name,
        "bases": reported,
        "automatic_job_geometry": not bases,
        "commands": len(obj.Path.Commands),
        "length": float(obj.Path.Length),
        "job": job.Name,
        "recomputed": True,
        **operation_path_status(obj),
    }


def replace_setup_model(
    job_name, model_name, source_name, subelement_map, placement_mode, refit_stock
):
    job = operation_job(job_name)
    model = setup_model(job, model_name)
    if not isinstance(source_name, str) or not source_name:
        raise ValueError("source_name must identify the replacement design object")
    source = job_models(job.Document, [source_name])[0]
    old_source = job.Proxy.baseObject(job, model)
    if source is model or model in getattr(source, "OutListRecursive", []):
        raise ValueError("Replacement would create a model dependency cycle")
    if placement_mode not in ("preserve_setup", "preserve_transform"):
        raise ValueError("placement_mode must be preserve_setup or preserve_transform")
    if type(refit_stock) is not bool:
        raise TypeError("refit_stock requires boolean")
    if refit_stock and stock_kind(job.Stock) != "FromBase":
        raise ValueError("refit_stock requires FromBase stock")
    if str(getattr(model, "MapMode", "Deactivated")) != "Deactivated" or any(
        path == "Placement" or path.startswith("Placement.") or path == "Objects"
        for path, _ in model.ExpressionEngine
    ):
        raise ValueError(
            "Model placement/source is controlled by attachment or expression"
        )
    refs, blocked = model_references(job, model)
    if blocked:
        raise ValueError(
            "Cannot replace model with unmigrated dependencies: " + str(blocked)
        )
    required = {sub for ref in refs for sub in ref["subelements"] if sub}
    if not isinstance(subelement_map, dict) or set(subelement_map) != required:
        raise ValueError(
            "subelement_map must explicitly cover every referenced element: "
            + str(sorted(required))
        )
    for old, new in subelement_map.items():
        if (
            not isinstance(new, str)
            or not re.fullmatch(r"(?:Face|Edge|Vertex)[1-9][0-9]*", old)
            or not re.fullmatch(r"(?:Face|Edge|Vertex)[1-9][0-9]*", new)
        ):
            raise ValueError("Subelement map requires native FaceN/EdgeN/VertexN names")
        before = model.Shape.getElement(old)
        after = source.Shape.getElement(new)
        if before.isNull() or after.isNull() or before.ShapeType != after.ShapeType:
            raise ValueError(
                "Mapped subelements must exist and have the same shape type"
            )
    before_placement = model.Placement.copy()
    placement = before_placement
    if placement_mode == "preserve_transform":
        placement = before_placement * old_source.Placement.inverse() * source.Placement
    source_placements = {obj: obj.Placement.copy() for obj in (old_source, source)}
    ready_before = {
        obj.Name: operation_path_status(obj)["path_ready"]
        for obj in job.Operations.Group
    }
    saved_links = {
        (ref["object"], ref["property"]): getattr(
            job.Document.getObject(ref["object"]), ref["property"]
        )
        for ref in refs
        if ref["subelements"]
    }
    model.Objects = [source]
    model.Placement = placement
    model.touch()
    model.recompute()
    edited = set()
    for ref in refs:
        if not ref["subelements"]:
            continue
        dependent = job.Document.getObject(ref["object"])
        key = (dependent.Name, ref["property"])
        if key in edited:
            continue
        # Native topology tracking may have changed Face6 into ?Face6 during
        # clone replacement. Map the original selectors captured before it.
        value = saved_links[key]

        def mapped(entry):
            if entry and entry[0] is model:
                return (model, [subelement_map.get(sub, sub) for sub in entry[1]])
            return entry

        if ref["type"].removesuffix("Global").endswith("SubList"):
            value = [mapped(entry) for entry in value]
        else:
            value = mapped(value)
        setattr(dependent, ref["property"], value)
        edited.add(key)
    job.Document.recompute()
    if refit_stock:
        refit_setup_stock(job)
    regenerate_setup(job)
    if not model.Placement.isSame(placement, 1e-9) or list(model.Objects) != [source]:
        raise ValueError("Native clone did not retain replacement source/placement")
    for obj, saved in source_placements.items():
        if not obj.Placement.isSame(saved, 1e-10):
            raise ValueError("Replacement unexpectedly modified a design source")
    for obj in job.Operations.Group:
        if ready_before[obj.Name] and not operation_path_status(obj)["path_ready"]:
            raise ValueError(
                "Replacement invalidated a previously ready operation: " + obj.Name
            )
    return setup_data(job) | {
        "replaced_model": model.Name,
        "old_source": old_source.Name,
        "new_source": source.Name,
        "placement_mode": placement_mode,
        "subelement_map": subelement_map,
        "updated_properties": [
            {"object": obj, "property": prop} for obj, prop in sorted(edited)
        ],
        "source_objects_preserved": True,
        "semantic_feature_match_verified": False,
    }
