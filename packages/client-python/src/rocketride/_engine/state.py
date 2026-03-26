"""
SQLite-backed state for tracked engine instances.

File: ~/.rocketride/instances/state.db

Uses SQLite for concurrent access safety — the SDK auto-spawn and CLI
can hit the state file simultaneously without race conditions.
"""

import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from .paths import state_db_path, ensure_dirs

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    id              TEXT PRIMARY KEY,
    pid             INTEGER NOT NULL,
    port            INTEGER NOT NULL,
    version         TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    owner           TEXT NOT NULL,
    restart_count   INTEGER NOT NULL DEFAULT 0
);
"""

_MIGRATIONS = [
    # Add restart_count column for existing databases
    'ALTER TABLE instances ADD COLUMN restart_count INTEGER NOT NULL DEFAULT 0',
]


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given pid is still running."""
    if pid == 0:
        return False
    if sys.platform == 'win32':
        import ctypes

        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _get_process_memory(pid: int) -> Optional[int]:
    """Return the RSS (resident set size) in bytes for a process, or None."""
    if pid == 0 or not _is_pid_alive(pid):
        return None
    if sys.platform == 'win32':
        import ctypes
        import ctypes.wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ('cb', ctypes.wintypes.DWORD),
                ('PageFaultCount', ctypes.wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            return None
        try:
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), ctypes.sizeof(counters)):
                return counters.WorkingSetSize
            return None
        finally:
            kernel32.CloseHandle(handle)
    else:
        # Linux / macOS: read from /proc
        try:
            with open(f'/proc/{pid}/status') as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        # Value is in kB
                        return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            pass
        return None


class StateDB:
    """Async context manager wrapping the instances state database."""

    def __init__(self):
        self._db: Optional[aiosqlite.Connection] = None

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def open(self):
        ensure_dirs()
        self._db = await aiosqlite.connect(str(state_db_path()))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        # Apply migrations for existing databases
        for sql in _MIGRATIONS:
            try:
                await self._db.execute(sql)
                await self._db.commit()
            except Exception:
                pass  # Column already exists

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    async def register(
        self,
        instance_id: str,
        pid: int,
        port: int,
        version: str,
        owner: str,
        restart_count: int = 0,
    ) -> None:
        """Register a running engine instance."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            'INSERT OR REPLACE INTO instances (id, pid, port, version, started_at, owner, restart_count) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (instance_id, pid, port, version, now, owner, restart_count),
        )
        await self._db.commit()

    async def unregister(self, instance_id: str) -> None:
        """Remove an instance from the state database."""
        await self._db.execute('DELETE FROM instances WHERE id = ?', (instance_id,))
        await self._db.commit()

    async def get(self, instance_id: str) -> Optional[Dict[str, Any]]:
        """Get a single instance by id."""
        cursor = await self._db.execute('SELECT * FROM instances WHERE id = ?', (instance_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all(self) -> List[Dict[str, Any]]:
        """Get all registered instances."""
        cursor = await self._db.execute('SELECT * FROM instances')
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def next_id(self) -> str:
        """Return the next sequential integer ID as a string."""
        cursor = await self._db.execute('SELECT id FROM instances ORDER BY CAST(id AS INTEGER) DESC LIMIT 1')
        row = await cursor.fetchone()
        if row:
            try:
                return str(int(row['id']) + 1)
            except (ValueError, TypeError):
                pass
        return '0'

    async def find_running(self) -> Optional[Dict[str, Any]]:
        """Return the first registered instance whose pid is still alive.

        Stale entries (dead pids) are cleaned up automatically.
        """
        all_instances = await self.get_all()
        for inst in all_instances:
            if inst['pid'] == 0:
                continue  # Installed but never started — skip
            if _is_pid_alive(inst['pid']):
                return inst
            # Clean up stale entry
            await self.unregister(inst['id'])
        return None
