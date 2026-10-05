"""Reusable subprocess API-server harness for integration tests.

Some Stage 9 guarantees are only observable across *independent* processes: each
API instance owns its own ``RealtimeHub`` and its own PostgreSQL ``LISTEN``
connection, so an event committed by one instance must reach an SSE client on
another through PostgreSQL alone. Threads (or an in-process ASGI app) share the
process-global engine and hub, which would let such a test pass without proving
cross-process delivery. This harness therefore launches real ``uvicorn``
subprocesses on OS-assigned free ports.

Use :func:`running_api_server` as a context manager: it starts the server,
waits for ``/health/ready``, yields a :class:`RunningServer`, and always tears
the subprocess down — even when the enclosing test fails.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    """Return an OS-assigned free TCP port on the loopback interface.

    Binding to port 0 makes the kernel pick a free port, so tests never collide
    with a fixed port or with each other.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class RunningServer:
    """A live ``uvicorn`` subprocess serving the real API on a free port."""

    process: subprocess.Popen[str]
    port: int
    base_url: str
    log_path: Path

    def logs(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - best-effort diagnostics
            return ""

    def stop(self) -> None:
        _terminate(self.process)


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:  # pragma: no cover - stubborn shutdown
        process.kill()
        process.wait(timeout=10)


def _wait_ready(server: RunningServer, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    url = f"{server.base_url}/health/ready"
    last_error = "no attempt made"
    while time.monotonic() < deadline:
        if server.process.poll() is not None:
            raise AssertionError(
                f"API server exited early (code {server.process.returncode}):\n{server.logs()}"
            )
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}: {response.text}"
        except Exception as exc:  # noqa: BLE001 - keep polling until the deadline
            last_error = str(exc)
        time.sleep(0.1)
    raise AssertionError(f"API server never became ready ({last_error}):\n{server.logs()}")


@contextmanager
def running_api_server(
    env: dict[str, str],
    *,
    ready_timeout: float = 40.0,
) -> Iterator[RunningServer]:
    """Run one real API instance; yield it and always clean it up.

    ``env`` is merged over the current environment, so callers only specify the
    settings that matter (database URL, secrets, cookie mode). The port is
    chosen by the kernel and passed explicitly to ``uvicorn``.
    """
    port = free_port()
    merged = {**os.environ, **env, "TAKEPLACE_API_PORT": str(port)}
    log_fd, log_name = tempfile.mkstemp(prefix="takeplace-api-", suffix=".log")
    log_file = os.fdopen(log_fd, "w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
            "--no-access-log",
        ],
        cwd=BACKEND_ROOT,
        env=merged,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    server = RunningServer(
        process=process,
        port=port,
        base_url=f"http://127.0.0.1:{port}",
        log_path=Path(log_name),
    )
    try:
        _wait_ready(server, ready_timeout)
        yield server
    finally:
        _terminate(process)
        log_file.close()
