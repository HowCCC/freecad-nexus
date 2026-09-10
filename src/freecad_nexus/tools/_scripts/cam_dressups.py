"""Create native dressup proxies, including GUI workbench implementations."""

import importlib
from pathlib import Path as FilePath
from typing import TYPE_CHECKING

import FreeCAD as App
from PathScripts import PathUtils

if TYPE_CHECKING:
    from .cam_discovery import dressup_catalog
    from .cam_properties import apply_parameters


def create_dressup(name, kind, base_name, parameters):
    options = {item['dressup'].lower(): item for item in dressup_catalog()}
    aliases = {'tag': 'tags', 'dogbone': 'dogboneii', 'legacydogbone': 'dogbone'}
    key = aliases.get(kind.lower(), kind.lower())
    if key not in options:
        raise ValueError('Unknown dressup: ' + kind)
    definition = options[key]
    if not definition['installed']:
        raise RuntimeError('Dressup module is not installed: ' + definition['module'])
    if definition['gui_required'] and not App.GuiUp:
        raise RuntimeError('This native dressup requires GUI FreeCAD: ' + definition['dressup'])
    document = App.ActiveDocument
    base = document.getObject(base_name) if document else None
    if base is None or not base.isDerivedFrom('Path::Feature'):
        raise ValueError('Native base operation required')
    job = PathUtils.findParentJob(base)
    if job is None or base not in job.Operations.Group:
        raise ValueError('Dressup base must be a top-level operation in its Job')
    if not base.Path.Commands:
        raise ValueError('Base operation has no toolpath')
    original_order = list(job.Operations.Group)
    module = importlib.import_module(definition['module'])
    if hasattr(module, 'Create'):
        obj = module.Create(base, name)
    else:
        obj = document.addObject('Path::FeaturePython', name)
        module.ObjectDressup(obj)
        obj.Base = base
        if callable(getattr(obj.Proxy, 'setup', None)):
            obj.Proxy.setup(obj)
        if key == 'axismap':
            obj.Radius = 45
        if App.GuiUp:
            obj.ViewObject.Proxy = module.ViewProviderDressup(obj.ViewObject)
    if key == 'axismap' and float(parameters.get('Radius', obj.Radius.Value)) <= 0:
        raise ValueError('AxisMap Radius must be positive')
    if key == 'zcorrect' and 'probefile' in parameters:
        if not FilePath(parameters['probefile']).is_file():
            raise FileNotFoundError(parameters['probefile'])
    applied = apply_parameters(obj, parameters)
    if key == 'zcorrect' and obj.probefile:
        # onChanged failures can be swallowed by FreeCAD's property setter.
        obj.Proxy._loadFile(obj, obj.probefile)
        if obj.interpSurface.isNull():
            raise ValueError('Probe file did not generate a correction surface')
    # Native factories/ViewProviders differ in whether they remove the base.
    # Preserve operation order and include only the outermost dressed path.
    job.Operations.Group = [obj if operation is base else operation for operation in original_order]
    obj.Proxy.execute(obj)
    document.recompute()
    if 'Invalid' in obj.State or not obj.Path.Commands:
        raise RuntimeError('Native dressup did not generate a valid toolpath: ' + obj.getStatusString())
    if App.GuiUp:
        base.Visibility = False
    return {'name': obj.Name, 'type': obj.TypeId, 'base': base.Name,
            'dressup': definition['dressup'], 'module': definition['module'],
            'parameters_applied': applied, 'command_count': len(obj.Path.Commands),
            'length': float(obj.Path.Length)}
