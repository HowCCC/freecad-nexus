"""Native CAM library lifecycle and portable library exchange in FreeCAD."""

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path as FilePath
import re
import tempfile
import uuid

import FreeCAD as App
from Path.Tool.assets import AssetUri
from Path.Tool.camassets import cam_assets
from Path.Tool.library import Library
from Path.Tool.library.serializers import FCTLSerializer, CamoticsLibrarySerializer, LinuxCNCSerializer
from Path.Tool.toolbit import ToolBit
from Path.Tool.toolbit.serializers.fctb import FCTBSerializer


def asset_uri(identifier, kind):
    uri = AssetUri(identifier) if '://' in identifier else AssetUri.build(asset_type=kind, asset_id=identifier)
    if uri.asset_type != kind or uri.version or uri.params:
        raise ValueError('Expected unversioned ' + kind + ' identifier')
    if not re.fullmatch(r'[\w .+-]+', uri.asset_id) or uri.asset_id in ('.', '..'):
        raise ValueError('Asset identifier cannot contain path separators or URI syntax')
    return uri


def asset_store(store, *, writable=False):
    if store not in cam_assets.stores:
        raise ValueError('Unknown CAM asset store: ' + store)
    if writable and store == 'builtin':
        raise ValueError('Copy built-in assets to the local store before editing')
    return store


@contextmanager
def asset_changes(store):
    """Roll back asset files separately from the document Undo transaction."""
    asset_store(store, writable=True)
    previous = {}

    def write(uri, data):
        uri = str(uri)
        if uri not in previous:
            previous[uri] = cam_assets.get_raw(uri, store=store) if cam_assets.exists(uri, store=store) else None
        parsed = AssetUri(uri)
        cam_assets.add_raw(parsed.asset_type, parsed.asset_id, data, store=store)

    try:
        yield write
    except Exception:
        for uri, data in reversed(list(previous.items())):
            if data is None:
                if cam_assets.exists(uri, store=store):
                    cam_assets.delete(uri, store=store)
            else:
                parsed = AssetUri(uri)
                cam_assets.add_raw(parsed.asset_type, parsed.asset_id, data, store=store)
        raise


def library_data(library):
    return {'id': library.get_id(), 'uri': str(library.get_uri()), 'label': library.label,
            'tools': [{'number': library.get_bit_no_from_bit(bit), 'id': bit.get_id(),
                       'uri': str(bit.get_uri()), 'label': bit.label, 'definition': bit.to_dict()}
                      for bit in library.get_bits()], 'tool_count': len(library.get_bits())}


def validate_fctl(data):
    value = json.loads(data)
    if value.get('version') != 1 or not isinstance(value.get('tools'), list):
        raise ValueError('Expected a version 1 FreeCAD tool library')
    numbers, ids = set(), set()
    for tool in value['tools']:
        number = tool.get('nr')
        if type(number) is not int or number <= 0 or number in numbers:
            raise ValueError('Tool numbers must be unique positive integers')
        tool_id = FilePath(tool['path']).stem
        asset_uri(tool_id, 'toolbit')
        if tool_id in ids:
            raise ValueError('A ToolBit may occur only once in a library')
        numbers.add(number)
        ids.add(tool_id)
    return value


def bit_from_data(raw, identifier, store):
    """Resolve the shape from the selected store before native deserialization."""
    dependencies = {}
    for uri in FCTBSerializer.extract_dependencies(raw):
        dependencies[uri] = cam_assets.get(uri, store=[store, 'builtin'], depth=0)
    return FCTBSerializer.deserialize(raw, identifier, dependencies)


