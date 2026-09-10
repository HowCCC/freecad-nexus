"""Native CAM Machine assets, explicit unit conversion and controller checks.

Uses asset_uri, asset_store and asset_changes from cam_libraries.py. Machine
assets describe spindle/feed limits; they are not geometric machine models.
"""

import json
import math
import os
from pathlib import Path as FilePath
import tempfile
import uuid
from typing import TYPE_CHECKING

import FreeCAD as App
from Path.Tool.camassets import cam_assets
from Path.Tool.machine import Machine

if TYPE_CHECKING:
    # The server concatenates these sources before execution in FreeCAD.
    from .cam_libraries import asset_changes, asset_store, asset_uri


_MACHINE_UNITS = {
    "max_power": "kW",
    "min_rpm": "rpm",
    "max_rpm": "rpm",
    "max_torque": "Nm",
    "peak_torque_rpm": "rpm",
    "min_feed": "mm/min",
    "max_feed": "mm/min",
}
_STORED_UNITS = dict(
    _MACHINE_UNITS, max_power="W", min_rpm="1/s", max_rpm="1/s", peak_torque_rpm="1/s"
)


def machine_parameters(values):
    """Reject native constructor fallback values and nonphysical limits."""
    if not isinstance(values, dict) or set(values) - set(_MACHINE_UNITS):
        raise ValueError("Unknown machine field; allowed: " + ", ".join(_MACHINE_UNITS))
    checked = {}
    for key, value in values.items():
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Finite numeric value required for " + key)
        if value < 0 or (key not in ("min_rpm", "min_feed") and value == 0):
            raise ValueError("Positive limit required for " + key)
        checked[key] = value
    return checked


def machine_data(machine):
    """Return quantities in the units stated in the JSON field names."""
    machine.validate()
    return {
        "id": machine.get_id(),
        "uri": str(machine.get_uri()),
        "label": machine.label,
        "max_power_w": machine.max_power.getValueAs("W").Value,
        "min_rpm": machine.get_min_rpm_value(),
        "max_rpm": machine.get_max_rpm_value(),
        "max_torque_nm": machine.max_torque.getValueAs("Nm").Value,
        "peak_torque_rpm": machine.get_peak_torque_rpm_value(),
        "min_feed_mm_min": machine.min_feed.getValueAs("mm/min").Value,
        "max_feed_mm_min": machine.max_feed.getValueAs("mm/min").Value,
    }


def machine_from_asset(raw, identifier):
    """Decode native v1 W/Hz values as quantities, avoiding double conversion.

    This FreeCAD build's Machine.from_bytes feeds W and Hz to a constructor
    expecting numeric kW and RPM. Keep the native file schema but provide
    explicit Quantity inputs to the constructor. No upstream files are changed.
    """
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("version") != Machine.API_VERSION:
        raise ValueError("Unsupported native Machine asset version")
    unknown = set(data) - set(_STORED_UNITS) - {"version", "id", "label"}
    if unknown:
        raise ValueError("Unknown machine asset fields: " + ", ".join(sorted(unknown)))
    if not isinstance(data.get("label"), str) or not data["label"].strip():
        raise ValueError("Machine label must be nonempty")
    machine_parameters({key: data[key] for key in _STORED_UNITS})
    quantities = {
        key: App.Units.Quantity(data[key], unit) for key, unit in _STORED_UNITS.items()
    }
    machine = Machine(label=data["label"], id=identifier, **quantities)
    machine.validate()
    return machine


def load_machine(identifier, store="local"):
    asset_store(store)
    uri = asset_uri(identifier, "machine")
    return machine_from_asset(cam_assets.get_raw(uri, store=store), uri.asset_id)


def create_machine(label, values, identifier="", store="local"):
    """Persist a validated Machine and verify its decoded values before commit."""
    asset_store(store, writable=True)
    uri = asset_uri(identifier or str(uuid.uuid4()), "machine")
    if cam_assets.exists(uri, store=store):
        raise ValueError("Machine already exists: " + uri.asset_id)
    if not isinstance(label, str) or not label.strip():
        raise ValueError("Machine label must be nonempty")
    machine = Machine(label=label, id=uri.asset_id, **machine_parameters(values))
    machine.validate()
    return persist_machine(machine, store)


