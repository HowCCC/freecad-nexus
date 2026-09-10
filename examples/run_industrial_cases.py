"""Generate industrial design examples through a real stdio MCP/socket session.

Requires GUI FreeCAD and the separately installed Robust bridge. Output includes
native CAD, viewport images and a JSON record of actual MCP tool calls.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def workflow(port, output):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from industrial_cases import generate

    params = StdioServerParameters(
        command=sys.executable,
        args=['-m', 'freecad_nexus.server', '--mode', 'socket', '--host', '127.0.0.1', '--port', str(port)],
        env=dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1'),
    )
    record = {'transport': 'stdio MCP → socket bridge → native FreeCAD', 'calls': [], 'cases': {}}
    with open(os.devnull, 'w') as sink:
        async with stdio_client(params, errlog=sink) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                record['registered_tools'] = len((await session.list_tools()).tools)

                async def call(tool_name, **arguments):
                    response = await session.call_tool(tool_name, arguments)
                    result = response.structuredContent
                    if result is None:
                        result = json.loads(next(c.text for c in response.content if c.type == 'text'))
                    entry = {'tool': tool_name, 'arguments': arguments, 'response': result}
                    record['calls'].append(entry)
                    (output / 'mcp-calls.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
                    if response.isError or not result.get('success'):
                        raise RuntimeError(f'{tool_name}: {result}')
                    print(f'OK {tool_name}', flush=True)
                    return result.get('result')

                record['freecad_version'] = await call('execute_python', code='_result_=App.Version()')
                record['cases'] = await generate(call, output)
                record['complete'] = True
                (output / 'mcp-calls.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
                (output / 'summary.json').write_text(json.dumps({k: v for k, v in record.items() if k != 'calls'}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freecad-bin', default=os.environ.get('FREECAD_BIN', '/Applications/FreeCAD.app/Contents/MacOS/FreeCAD'))
    parser.add_argument('--bridge-source', default=os.environ.get('FREECAD_BRIDGE_SOURCE', str(ROOT.parent / 'freecad-addon-robust-mcp-server/freecad/RobustMCPBridge')))
    parser.add_argument('--output', type=Path, default=ROOT / 'examples/output')
    args = parser.parse_args()
    addon = Path(args.bridge_source).resolve()
    if not (addon / 'freecad_mcp_bridge/server.py').is_file():
        parser.error('--bridge-source must point to RobustMCPBridge')
    output = args.output.resolve()
    # Each run owns a fresh directory; never replace a user's CAD files.
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error('--output must be empty; use a new directory for another run')
    with tempfile.TemporaryDirectory(prefix='freecad-nexus-') as temporary:
        tmp = Path(temporary)
        ready, stop = tmp / 'ready.json', tmp / 'stop'
        port, xmlrpc = free_port(), free_port()
        while xmlrpc == port:
            xmlrpc = free_port()
        macro = tmp / 'bridge.FCMacro'
        macro.write_text(f'''import sys, json, traceback, os
import FreeCAD as App, FreeCADGui as Gui
from PySide import QtCore
try:
    sys.path.insert(0, {str(addon)!r})
    from freecad_mcp_bridge import FreecadMCPPlugin
    plugin = FreecadMCPPlugin(host='127.0.0.1', port={port}, xmlrpc_port={xmlrpc})
    plugin.start()
    def check_stop():
        if os.path.exists({str(stop)!r}):
            timer.stop()
            plugin.stop()
            for name in list(App.listDocuments()):
                App.closeDocument(name)
            Gui.getMainWindow().close()
    timer = QtCore.QTimer()
    timer.timeout.connect(check_stop)
    timer.start(50)
    result = {{'success': True}}
except BaseException:
    result = {{'success': False, 'traceback': traceback.format_exc()}}
with open({str(ready)!r}, 'w') as stream:
    json.dump(result, stream)
''')
        with open(os.devnull, 'wb') as sink:
            process = subprocess.Popen([args.freecad_bin, '-u', str(tmp/'user.cfg'), '-s', str(tmp/'system.cfg'), str(macro)], stdout=sink, stderr=sink)
            try:
                deadline = time.monotonic() + 30
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                if not ready.exists():
                    raise RuntimeError('Isolated FreeCAD bridge did not start')
                status = json.loads(ready.read_text())
                if not status['success']:
                    raise RuntimeError(status['traceback'])
                asyncio.run(asyncio.wait_for(workflow(port, output), timeout=600))
            finally:
                stop.touch()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=3)
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)
    print(f'Artifacts: {output}')


if __name__ == '__main__':
    main()