def load_library(identifier, store='local'):
    asset_store(store)
    uri = asset_uri(identifier, 'toolbitlibrary')
    data = cam_assets.get_raw(uri, store=store)
    source = validate_fctl(data)
    dependencies = {}
    for entry in source['tools']:
        dependency = asset_uri(FilePath(entry['path']).stem, 'toolbit')
        dependencies[dependency] = cam_assets.get(dependency, store=[store, 'builtin'])
    library = FCTLSerializer.deserialize(data, uri.asset_id, dependencies)
    if len(library.get_bits()) != len(source['tools']):
        raise ValueError('Library contains unresolved ToolBits')
    return library


def resolve_library_tools(entries, store):
    """Resolve all inputs before touching persistent assets."""
    resolved, numbers, ids = [], set(), set()
    for entry in entries:
        if set(entry) - {'number', 'tool_id', 'tool_file', 'tool_object'}:
            raise ValueError('Unknown library tool field')
        number = entry.get('number')
        if type(number) is not int or number <= 0 or number in numbers:
            raise ValueError('Tool numbers must be unique positive integers')
        sources = [key for key in ('tool_id', 'tool_file', 'tool_object') if entry.get(key)]
        if len(sources) != 1:
            raise ValueError('Specify exactly one of tool_id, tool_file or tool_object')
        source = sources[0]
        if source == 'tool_id':
            bit = cam_assets.get(asset_uri(entry[source], 'toolbit'), store=[store, 'builtin'])
        elif source == 'tool_file':
            bit = ToolBit.from_file(entry[source])
        else:
            obj = App.ActiveDocument.getObject(entry[source]) if App.ActiveDocument else None
            if obj is None or not isinstance(getattr(obj, 'Proxy', None), ToolBit):
                raise ValueError('Native ToolBit object required')
            bit = obj.Proxy
        asset_uri(bit.get_id(), 'toolbit')
        if bit.get_id() in ids:
            raise ValueError('A ToolBit may occur only once in a library')
        numbers.add(number)
        ids.add(bit.get_id())
        resolved.append((number, bit))
    return resolved


def store_library(library, store, *, overwrite=False):
    uri = asset_uri(library.get_id(), 'toolbitlibrary')
    asset_store(store, writable=True)
    if cam_assets.exists(uri, store=store) and not overwrite:
        raise ValueError('Library already exists; explicitly enable overwrite')
    # Never silently rewrite a ToolBit shared with a different library.
    payloads = []
    for bit in library.get_bits():
        data = FCTBSerializer.serialize(bit)
        bit_uri = asset_uri(bit.get_id(), 'toolbit')
        if cam_assets.exists(bit_uri, store=store):
            if json.loads(cam_assets.get_raw(bit_uri, store=store)) != json.loads(data):
                raise ValueError('ToolBit ID collision with different data: ' + bit.get_id())
        else:
            payloads.append((bit_uri, data))
    with asset_changes(store) as write:
        for bit_uri, data in payloads:
            write(bit_uri, data)
        write(uri, FCTLSerializer.serialize(library))
        loaded = load_library(str(uri), store)
        result = library_data(loaded)
    result['store'] = store
    return result


def create_library(label, entries, identifier='', store='local'):
    uri = asset_uri(identifier or str(uuid.uuid4()), 'toolbitlibrary')
    library = Library(label, id=uri.asset_id)
    for number, bit in resolve_library_tools(entries, store):
        library.add_bit(bit, bit_no=number)
    return store_library(library, store)


def edit_library(identifier, label, entries, remove_numbers, store='local'):
    original = load_library(identifier, store)
    library = Library(label if label is not None else original.label, id=original.get_id())
    existing = {original.get_bit_no_from_bit(bit): bit for bit in original.get_bits()}
    if len(set(remove_numbers)) != len(remove_numbers) or any(n not in existing for n in remove_numbers):
        raise ValueError('remove_numbers must identify existing unique tool numbers')
    for number in remove_numbers:
        existing.pop(number)
    replacements = resolve_library_tools(entries, store)
    for number, bit in replacements:
        existing[number] = bit
    if len({bit.get_id() for bit in existing.values()}) != len(existing):
        raise ValueError('Duplicate ToolBit; remove its old number before reassigning')
    for number, bit in sorted(existing.items()):
        library.add_bit(bit, bit_no=number)
    return store_library(library, store, overwrite=True)


