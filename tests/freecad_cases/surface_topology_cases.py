"""Reinsert spline edits into shells/solids, retaining cavities and placement."""

from pathlib import Path


def run(root, output):
    import FreeCAD as App
    import Part
    from workflows import tools_for

    call = tools_for(root, "surface")
    doc = App.newDocument("SurfaceReplacement")

    def obj(name, shape):
        feature = doc.addObject("Part::Feature", name)
        feature.Shape = shape
        return feature

    box = obj("Box", Part.makeBox(10, 10, 10))
    box.Placement = App.Placement(
        App.Vector(12, -5, 7), App.Rotation(App.Vector(1, 2, 3), 31)
    )
    before = box.Shape.exportBrepToString()
    replacements = []
    # The actual registered spline adapter supplies the replacement faces.
    for index in (0, 2, 5):
        nurbs = call("surface_to_nurbs", object_name=box.Name)
        edit = call(
            "surface_increase_degree",
            object_name=nurbs["name"],
            face_index=index,
            u_degree=3,
            v_degree=3,
        )
        replacements.append({"face_index": index, "object": edit["name"]})
    replaced = call(
        "surface_replace_faces", object_name=box.Name, replacements=replacements
    )
    output_name = replaced["name"]
    result = doc.getObject(output_name).Shape
    assert result.isValid() and result.isClosed() and result.ShapeType == "Solid"
    assert abs(result.Volume - 1000) < 1e-7
    assert (result.CenterOfMass - box.Shape.CenterOfMass).Length < 1e-7
    assert box.Shape.exportBrepToString() == before
    assert len(replaced["replacements"]) == 3 and not replaced["source_modified"]
    assert not replaced["parametric"] and not replaced["face_indices_preserved"]
    assert not any(o.Name.startswith("MCPSewing") for o in doc.Objects)
    # Native direct face replacement leaves duplicated unsewn boundaries.
    naive = box.Shape.replaceShape(
        [(box.Shape.Faces[0], doc.getObject(replacements[0]["object"]).Shape)]
    )
    assert not naive.isValid() or not naive.isClosed()
    # Inner shell orientation/cavity must survive, not become an added solid.
    cavity = obj(
        "Cavity",
        Part.makeBox(10, 10, 10).cut(Part.makeBox(2, 2, 2, App.Vector(4, 4, 4))),
    )
    inner = next(
        i for i, face in enumerate(cavity.Shape.Faces) if abs(face.Area - 4) < 1e-7
    )
    patch = obj("InnerPatch", cavity.Shape.Faces[inner].toNurbs())
    result = call(
        "surface_replace_faces",
        object_name=cavity.Name,
        replacements=[{"face_index": inner, "object": patch.Name}],
    )
    assert abs(result["volume"] - 992) < 1e-7 and result["solids"] == 1
    assert len(doc.getObject(result["name"]).Shape.Shells) == 2
    # Two solids and an unrelated edge retain compound structure and geometry.
    compound = obj(
        "Compound",
        Part.makeCompound(
            [
                Part.makeBox(2, 3, 4),
                Part.makeBox(3, 4, 5, App.Vector(20, 0, 0)),
                Part.makeLine(App.Vector(0, -2, 0), App.Vector(5, -2, 0)),
            ]
        ),
    )
    patch = obj("CompoundPatch", compound.Shape.Faces[7].toNurbs())
    result = call(
        "surface_replace_faces",
        object_name=compound.Name,
        replacements=[{"face_index": 7, "object": patch.Name}],
    )
    assert result["shape_type"] == "Compound" and result["solids"] == 2
    assert abs(result["volume"] - 84) < 1e-7
    assert len(doc.getObject(result["name"]).Shape.Edges) == len(compound.Shape.Edges)
    # Open shell remains open, with the same two joined faces.
    open_box = Part.makeBox(3, 4, 5)
    opened = obj("OpenShell", Part.makeShell([open_box.Faces[i] for i in (0, 2)]))
    patch = obj("OpenPatch", opened.Shape.Faces[0].toNurbs())
    result = call(
        "surface_replace_faces",
        object_name=opened.Name,
        replacements=[{"face_index": 0, "object": patch.Name}],
    )
    assert result["shape_type"] == "Shell" and not result["shells"][0]["closed"]
    # Reversed replacement can be repaired explicitly, with the original retained.
    flipped = obj("Flipped", box.Shape.Faces[0].toNurbs().reversed())
    result = call(
        "surface_replace_faces",
        object_name=box.Name,
        replacements=[{"face_index": 0, "object": flipped.Name, "reverse": True}],
    )
    assert abs(result["volume"] - 1000) < 1e-7
    # Change real geometry: raise only interior poles of the top face while
    # retaining all shared box boundaries, then reconstruct a larger solid.
    plain = obj("Plain", Part.makeBox(10, 10, 10))
    nurbs = call("surface_to_nurbs", object_name=plain.Name)
    top = next(i for i, f in enumerate(plain.Shape.Faces) if f.CenterOfMass.z == 10)
    elevated = call(
        "surface_increase_degree",
        object_name=nurbs["name"],
        face_index=top,
        u_degree=3,
        v_degree=3,
    )
    edited_face = doc.getObject(elevated["name"]).Shape.Faces[0]
    edits = []
    for u in (2, 3):
        for v in (2, 3):
            point = edited_face.Surface.getPole(u, v)
            edits.append(
                {"u_index": u, "v_index": v, "pole": [point.x, point.y, point.z + 2]}
            )
    deformed = call(
        "surface_edit_control_net", object_name=elevated["name"], edits=edits
    )
    result = call(
        "surface_replace_faces",
        object_name=plain.Name,
        replacements=[{"face_index": top, "object": deformed["name"]}],
    )
    # Bicubic Bernstein integral: four interior poles each contribute 1/16.
    assert abs(result["volume"] - 1050) < 1e-5, result
    assert plain.Shape.Volume < 1000.00001
    # Shared seams and collapsed polar boundaries must survive reinsertion.
    for kind, shape in (
        ("Sphere", Part.makeSphere(5)),
        ("Cylinder", Part.makeCylinder(5, 10)),
        ("Torus", Part.makeTorus(8, 2)),
    ):
        source = obj(kind, shape)
        nurbs = call("surface_to_nurbs", object_name=source.Name)
        result = call(
            "surface_replace_faces",
            object_name=source.Name,
            replacements=[{"face_index": 0, "object": nurbs["name"]}],
        )
        actual = doc.getObject(result["name"]).Shape
        assert actual.isValid() and actual.isClosed() and len(actual.Solids) == 1
        # Native area/volume integration differs for equivalent NURBS forms.
        assert abs(actual.Volume - shape.Volume) < shape.Volume * 0.01
    # Gaps and ambiguous mappings must fail without outputs or staging objects.
    moved = box.Shape.Faces[0].toNurbs()
    moved.translate(App.Vector(2, 3, 4))
    gap = obj("Gap", moved)
    names = {o.Name for o in doc.Objects}
    for mapping in (
        [{"face_index": 0, "object": gap.Name}],
        [replacements[0], replacements[0]],
        [{"face_index": -1, "object": patch.Name}],
        [{"face_index": 0, "object": patch.Name, "replacement_face_index": 9}],
    ):
        call(
            "surface_replace_faces",
            object_name=box.Name,
            replacements=mapping,
            expect_success=False,
        )
        assert {o.Name for o in doc.Objects} == names
        assert box.Shape.exportBrepToString() == before
    filename = str(Path(output) / "face_replacement.FCStd")
    doc.saveAs(filename)
    App.closeDocument(doc.Name)
    doc = App.openDocument(filename)
    shape = doc.getObject(output_name).Shape
    assert shape.isValid() and shape.isClosed() and abs(shape.Volume - 1000) < 1e-7
    return {
        "multi_face": True,
        "solid_volume": 1000,
        "cavity_volume": 992,
        "compound": True,
        "open_shell": True,
        "gap_rollback": True,
        "saved_reopened": True,
    }