def same_machine(expected, actual):
    for key, value in expected.items():
        if type(value) in (int, float):
            if not math.isclose(value, actual[key], rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError("Machine persistence changed " + key)
        elif value != actual[key]:
            raise ValueError("Machine persistence changed " + key)


def persist_machine(machine, store):
    """Verify actual stored quantity values while asset rollback is active."""
    expected = machine_data(machine)
    serializer = cam_assets.get_serializer_for_class(Machine)
    uri = machine.get_uri()
    with asset_changes(store) as write:
        write(uri, machine.to_bytes(serializer))
        actual = machine_data(load_machine(str(uri), store))
        same_machine(expected, actual)
    return actual | {"store": store}


def edit_machine(identifier, values, label, store):
    asset_store(store, writable=True)
    current = load_machine(identifier, store)
    values = machine_parameters(values)
    if label is not None and (not isinstance(label, str) or not label.strip()):
        raise ValueError("Machine label must be nonempty")
    # Reconstruct from explicit constructor-unit numbers and existing native
    # quantities: setters automatically change the opposite bound, which
    # would modify fields omitted by the caller. Numeric RPM goes through
    # the native constructor's documented conversion, not a unit-string guess.
    quantities = {
        key: values[key] if key in values else getattr(current, key)
        for key in _MACHINE_UNITS
    }
    updated = Machine(
        label=current.label if label is None else label,
        id=current.get_id(),
        **quantities,
    )
    updated.validate()
    return persist_machine(updated, store)


def export_machine(identifier, file_path, store, overwrite):
    machine = load_machine(identifier, store)
    if not isinstance(file_path, str) or not file_path.strip():
        raise ValueError("Provide a machine output path")
    path = FilePath(file_path).expanduser().absolute()
    if not path.parent.is_dir():
        raise FileNotFoundError("Output directory does not exist")
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ValueError("Output must be a regular file")
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    serializer = cam_assets.get_serializer_for_class(Machine)
    payload = machine.to_bytes(serializer)
    with tempfile.TemporaryDirectory(
        prefix=".mcp-machine-", dir=path.parent
    ) as directory:
        temporary = FilePath(directory) / "machine.fcm"
        temporary.write_bytes(payload)
        same_machine(
            machine_data(machine),
            machine_data(machine_from_asset(temporary.read_bytes(), machine.get_id())),
        )
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    return machine_data(machine) | {
        "path": str(path),
        "size": path.stat().st_size,
        "format": "FreeCAD Machine v1 (.fcm)",
        "serialized_units": _STORED_UNITS,
        "store": store,
        "native_loader_unit_bug": "Installed native from_bytes interprets W/Hz as kW/RPM; MCP import corrects this",
    }


def import_machine(file_path, identifier, label, store, overwrite):
    asset_store(store, writable=True)
    path = FilePath(file_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    payload = path.read_bytes()
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Expected native Machine JSON object")
    selected = identifier or data.get("id") or path.stem
    if not isinstance(selected, str):
        raise TypeError("Machine ID must be a string")
    uri = asset_uri(selected, "machine")
    if cam_assets.exists(uri, store=store) and not overwrite:
        raise FileExistsError("Machine already exists: " + uri.asset_id)
    machine = machine_from_asset(payload, uri.asset_id)
    if label is not None:
        if not isinstance(label, str) or not label.strip():
            raise ValueError("Machine label must be nonempty")
        machine.label = label
    return persist_machine(machine, store) | {
        "imported_from": str(path.absolute()),
        "format": "FreeCAD Machine v1 (.fcm)",
    }


def inspect_machine(identifier, store="local"):
    return machine_data(load_machine(identifier, store)) | {"store": store}


def delete_machine(identifier, store="local"):
    asset_store(store, writable=True)
    uri = asset_uri(identifier, "machine")
    if not cam_assets.exists(uri, store=store):
        raise ValueError("Machine not found")
    cam_assets.delete(uri, store=store)
    return {"deleted": str(uri), "store": store}


def evaluate_machine(identifier, rpm, store="local"):
    """Evaluate the native spindle torque curve at numeric revolutions/minute."""
    if type(rpm) not in (int, float) or not math.isfinite(rpm) or rpm < 0:
        raise ValueError("rpm must be finite and non-negative")
    machine = load_machine(identifier, store)
    return machine_data(machine) | {
        "rpm": rpm,
        "torque_nm": machine.get_torque_at_rpm(rpm),
        "within_limits": machine.get_min_rpm_value()
        <= rpm
        <= machine.get_max_rpm_value(),
        "model": "FreeCAD Machine spindle torque curve",
    }


def validate_job_machine(job_name, identifier, store="local"):
    """Check all Job ToolControllers; this does not bind Machine to a Job."""
    from Path.Main.Job import ObjectJob

    job = App.ActiveDocument.getObject(job_name) if App.ActiveDocument else None
    if job is None or not isinstance(getattr(job, "Proxy", None), ObjectJob):
        raise ValueError("Native CAM Job required")
    machine = load_machine(identifier, store)
    limits = machine_data(machine)
    checks = []
    for controller in job.Tools.Group:
        values = {
            "spindle_rpm": float(controller.SpindleSpeed),
            "horizontal_feed_mm_min": controller.HorizFeed.getValueAs("mm/min").Value,
            "vertical_feed_mm_min": controller.VertFeed.getValueAs("mm/min").Value,
        }
        issues = []
        for key, value in values.items():
            low, high = (
                (limits["min_rpm"], limits["max_rpm"])
                if key == "spindle_rpm"
                else (limits["min_feed_mm_min"], limits["max_feed_mm_min"])
            )
            if not math.isfinite(value) or not low <= value <= high:
                issues.append(
                    {"property": key, "value": value, "minimum": low, "maximum": high}
                )
        checks.append(
            {
                "controller": controller.Name,
                "tool_number": controller.ToolNumber,
                **values,
                "valid": not issues,
                "issues": issues,
            }
        )
    return {
        "job": job.Name,
        "machine": limits,
        "valid": bool(checks) and all(x["valid"] for x in checks),
        "controllers": checks,
        "issues": [] if checks else ["Job has no ToolControllers"],
        "scope": "Spindle and feed limits of all Job ToolControllers",
        "machine_job_binding": False,
        "collision_check_performed": False,
    }
