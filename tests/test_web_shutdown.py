import asyncio
import importlib
import socket
import time
from pathlib import Path
from typing import Callable
from unittest.mock import Mock

import httpx
import pytest
import uvicorn
from anyio.to_process import run_sync

from gds_migrator.web.app import SHUTDOWN_GRACE_SECONDS, app, sessions
from test_web_app import _project_files


def _blocking_calculation(marker: Path) -> None:
    marker.touch()
    time.sleep(60)


async def _wait_until(predicate: Callable[[], bool]) -> None:
    while not predicate():
        await asyncio.sleep(0.01)


@pytest.mark.parametrize("endpoint", ["migrate", "constraints"])
def test_shutdown_cancels_running_calculation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str,
) -> None:
    web_app = importlib.import_module("gds_migrator.web.app")
    marker = tmp_path / "worker-started"

    async def blocking_run_sync(
        function: Callable[..., object], *args: object, cancellable: bool = False,
    ) -> None:
        await run_sync(_blocking_calculation, marker, cancellable=cancellable)

    monkeypatch.setattr(web_app, "run_sync", blocking_run_sync)

    async def exercise_server() -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
                log_level="critical",
            )
            server = uvicorn.Server(config)
            server_task = asyncio.create_task(server.serve(sockets=[listener]))
            request_task = None
            session_id = None
            try:
                await asyncio.wait_for(_wait_until(lambda: server.started), 10)
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", timeout=10,
                ) as client:
                    layout, technology = _project_files(tmp_path)
                    response = await client.post("/api/sessions", files={
                        "layout": ("layout.gds", layout),
                        "technology": ("technology.json", technology),
                    })
                    assert response.status_code == 200, response.text
                    session_id = response.json()["id"]
                    path = f"/api/sessions/{session_id}/{endpoint}"
                    request_task = asyncio.create_task(
                        client.post(path) if endpoint == "migrate"
                        else client.get(path, params={"cell": "TOP"})
                    )
                    await asyncio.wait_for(_wait_until(marker.exists), 10)
                    response = await client.get(f"/api/sessions/{session_id}")
                    assert response.status_code == 200

                    # The first Ctrl+C sets this flag before graceful shutdown.
                    server.should_exit = True
                    await asyncio.wait_for(server_task, SHUTDOWN_GRACE_SECONDS + 5)
                    response = await request_task
                    assert response.status_code == 500
            finally:
                server.should_exit = True
                if request_task is not None and not request_task.done():
                    request_task.cancel()
                    await asyncio.gather(request_task, return_exceptions=True)
                if not server_task.done():
                    await asyncio.wait_for(server_task, SHUTDOWN_GRACE_SECONDS + 5)
                if session_id is not None:
                    project = sessions.pop(session_id)
                    project.temporary_directory.cleanup()

    asyncio.run(exercise_server())


def test_web_entrypoint_bounds_graceful_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    web_app = importlib.import_module("gds_migrator.web.app")
    start_server = Mock()
    monkeypatch.setattr(uvicorn, "run", start_server)

    web_app.run()

    start_server.assert_called_once_with(
        "gds_migrator.web.app:app",
        host="127.0.0.1",
        port=8000,
        timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
    )