def write_files(payloads, overwrite=False):
    """Stage all export files, then replace; restore preexisting files on error."""
    staged, previous, changed = {}, {}, []
    try:
        for path, data in payloads.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and not overwrite and path.read_bytes() != data:
                raise FileExistsError(str(path))
            previous[path] = path.read_bytes() if path.exists() else None
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                stream.write(data)
                staged[path] = FilePath(stream.name)
        for path, temporary in staged.items():
            os.replace(temporary, path)
            changed.append(path)
    except Exception:
        for path in reversed(changed):
            if previous[path] is None:
                path.unlink()
            else:
                path.write_bytes(previous[path])
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def export_library(identifier, filename, kind='fctl', store='local', overwrite=False):
    library = load_library(identifier, store)
    path = FilePath(filename)
    kind = kind.lower()
    serializers = {'fctl': FCTLSerializer, 'camotics': CamoticsLibrarySerializer, 'linuxcnc': LinuxCNCSerializer}
    if kind not in serializers:
        raise ValueError('format must be fctl, camotics or linuxcnc')
    payloads = {path: serializers[kind].serialize(library)}
    if kind == 'fctl':
        manifest = json.loads(payloads[path])
        for entry in manifest['tools']:
            bit = next(b for b in library.get_bits() if b.get_id() == FilePath(entry['path']).stem)
            relative = FilePath(path.stem + '_assets') / 'Bit' / (bit.get_id() + '.fctb')
            entry['path'] = relative.as_posix()
            payloads[path.parent / relative] = FCTBSerializer.serialize(bit)
            for shape_uri in FCTBSerializer.extract_dependencies(payloads[path.parent / relative]):
                shape_path = path.parent / (path.stem + '_assets') / 'Shape' / (shape_uri.asset_id + '.fcstd')
                payloads[shape_path] = cam_assets.get_raw(shape_uri, store=[store, 'builtin'])
        payloads[path] = json.dumps(manifest, indent=2).encode()
    write_files(payloads, overwrite=overwrite)
    return {'path': str(path), 'format': kind, 'files': [str(p) for p in payloads],
            'tool_count': len(library.get_bits()), 'portable_dependencies': kind == 'fctl',
            'units': 'FreeCAD user preferences' if kind == 'linuxcnc' else 'mm'}


def import_library(filename, identifier='', label=None, store='local', overwrite=False):
    # Imported custom shape assets must be staged before ToolBit construction,
    # then rolled back along with the library when any dependency is invalid.
    with asset_changes(store) as write:
        return import_library_with_shapes(filename, identifier, label, store, overwrite, write)


