"""Native Assembly BOM settings, cells and checked CSV export."""

import csv
import io
import os
from pathlib import Path as FilePath
import re
import tempfile
from collections import OrderedDict
from typing import TYPE_CHECKING

import FreeCAD as App
import UtilsAssembly

if TYPE_CHECKING:
    from .assembly_joints import native_assembly, assembly_explicit_solve
    from .assembly_state import json_value

BOM_DEFAULT_COLUMNS = ["Index", "Name", "Description", "File Name", "Quantity"]
BOM_GENERATED_COLUMNS = {"Index", "Name", "File Name", "Quantity"}


def native_bom(name):
    doc = App.ActiveDocument
    bom = doc.getObject(name) if doc else None
    if bom is None or not bom.isDerivedFrom("Assembly::BomObject"):
        raise ValueError("Native Assembly BOM not found: " + name)
    return bom


def bom_column(index):
    label = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        label = chr(65 + remainder) + label
    return label


def bom_columns(columns):
    if not columns or any(
        not isinstance(col, str) or not col.strip() for col in columns
    ):
        raise ValueError("BOM columns must be nonempty names")
    if len(set(columns)) != len(columns):
        raise ValueError("BOM columns must be unique")
    if any(col == "." for col in columns):
        raise ValueError("Property columns need a property name after the dot")
    return list(columns)


def bom_value(bom, address):
    try:
        return json_value(bom.get(address))
    except ValueError:
        return ""


def assembly_parts(
    assembly,
    only_parts=False,
    detail_parts=True,
    detail_subassemblies=True,
    group_identical=True,
):
    """Count source identities within each parent, then multiply instance totals.

    Native BOM accidentally searches descendant rows when merging siblings.
    Keep each sibling bucket separate and never identify sources by Label.
    """
    rows = []

    def visit(parent, prefix, parent_count, ancestors):
        identity = (parent.Document.Name, parent.Name)
        if identity in ancestors:
            raise ValueError("Cyclic assembly structure")
        siblings = OrderedDict()
        for instance_index, child in enumerate(parent.OutList):
            if child.isDerivedFrom("Assembly::AssemblyLink"):
                source = child.LinkedObject
            elif child.isDerivedFrom("App::Link"):
                source = child.getLinkedObject(True)
            else:
                source = child
            if source is None:
                continue
            if not (
                source.isDerivedFrom("App::Part")
                or source.isDerivedFrom("Assembly::AssemblyObject")
                or source.isDerivedFrom("Part::Feature")
                and not only_parts
            ):
                continue
            key = (source.Document.Name, source.Name)
            if not group_identical:
                key = key + (instance_index,)
            if key not in siblings:
                siblings[key] = [source, []]
            siblings[key][1].append(
                {"document": child.Document.Name, "object": child.Name}
            )
        for index, (source, instances) in enumerate(siblings.values(), 1):
            path = prefix + "." + str(index) if prefix else str(index)
            count = len(instances)
            children = (
                source.isDerivedFrom("Assembly::AssemblyObject")
                and detail_subassemblies
                or source.isDerivedFrom("App::Part")
                and not source.isDerivedFrom("Assembly::AssemblyObject")
                and detail_parts
            )
            row = {
                "index": path,
                "parent_index": prefix or None,
                "document": source.Document.Name,
                "object": source.Name,
                "label": source.Label,
                "type": source.TypeId,
                "file_name": source.Document.FileName,
                "quantity": count,
                "total_quantity": count * parent_count,
                "instances": instances,
                "expanded": bool(children),
            }
            rows.append(row)
            if children:
                visit(source, path, count * parent_count, ancestors + [identity])

    visit(assembly, "", 1, [])
    flat = OrderedDict()
    for row in rows:
        if row["expanded"]:
            continue
        key = (row["document"], row["object"])
        if key not in flat:
            flat[key] = {
                key: row[key]
                for key in ("document", "object", "label", "type", "file_name")
            }
            flat[key].update(quantity=0, hierarchy_indexes=[])
        flat[key]["quantity"] += row["total_quantity"]
        flat[key]["hierarchy_indexes"].append(row["index"])
    return {
        "assembly": assembly.Name,
        "hierarchy": rows,
        "parts": list(flat.values()),
        "identity": "source document and object name",
        "quantity_scope": "sibling quantity; total_quantity multiplies ancestor instance counts",
        "flat_scope": "unexpanded components only",
    }


