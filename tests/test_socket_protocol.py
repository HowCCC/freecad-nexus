"""Exercise real TCP framing and uncertain execution outcomes without FreeCAD."""

import asyncio
import contextlib
import json
from pathlib import Path
import sys

import pytest

pytest.importorskip("mcp")
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from freecad_nexus.bridge.socket import SocketBridge


@pytest.mark.parametrize("failure", ["close", "timeout", "wrong_id"])
@pytest.mark.parametrize("reconnect", [False, True])
def test_socket_uncertain_execution_is_not_replayed(failure, reconnect):
    async def run():
        executions = []
        handlers = set()
        finished = asyncio.Event()

        async def handle(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                while data := await reader.readline():
                    request = json.loads(data)
                    if request["method"] == "ping":
                        result = "pong"
                    else:
                        executions.append(request["params"]["code"])
                        first = len(executions) == 1
                        if failure == "close" and first:
                            break
                        if failure == "timeout" and first:
                            await finished.wait()
                        result = {"success": True, "result": len(executions)}
                    response_id = (
                        "wrong"
                        if failure == "wrong_id"
                        and request["method"] == "execute"
                        and len(executions) == 1
                        else request["id"]
                    )
                    writer.write(
                        json.dumps(
                            {"jsonrpc": "2.0", "id": response_id, "result": result}
                        ).encode()
                        + b"\n"
                    )
                    await writer.drain()
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                handlers.discard(task)

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        bridge = SocketBridge(
            host="127.0.0.1",
            port=server.sockets[0].getsockname()[1],
            timeout=1,
            auto_reconnect=reconnect,
        )
        try:
            await bridge.connect()
            response = await asyncio.wait_for(
                bridge.execute_python("mutation()", timeout_ms=50), 2
            )
            assert not response.success
            assert not await bridge.is_connected()
            assert executions == ["mutation()"]
            # A subsequent command cannot consume a late reply or repeat work.
            again = await asyncio.wait_for(bridge.execute_python("next_mutation()"), 1)
            assert again.success == reconnect
            assert executions == (
                ["mutation()", "next_mutation()"] if reconnect else ["mutation()"]
            )
            if reconnect:
                assert again.result == 2
        finally:
            finished.set()
            await bridge.disconnect()
            server.close()
            await server.wait_closed()
            if handlers:
                await asyncio.gather(*handlers, return_exceptions=True)

    asyncio.run(run())