def import_library_with_shapes(filename, identifier, label, store, overwrite, write):
    path = FilePath(filename)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    raw = path.read_bytes()
    uri = asset_uri(identifier or str(uuid.uuid4()), 'toolbitlibrary')
    if path.suffix.lower() == '.fctl':
        source = validate_fctl(raw)
        dependencies = {}
        for entry in source['tools']:
            reference = FilePath(entry['path'])
            bit_id = reference.stem
            # Prefer explicit file references, then standard sibling Bit layout,
            # and only then the asset manager. Never silently drop a dependency.
            candidates = [path.parent / reference, path.parent.parent / 'Bit' / reference.name]
            tool_path = next((candidate for candidate in candidates if candidate.is_file()), None)
            if tool_path:
                attrs = json.loads(tool_path.read_bytes())
                attrs['id'] = bit_id
                shape_id = FilePath(attrs['shape']).stem
                shape_uri = asset_uri(shape_id, 'toolbitshape')
                shape_paths = [tool_path.parent.parent / 'Shape' / (shape_id + '.fcstd'),
                               tool_path.parent / attrs['shape']]
                shape_path = next((p for p in shape_paths if p.is_file()), None)
                if shape_path:
                    shape_data = shape_path.read_bytes()
                    if cam_assets.exists(shape_uri, store=store):
                        if cam_assets.get_raw(shape_uri, store=store) != shape_data:
                            raise ValueError('Custom shape ID collision: ' + shape_id)
                    else:
                        write(shape_uri, shape_data)
                elif not cam_assets.exists(shape_uri, store=[store, 'builtin']):
                    raise ValueError('Missing ToolBit shape dependency: ' + shape_id)
                bit = bit_from_data(json.dumps(attrs).encode(), bit_id, store)
            else:
                bit = cam_assets.get(asset_uri(bit_id, 'toolbit'), store=[store, 'builtin'])
            dependencies[asset_uri(bit_id, 'toolbit')] = bit
        library = FCTLSerializer.deserialize(raw, uri.asset_id, dependencies)
        if len(library.get_bits()) != len(source['tools']):
            raise ValueError('Import lost a ToolBit dependency')
    elif path.suffix.lower() == '.json':
        data = json.loads(raw)
        if not isinstance(data, dict) or any(not str(n).isdigit() or int(n) <= 0 for n in data) or len({int(n) for n in data}) != len(data):
            raise ValueError('Expected a CAMotics tool table keyed by positive tool number')
        for number, tool in data.items():
            if tool.get('shape', 'Cylindrical') not in ('Ballnose', 'Cylindrical', 'Conical', 'Snubnose'):
                raise ValueError('Unsupported CAMotics tool shape')
            units = tool.get('units', 'metric')
            if units not in ('metric', 'imperial'):
                raise ValueError('CAMotics units must be metric or imperial')
            for key in ('diameter', 'length'):
                value = float(tool[key])
                if not math.isfinite(value) or value <= 0:
                    raise ValueError('CAMotics tool dimensions must be finite and positive')
                tool[key] = value * (25.4 if units == 'imperial' else 1)
            tool['units'] = 'metric'
        library = CamoticsLibrarySerializer.deserialize(json.dumps(data).encode(), uri.asset_id, {})
        # Native imports use constant camotics_tool_N IDs, which collide across
        # unrelated libraries. Assign a library-scoped ID before persistence.
        for bit in library.get_bits():
            bit.set_id(uri.asset_id + '_tool_' + str(library.get_bit_no_from_bit(bit)))
    else:
        raise ValueError('Import accepts .fctl or CAMotics .json; native LinuxCNC importer is unavailable')
    if label is not None:
        renamed = Library(label, id=library.get_id())
        for bit in library.get_bits():
            renamed.add_bit(bit, library.get_bit_no_from_bit(bit))
        library = renamed
    return store_library(library, store, overwrite=overwrite)


def attach_library_tool(identifier, number, job_name, store='local'):
    from Path.Tool import Controller
    from Path.Main.Job import ObjectJob

    job = App.ActiveDocument.getObject(job_name) if App.ActiveDocument else None
    if not job or not isinstance(getattr(job, 'Proxy', None), ObjectJob):
        raise ValueError('Native Job required')
    if any(tc.ToolNumber == number for tc in job.Tools.Group):
        raise ValueError('Job already contains this tool number')
    library = load_library(identifier, store)
    bit = next((b for b in library.get_bits() if library.get_bit_no_from_bit(b) == number), None)
    if bit is None:
        raise ValueError('Tool number not found in library')
    tool = bit.attach_to_doc(doc=App.ActiveDocument)
    controller = Controller.Create(name=bit.label, tool=tool, toolNumber=number, assignViewProvider=bool(App.GuiUp))
    job.Proxy.addToolController(controller)
    App.ActiveDocument.recompute()
    return {'job': job.Name, 'tool': tool.Name, 'controller': controller.Name, 'tool_number': number}
