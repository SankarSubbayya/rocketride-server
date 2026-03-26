"""
Engine process spawn and teardown.

Manages the engine subprocess lifecycle: start, health-check, stop.
"""

import asyncio
import sys
from pathlib import Path

import aiohttp

from ..core.exceptions import EngineError
from .paths import logs_dir


async def spawn_engine(
    binary_path: Path,
    port: int,
    instance_id: str,
) -> int:
    """Start the engine binary as a subprocess.

    Redirects stdout/stderr to log files under ~/.rocketride/logs/{id}/.
    Returns the process pid.
    """
    log_dir = logs_dir(instance_id)
    log_dir.mkdir(parents=True, exist_ok=True)

    stdout_log = log_dir / 'stdout.log'
    stderr_log = log_dir / 'stderr.log'

    stdout_fh = open(stdout_log, 'w')
    stderr_fh = open(stderr_log, 'w')

    try:
        if sys.platform == 'win32':
            # On Windows, use CREATE_NEW_PROCESS_GROUP so the child
            # doesn't receive Ctrl+C from the parent console.
            process = await asyncio.create_subprocess_exec(
                str(binary_path),
                '--port',
                str(port),
                stdout=stdout_fh,
                stderr=stderr_fh,
                creationflags=0x00000200,  # CREATE_NEW_PROCESS_GROUP
            )
        else:
            process = await asyncio.create_subprocess_exec(
                str(binary_path),
                '--port',
                str(port),
                stdout=stdout_fh,
                stderr=stderr_fh,
                start_new_session=True,
            )
    except Exception as e:
        stdout_fh.close()
        stderr_fh.close()
        raise EngineError(f'Failed to start engine: {e}') from e

    # Don't close the file handles — the subprocess owns them now.
    # They'll be closed when the subprocess exits.
    return process.pid


async def stop_engine(pid: int, timeout: float = 10.0) -> None:
    """Stop an engine process by pid.

    Sends SIGTERM (Unix) or TerminateProcess (Windows), waits up to
    timeout seconds, then escalates to SIGKILL if still running.
    """
    import os
    import signal

    if sys.platform == 'win32':
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
        return

    # Unix: SIGTERM then SIGKILL
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return  # Already gone

    # Wait for process to exit
    for _ in range(int(timeout * 10)):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return  # Exited
        await asyncio.sleep(0.1)

    # Still alive — escalate
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def wait_healthy(port: int, timeout: float = 30.0) -> None:
    """Poll the engine's HTTP endpoint until it responds.

    Raises EngineError if the engine doesn't become healthy within timeout.
    """
    url = f'http://127.0.0.1:{port}'
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status < 500:
                        return
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            pass
        await asyncio.sleep(0.5)

    raise EngineError(f'Engine on port {port} did not become healthy within {timeout}s')