def bom_structure_validation(bom, data):
    assembly = next(
        (obj for obj in bom.InList if obj.isDerivedFrom("Assembly::AssemblyObject")),
        None,
    )
    if assembly is None:
        return {"checked": False, "reason": "BOM has no Assembly"}
    reference = assembly_parts(
        assembly,
        bom.onlyParts,
        bom.detailParts,
        bom.detailSubAssemblies,
        "Quantity" in bom.columnsNames,
    )
    expected = reference["hierarchy"]
    issues = []
    if len(expected) != len(data):
        issues.append("Native BOM row count differs from sibling-scoped traversal")
    for actual, row in zip(data, expected):
        for column, key in [
            ("Index", "index"),
            ("Name", "label"),
            ("Quantity", "quantity"),
        ]:
            if column in actual["values"] and actual["values"][column] != row[key]:
                issues.append(
                    "Row "
                    + str(actual["sheet_row"])
                    + " "
                    + column
                    + " differs from source hierarchy"
                )
    return {
        "checked": True,
        "valid": not issues,
        "issues": issues,
        "expected_hierarchy": expected,
    }


def bom_data(bom, recompute=False):
    if recompute:
        with assembly_explicit_solve():
            bom.recompute()
            bom.Document.recompute()
    if not bom.isValid():
        raise ValueError("Native BOM recompute failed: " + bom.getStatusString())
    used = bom.getUsedRange()
    last = int(re.search(r"\d+$", used[1]).group()) if used else 1
    columns = list(bom.columnsNames)
    rows = []
    for row in range(2, last + 1):
        values = {
            name: bom_value(bom, bom_column(index) + str(row))
            for index, name in enumerate(columns, 1)
        }
        rows.append({"sheet_row": row, "values": values})
    warnings = []
    labels = [row["values"].get("Name") for row in rows]
    if "Name" in columns and len(set(labels)) < len(labels):
        warnings.append(
            "Native BOM custom columns match source labels; duplicate labels can share custom text"
        )
    validation = bom_structure_validation(bom, rows)
    if validation.get("valid") is False:
        warnings.append(
            "Native BOM hierarchy/quantity mismatch; use assembly_parts_list for source-identity counts"
        )
    return {
        "name": bom.Name,
        "bom": bom.Name,
        "type": bom.TypeId,
        "columns": columns,
        "rows": len(rows),
        "data": rows,
        "used_range": list(used),
        "only_parts": bom.onlyParts,
        "detail_parts": bom.detailParts,
        "detail_subassemblies": bom.detailSubAssemblies,
        "warnings": warnings,
        "quantity_scope": "per containing assembly/part; subassembly child quantities are not flattened totals",
        "custom_column_identity": "native source Label matching",
        "structure_validation": validation,
    }


def create_bom(
    assembly_name, only_parts, detail_parts, detail_subassemblies, columns, name
):
    assembly = native_assembly(assembly_name)
    columns = bom_columns(BOM_DEFAULT_COLUMNS if columns is None else columns)
    if any(
        type(value) is not bool
        for value in (only_parts, detail_parts, detail_subassemblies)
    ):
        raise TypeError("BOM settings require booleans")
    bom = UtilsAssembly.getBomGroup(assembly).newObject("Assembly::BomObject", name)
    bom.columnsNames = columns
    bom.onlyParts = only_parts
    bom.detailParts = detail_parts
    bom.detailSubAssemblies = detail_subassemblies
    return bom_data(bom, True)


