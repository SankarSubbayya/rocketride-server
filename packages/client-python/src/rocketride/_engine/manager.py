"""
Engine lifecycle orchestrator.

Implements the auto-spawn decision tree:
1. Check state.db for a running instance -> reuse it
2. Compatible binary installed -> spawn it
3. No binary -> download latest compatible -> spawn

Teardown only happens if we started the engine ourselves.
"""

import signal
import sys
import uuid
from pathlib import Path
from typing import Optional, Tuple

from .downloader import download_engine
from .paths import engine_binary, rocketride_home
from .ports import find_available_port
from .process import spawn_engine, stop_engine, wait_healthy
from .resolver import get_compat_range, resolve_compatible_version
from .state import StateDB


class EngineManager:
    """High-level engine lifecycle manager.

    Used by RocketRideClient for auto-spawn and by the CLI ``run`` command.
    """

    def __init__(self):
        self._instance_id: Optional[str] = None
        self._port: Optional[int] = None
        self._we_started: bool = False
        self._original_sigterm = None
        self._original_sigint = None

    @property
    def we_started(self) -> bool:
        return self._we_started

    @property
    def uri(self) -> Optional[str]:
        if self._port is None:
            return None
        return f'http://127.0.0.1:{self._port}'

    async def ensure_running(self) -> Tuple[str, bool]:
        """Ensure an engine instance is running.

        Returns (uri, we_started) where we_started indicates whether
        this manager spawned the instance (and is therefore responsible
        for teardown).
        """
        async with StateDB() as db:
            # 1. Check for an existing live instance
            existing = await db.find_running()
            if existing:
                self._port = existing['port']
                self._instance_id = existing['id']
                self._we_started = False
                return (self.uri, False)

            # 2. Find or download a compatible binary
            binary = await self._resolve_binary()

            # 3. Spawn
            port = find_available_port()
            instance_id = uuid.uuid4().hex[:12]

            pid = await spawn_engine(binary, port, instance_id)
            await db.register(instance_id, pid, port, self._version, 'sdk')

        # Wait for the engine to be ready
        await wait_healthy(port)

        self._port = port
        self._instance_id = instance_id
        self._we_started = True

        # Register cleanup handlers
        self._register_signal_handlers()

        return (self.uri, True)

    async def teardown(self) -> None:
        """Stop the engine if we started it, and unregister from state."""
        if not self._we_started or not self._instance_id:
            return

        async with StateDB() as db:
            inst = await db.get(self._instance_id)
            if inst:
                await stop_engine(inst['pid'])
                await db.unregister(self._instance_id)

        self._restore_signal_handlers()
        self._we_started = False
        self._instance_id = None
        self._port = None

    async def _resolve_binary(self) -> Path:
        """Find an installed compatible binary or download one."""
        compat = get_compat_range()

        # Check for any already-installed compatible version
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        spec = SpecifierSet(compat)
        engines_root = rocketride_home() / 'engines'

        if engines_root.exists():
            installed = []
            for entry in engines_root.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    v = Version(entry.name)
                except Exception:
                    continue
                if v in spec and engine_binary(entry.name).exists():
                    installed.append((v, entry.name))

            if installed:
                # Use the latest installed compatible version
                installed.sort(key=lambda x: x[0], reverse=True)
                best_version = installed[0][1]
                self._version = best_version
                return engine_binary(best_version)

        # Nothing installed — download the latest compatible
        version = await resolve_compatible_version(compat)
        self._version = version
        return await download_engine(version)

    def _register_signal_handlers(self) -> None:
        """Register signal handlers to clean up on Ctrl+C / SIGTERM."""
        if sys.platform == 'win32':
            # On Windows, only SIGINT is supported in Python
            self._original_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self._signal_handler)
        else:
            self._original_sigterm = signal.getsignal(signal.SIGTERM)
            self._original_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)

    def _restore_signal_handlers(self) -> None:
        """Restore original signal handlers."""
        if self._original_sigint is not None:
            signal.signal(signal.SIGINT, self._original_sigint)
            self._original_sigint = None
        if sys.platform != 'win32' and self._original_sigterm is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm)
            self._original_sigterm = None

    def _signal_handler(self, signum, frame):
        """Emergency cleanup on signal — run teardown synchronously."""
        import os

        if self._we_started and self._instance_id:
            # Best-effort synchronous cleanup
            try:
                # We can't run async teardown from a signal handler,
                # so do a direct pid kill.
                # Open a synchronous connection to clean up state.
                import sqlite3
                from .paths import state_db_path

                db_path = str(state_db_path())
                if os.path.exists(db_path):
                    conn = sqlite3.connect(db_path)
                    cursor = conn.execute(
                        'SELECT pid FROM instances WHERE id = ?',
                        (self._instance_id,),
                    )
                    row = cursor.fetchone()
                    if row:
                        pid = row[0]
                        if sys.platform == 'win32':
                            import ctypes

                            kernel32 = ctypes.windll.kernel32
                            handle = kernel32.OpenProcess(0x0001, False, pid)
                            if handle:
                                kernel32.TerminateProcess(handle, 1)
                                kernel32.CloseHandle(handle)
                        else:
                            os.kill(pid, signal.SIGTERM)
                    conn.execute(
                        'DELETE FROM instances WHERE id = ?',
                        (self._instance_id,),
                    )
                    conn.commit()
                    conn.close()
            except Exception:
                pass

        # Re-raise the signal with the original handler
        original = self._original_sigterm if signum == getattr(signal, 'SIGTERM', None) else self._original_sigint
        if callable(original):
            original(signum, frame)
        elif original == signal.SIG_DFL:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
