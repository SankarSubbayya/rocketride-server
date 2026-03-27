"""
Engine process spawn and teardown.

Manages the engine subprocess lifecycle: start, health-check, stop.
"""

import asyncio
import subprocess
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
    """Start the engine binary as a fully detached subprocess.

    Redirects stdout/stderr to log files under ~/.rocketride/logs/{id}/.
    Returns the process pid.

    Uses subprocess.Popen (not asyncio) so the child process is fully
    independent of the parent — it survives after the CLI exits.
    """
    log_dir = logs_dir(instance_id)
    log_dir.mkdir(parents=True, exist_ok=True)

    stdout_log = log_dir / 'stdout.log'
    stderr_log = log_dir / 'stderr.log'

    stdout_fh = open(stdout_log, 'w')
    stderr_fh = open(stderr_log, 'w')

    # The engine binary is a Python interpreter — it needs the eaas.py
    # entrypoint script as its first argument.
    script = str(binary_path.parent / 'ai' / 'eaas.py')

    try:
        if sys.platform == 'win32':
            # CREATE_NO_WINDOW (0x08000000) — no console window at all
            # CREATE_NEW_PROCESS_GROUP (0x200) — don't inherit Ctrl+C
            process = subprocess.Popen(
                [str(binary_path), script, '--port', str(port)],
                stdout=stdout_fh,
                stderr=stderr_fh,
                creationflags=0x08000000 | 0x00000200,
            )
        else:
            process = subprocess.Popen(
                [str(binary_path), script, '--port', str(port)],
                stdout=stdout_fh,
                stderr=stderr_fh,
                start_new_session=True,
            )
    except Exception as e:
        stdout_fh.close()
        stderr_fh.close()
        raise EngineError(f'Failed to start engine: {e}') from e

    return process.pid


async def stop_engine(pid: int, timeout: float = 10.0) -> None:
    """Stop an engine process by pid.

    Sends SIGTERM (Unix) or TerminateProcess (Windows), waits up to
    timeout seconds, then escalates to SIGKILL if still running.
    """
    import os
    import signal

    if sys.platform == 'win32':
        # Kill the entire process tree so child processes don't hold file locks
        try:
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # Fallback: kill just the parent process
            import ctypes

            kernel32 = ctypes.windll.kernel32
            PROCESS_TERMINATE = 0x0001
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 1)
                kernel32.WaitForSingleObject(handle, int(timeout * 1000))
                kernel32.CloseHandle(handle)
        # Give Windows a moment to release file handles
        await asyncio.sleep(0.5)
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


async def wait_healthy(
    port: int,
    pid: int = 0,
    timeout: float = 600.0,
    log_file: Path | None = None,
) -> None:
    """Poll the engine's HTTP endpoint until it responds.

    If *pid* is provided, checks that the process is still alive each
    iteration — exits immediately if the engine crashes.

    If *log_file* is provided, tails it to stdout while waiting so the
    user can see engine startup output in real time.

    Raises EngineError if the engine doesn't become healthy within timeout.
    """
    from .state import _is_pid_alive

    url = f'http://127.0.0.1:{port}'
    deadline = asyncio.get_event_loop().time() + timeout
    file_pos = 0

    def _flush_log():
        nonlocal file_pos
        if log_file and log_file.exists():
            try:
                with open(log_file, 'r') as f:
                    f.seek(file_pos)
                    new = f.read()
                    if new:
                        sys.stdout.write(new)
                        sys.stdout.flush()
                        file_pos += len(new)
            except OSError:
                pass

    while asyncio.get_event_loop().time() < deadline:
        _flush_log()

        # Check if the process has crashed
        if pid and not _is_pid_alive(pid):
            _flush_log()
            raise EngineError(f'Engine process (PID {pid}) exited before becoming healthy')

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status < 500:
                        _flush_log()
                        return
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            pass
        await asyncio.sleep(0.5)

    raise EngineError(f'Engine on port {port} did not become healthy within {timeout}s')