def configure_bom(name, columns, only_parts, detail_parts, detail_subassemblies):
    bom = native_bom(name)
    before = bom_data(bom, True)
    # Native saveCustomColumnData iterates the NEW column count, so deleting
    # columns may drop surviving custom cells to the right. Capture before.
    custom = {}
    for row in before["data"]:
        label = row["values"].get("Name")
        for col, value in row["values"].items():
            if col in BOM_GENERATED_COLUMNS or col.startswith(".") or value == "":
                continue
            if label is None:
                raise ValueError(
                    "Name column is required to preserve native custom text"
                )
            key = (str(label), col)
            if key in custom and custom[key] != value:
                raise ValueError(
                    "Ambiguous custom values for duplicate source Label: " + str(label)
                )
            custom[key] = value
    if columns is not None:
        columns = bom_columns(columns)
        if custom and "Name" not in columns:
            raise ValueError("Keep Name to preserve existing custom column identity")
    for key, value in [
        ("onlyParts", only_parts),
        ("detailParts", detail_parts),
        ("detailSubAssemblies", detail_subassemblies),
    ]:
        if value is not None and type(value) is not bool:
            raise TypeError(key + " requires a boolean")
    with assembly_explicit_solve():
        # Discard stale cells after capturing custom values: native save logic
        # otherwise uses the new Name index to identify old rows.
        if columns is not None:
            bom.clearAll()
        if columns is not None:
            bom.columnsNames = columns
        for key, value in [
            ("onlyParts", only_parts),
            ("detailParts", detail_parts),
            ("detailSubAssemblies", detail_subassemblies),
        ]:
            if value is not None:
                setattr(bom, key, value)
        after = bom_data(bom, True)
        for row in after["data"]:
            label = str(row["values"].get("Name"))
            for index, col in enumerate(bom.columnsNames, 1):
                if (label, col) in custom:
                    bom.set(
                        bom_column(index) + str(row["sheet_row"]),
                        "'" + str(custom[(label, col)]),
                    )
    result = bom_data(bom, True)
    for row in result["data"]:
        for col, value in row["values"].items():
            key = (str(row["values"].get("Name")), col)
            if key in custom and value != custom[key]:
                raise ValueError(
                    "Native BOM did not retain custom text after reconfiguration"
                )
    return result


def edit_bom_cells(name, edits):
    bom = native_bom(name)
    data = bom_data(bom, True)
    if not edits:
        raise ValueError("Provide at least one custom cell edit")
    if "Name" not in bom.columnsNames:
        raise ValueError("Name column is required for native custom cell persistence")
    labels = [row["values"]["Name"] for row in data["data"]]
    seen = set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"row", "column", "value"}:
            raise ValueError("Each edit requires row (sheet row), column and value")
        row, col, value = edit["row"], edit["column"], edit["value"]
        if type(row) is not int or not 2 <= row <= data["rows"] + 1:
            raise ValueError("row must identify a data row (sheet rows start at 2)")
        if (
            col not in bom.columnsNames
            or col in BOM_GENERATED_COLUMNS
            or col.startswith(".")
        ):
            raise ValueError("Only custom text columns can be edited")
        if not isinstance(value, str):
            raise TypeError("Custom cell value must be a string")
        if labels.count(labels[row - 2]) > 1:
            raise ValueError(
                "Native custom cells cannot distinguish duplicate source labels"
            )
        if (row, col) in seen:
            raise ValueError("Duplicate cell edit")
        seen.add((row, col))
    with assembly_explicit_solve():
        for edit in edits:
            address = bom_column(
                list(bom.columnsNames).index(edit["column"]) + 1
            ) + str(edit["row"])
            bom.set(address, "'" + edit["value"])
    result = bom_data(bom, True)
    for edit in edits:
        actual = result["data"][edit["row"] - 2]["values"][edit["column"]]
        if actual != edit["value"]:
            raise ValueError("Native BOM did not retain literal custom cell text")
    result["edited_cells"] = len(edits)
    return result


