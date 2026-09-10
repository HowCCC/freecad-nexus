"""Reinsert edited faces into their original native shell/solid structure."""

import math
from typing import TYPE_CHECKING

import FreeCAD as App
import Part

if TYPE_CHECKING:
    from .surface_geometry import surface_object, surface_face, surface_output


def surface_sewn_shell(faces, tolerance):
    """Use native sewing with an explicit tolerance, then release staging objects."""
    doc = App.ActiveDocument
    source = sewing = None
    try:
        source = doc.addObject("Part::Feature", "MCPSewingInput")
        source.Shape = Part.makeCompound(faces)
        sewing = doc.addObject("Surface::Sewing", "MCPSewingStage")
        sewing.ShapeList = [(source, ["Face" + str(i + 1) for i in range(len(faces))])]
        sewing.Tolerance = tolerance
        sewing.Nonmanifold = False
        sewing.CutFreeEdges = False
        sewing.recompute()
        shape = sewing.Shape.copy()
        if shape.isNull() or not shape.isValid() or "Invalid" in sewing.State:
            raise ValueError("Native face sewing failed: " + sewing.getStatusString())
        # Sewing a single sphere/torus returns its closed Face rather than a
        # Shell. Wrap that exact topology; no surfaces or seams are rebuilt.
        if shape.ShapeType == "Face" and len(faces) == 1:
            shape = Part.makeShell([shape])
        if len(shape.Shells) != 1 or len(shape.Faces) != len(faces):
            raise ValueError(
                "Replacement faces must form one shell without splitting or losing faces"
            )
        shell = shape.Shells[0]
        if len(shell.Faces) != len(faces):
            raise ValueError("Replacement left detached faces outside the sewn shell")
        return shell
    finally:
        if sewing is not None:
            doc.removeObject(sewing.Name)
        if source is not None:
            doc.removeObject(source.Name)


def surface_replace_faces(object_name, replacements, name, tolerance):
    obj = surface_object(object_name)
    shape = obj.Shape
    if not shape.isValid() or not shape.Shells:
        raise ValueError(
            "Face replacement requires a valid shell, solid or compound containing shells"
        )
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Sewing tolerance must be finite and positive, in mm")
    if not isinstance(replacements, list) or not replacements:
        raise ValueError("Provide at least one face replacement")
    selected, records = {}, []
    shells = list(shape.Shells)
    for item in replacements:
        if not isinstance(item, dict) or set(item) - {
            "face_index",
            "object",
            "replacement_face_index",
            "reverse",
        }:
            raise ValueError(
                "Replacements accept face_index, object, replacement_face_index and reverse"
            )
        index = item.get("face_index")
        if type(index) is not int or not 0 <= index < len(shape.Faces):
            raise IndexError("face_index must select a zero-based source face")
        if index in selected:
            raise ValueError("A source face can only be replaced once")
        source_name = item.get("object")
        if not isinstance(source_name, str) or not source_name:
            raise ValueError("Replacement object name is required")
        replacement_index = item.get("replacement_face_index", 0)
        _, face = surface_face(source_name, replacement_index)
        reverse = item.get("reverse", False)
        if type(reverse) is not bool:
            raise ValueError("reverse must be a boolean")
        face = face.copy()
        if reverse:
            face.reverse()
        if not face.isValid():
            raise ValueError("Replacement face is invalid")
        old = shape.Faces[index]
        owners = [
            i
            for i, shell in enumerate(shells)
            if any(old.isSame(f) for f in shell.Faces)
        ]
        if len(owners) != 1:
            raise ValueError(
                "Each selected face must belong to exactly one source shell"
            )
        selected[index] = face
        records.append(
            {
                "face_index": index,
                "replacement_object": source_name,
                "replacement_face_index": replacement_index,
                "shell_index": owners[0],
                "reverse": reverse,
            }
        )
    shell_replacements, shell_results = [], []
    for index in sorted({r["shell_index"] for r in records}):
        shell = shells[index]
        faces = []
        for face in shell.Faces:
            target = next((i for i in selected if face.isSame(shape.Faces[i])), None)
            faces.append(face.copy() if target is None else selected[target])
        sewn = surface_sewn_shell(faces, tolerance)
        if shell.isClosed() != sewn.isClosed():
            raise ValueError(
                "Face replacement changed shell closure; fit every shared boundary before replacement"
            )
        shell_replacements.append((shell, sewn))
        shell_results.append(
            {
                "shell_index": index,
                "face_count": len(sewn.Faces),
                "closed": sewn.isClosed(),
            }
        )
    result = shape.replaceShape(shell_replacements)
    if (
        result.ShapeType != shape.ShapeType
        or len(result.Solids) != len(shape.Solids)
        or len(result.Shells) != len(shape.Shells)
        or len(result.Faces) != len(shape.Faces)
        or not result.isValid()
    ):
        raise ValueError(
            "Replaced shape did not retain valid source shell/solid structure"
        )
    if any(solid.Volume <= 0 or not solid.isClosed() for solid in result.Solids):
        raise ValueError("Replaced solid must be closed with positive oriented volume")
    data = surface_output(result, name or obj.Name + "_replaced")
    return data | {
        "source": obj.Name,
        "replacements": records,
        "shells": shell_results,
        "tolerance_mm": tolerance,
        "parametric": False,
        "face_indices_preserved": False,
        "source_modified": False,
    }