def export_bom_csv(name, file_path, overwrite, delimiter):
    bom = native_bom(name)
    result = bom_data(bom, True)
    if result["structure_validation"].get("valid") is False:
        raise ValueError(
            "Native BOM quantities/hierarchy are inconsistent; export assembly_parts_list instead"
        )
    if (
        not isinstance(delimiter, str)
        or len(delimiter) != 1
        or not delimiter.isascii()
        or delimiter in '\r\n"\\\0'
    ):
        raise ValueError(
            "delimiter must be a single ASCII character other than quotes, backslash or newline"
        )
    if not isinstance(file_path, str) or not file_path.strip():
        raise ValueError("Provide a CSV file path")
    path = FilePath(file_path).expanduser().absolute()
    if not path.parent.is_dir():
        raise FileNotFoundError("Output directory does not exist")
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ValueError("CSV output must be a regular file")
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    with tempfile.TemporaryDirectory(prefix=".mcp-bom-", dir=path.parent) as directory:
        temporary = FilePath(directory) / "output.csv"
        succeeded = bom.exportFile(str(temporary), delimiter, '"', '"')
        if not succeeded or not temporary.is_file():
            raise ValueError("Native BOM CSV exporter failed")
        contents = temporary.read_text(encoding="utf-8")
        # Installed native exporter tests only delimiters when deciding to
        # quote, omits trailing empty cells and rounds numbers to six digits.
        # Validate every field against evaluated native cells, then normalize
        # when needed so quotes/newlines, blank columns and precision survive.
        expected = [list(bom.columnsNames)]
        for row in range(2, result["rows"] + 2):
            values = []
            for index in range(1, len(bom.columnsNames) + 1):
                value = bom_value(bom, bom_column(index) + str(row))
                if isinstance(value, dict) and "value" in value:
                    value = value["value"]
                values.append(str(value))
            expected.append(values)
        try:
            rows = list(
                csv.reader(io.StringIO(contents), delimiter=delimiter, strict=True)
            )
        except csv.Error:
            rows = None
        normalized = rows != expected
        if normalized:
            with temporary.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(
                    stream, delimiter=delimiter, lineterminator="\r\n"
                ).writerows(expected)
        with temporary.open(encoding="utf-8", newline="") as stream:
            if list(csv.reader(stream, delimiter=delimiter, strict=True)) != expected:
                raise ValueError("CSV output does not match native BOM cells")
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    return {
        "bom": bom.Name,
        "path": str(path),
        "exists": True,
        "size": path.stat().st_size,
        "rows": result["rows"],
        "columns": result["columns"],
        "used_range": result["used_range"],
        "delimiter": delimiter,
        "quotechar": '"',
        "escapechar": None,
        "doublequote": True,
        "native_export": not normalized,
        "native_export_attempted": True,
        "normalized_from_native_cells": normalized,
        "quantity_values": "FreeCAD internal units; inspect BOM for unit metadata",
    }


def parts_list(assembly_name, layout, file_path, overwrite):
    if layout not in ("hierarchy", "flat"):
        raise ValueError("layout must be hierarchy or flat")
    result = assembly_parts(native_assembly(assembly_name))
    result["layout"] = layout
    if file_path:
        path = FilePath(file_path).expanduser().absolute()
        if not path.parent.is_dir():
            raise FileNotFoundError("Output directory does not exist")
        if path.is_symlink() or path.exists() and not path.is_file():
            raise ValueError("CSV output must be a regular file")
        if path.exists() and not overwrite:
            raise FileExistsError(str(path))
        columns = ["document", "object", "label", "type", "file_name", "quantity"]
        if layout == "hierarchy":
            columns = ["index", "parent_index"] + columns + ["total_quantity"]
        rows = result["hierarchy"] if layout == "hierarchy" else result["parts"]
        with tempfile.TemporaryDirectory(
            prefix=".mcp-parts-", dir=path.parent
        ) as directory:
            temporary = FilePath(directory) / "parts.csv"
            with temporary.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=columns, extrasaction="ignore"
                )
                writer.writeheader()
                writer.writerows(rows)
            if overwrite:
                os.replace(temporary, path)
            else:
                os.link(temporary, path)
        result.update(path=str(path), size=path.stat().st_size)
    return result
