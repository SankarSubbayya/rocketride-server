"""
Unit tests for the _engine subpackage.

All tests are self-contained — no live server, no network, no disk
writes to the real ~/.rocketride directory.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from rocketride._engine import paths, platform, ports
from rocketride._engine.state import StateDB, _is_pid_alive
from rocketride._engine.resolver import get_compat_range, resolve_compatible_version
from rocketride._engine.downloader import download_engine
from rocketride._engine.process import wait_healthy
from rocketride._engine.manager import EngineManager
from rocketride.core.exceptions import (
    UnsupportedPlatformError,
    EngineError,
    EngineNotFoundError,
)


# ── paths ────────────────────────────────────────────────────────


class TestPaths:
    def test_rocketride_home(self):
        assert paths.rocketride_home() == Path.home() / '.rocketride'

    def test_engines_dir(self):
        assert paths.engines_dir('3.1.0') == Path.home() / '.rocketride' / 'engines' / '3.1.0'

    def test_engine_binary_unix(self):
        with patch.object(sys, 'platform', 'linux'):
            result = paths.engine_binary('3.1.0')
            assert result.name == 'rocketride-engine'

    def test_engine_binary_windows(self):
        with patch.object(sys, 'platform', 'win32'):
            result = paths.engine_binary('3.1.0')
            assert result.name == 'rocketride-engine.exe'

    def test_state_db_path(self):
        assert paths.state_db_path() == Path.home() / '.rocketride' / 'instances' / 'state.db'

    def test_logs_dir(self):
        assert paths.logs_dir('abc123') == Path.home() / '.rocketride' / 'logs' / 'abc123'

    def test_ensure_dirs(self, tmp_path):
        with patch.object(paths, 'rocketride_home', return_value=tmp_path / '.rocketride'):
            paths.ensure_dirs()
            assert (tmp_path / '.rocketride').is_dir()
            assert (tmp_path / '.rocketride' / 'engines').is_dir()
            assert (tmp_path / '.rocketride' / 'instances').is_dir()
            assert (tmp_path / '.rocketride' / 'logs').is_dir()


# ── platform ─────────────────────────────────────────────────────


class TestPlatform:
    def test_darwin_arm64(self):
        with patch('rocketride._engine.platform._platform.system', return_value='Darwin'), patch('rocketride._engine.platform._platform.machine', return_value='arm64'), patch.object(sys, 'platform', 'darwin'):
            assert platform.get_platform() == ('darwin', 'arm64')

    def test_linux_x86_64(self):
        with patch('rocketride._engine.platform._platform.system', return_value='Linux'), patch('rocketride._engine.platform._platform.machine', return_value='x86_64'), patch.object(sys, 'platform', 'linux'):
            assert platform.get_platform() == ('linux', 'x64')

    def test_linux_amd64(self):
        with patch('rocketride._engine.platform._platform.system', return_value='Linux'), patch('rocketride._engine.platform._platform.machine', return_value='amd64'), patch.object(sys, 'platform', 'linux'):
            assert platform.get_platform() == ('linux', 'x64')

    def test_windows(self):
        with patch('rocketride._engine.platform._platform.system', return_value='Windows'), patch.object(sys, 'platform', 'win32'):
            assert platform.get_platform() == ('win', '64')

    def test_unsupported_platform_raises(self):
        with patch('rocketride._engine.platform._platform.system', return_value='Darwin'), patch('rocketride._engine.platform._platform.machine', return_value='x86_64'), patch.object(sys, 'platform', 'darwin'):
            with pytest.raises(UnsupportedPlatformError, match='Unsupported platform'):
                platform.get_platform()

    def test_asset_name_darwin_arm64(self):
        with patch('rocketride._engine.platform.get_platform', return_value=('darwin', 'arm64')):
            assert platform.asset_name('3.2.1') == 'rocketride-server-v3.2.1-darwin-arm64.tar.gz'

    def test_asset_name_linux_x64(self):
        with patch('rocketride._engine.platform.get_platform', return_value=('linux', 'x64')):
            assert platform.asset_name('3.2.1') == 'rocketride-server-v3.2.1-linux-x64.tar.gz'

    def test_asset_name_win64(self):
        with patch('rocketride._engine.platform.get_platform', return_value=('win', '64')):
            assert platform.asset_name('3.2.1') == 'rocketride-server-v3.2.1-win64.zip'

    def test_release_tag(self):
        assert platform.release_tag('3.2.1') == 'server-v3.2.1'


# ── ports ────────────────────────────────────────────────────────


class TestPorts:
    def test_find_available_port_returns_int(self):
        # Use the default base port — any free port in the range is fine
        port = ports.find_available_port()
        assert isinstance(port, int)
        assert port >= 5565

    def test_find_available_port_skips_occupied(self):
        import socket

        # Occupy a port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            occupied_port = s.getsockname()[1]
            # Ask for that port — should get next one
            result = ports.find_available_port(base=occupied_port)
            assert result > occupied_port

    def test_find_available_port_raises_when_exhausted(self):
        with patch('rocketride._engine.ports.socket.socket') as mock_sock:
            instance = MagicMock()
            instance.__enter__ = MagicMock(return_value=instance)
            instance.__exit__ = MagicMock(return_value=False)
            instance.bind.side_effect = OSError('in use')
            mock_sock.return_value = instance
            with pytest.raises(OSError, match='No available port'):
                ports.find_available_port(base=5565)


# ── state ────────────────────────────────────────────────────────


class TestStateDB:
    @pytest.fixture
    def tmp_home(self, tmp_path):
        """Redirect ~/.rocketride to a temp directory."""
        home = tmp_path / '.rocketride'
        with patch('rocketride._engine.state.state_db_path', return_value=home / 'instances' / 'state.db'), patch('rocketride._engine.state.ensure_dirs', side_effect=lambda: (home / 'instances').mkdir(parents=True, exist_ok=True)):
            yield home

    @pytest.mark.asyncio
    async def test_register_and_get(self, tmp_home):
        async with StateDB() as db:
            await db.register('test-1', 12345, 5565, '3.1.0', 'user')
            inst = await db.get('test-1')
            assert inst is not None
            assert inst['pid'] == 12345
            assert inst['port'] == 5565
            assert inst['version'] == '3.1.0'
            assert inst['owner'] == 'user'

    @pytest.mark.asyncio
    async def test_unregister(self, tmp_home):
        async with StateDB() as db:
            await db.register('test-2', 99999, 5566, '3.1.0', 'sdk')
            await db.unregister('test-2')
            assert await db.get('test-2') is None

    @pytest.mark.asyncio
    async def test_get_all(self, tmp_home):
        async with StateDB() as db:
            await db.register('a', 1, 5565, '3.0.0', 'user')
            await db.register('b', 2, 5566, '3.1.0', 'sdk')
            all_inst = await db.get_all()
            assert len(all_inst) == 2
            ids = {inst['id'] for inst in all_inst}
            assert ids == {'a', 'b'}

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self, tmp_home):
        async with StateDB() as db:
            assert await db.get('nonexistent') is None

    @pytest.mark.asyncio
    async def test_find_running_returns_alive_instance(self, tmp_home):
        async with StateDB() as db:
            await db.register('live', os.getpid(), 5565, '3.1.0', 'user')
            result = await db.find_running()
            assert result is not None
            assert result['id'] == 'live'

    @pytest.mark.asyncio
    async def test_find_running_cleans_stale(self, tmp_home):
        async with StateDB() as db:
            # Register with a PID that definitely doesn't exist
            await db.register('dead', 999999999, 5565, '3.1.0', 'user')
            result = await db.find_running()
            assert result is None
            # Stale entry should have been removed
            assert await db.get('dead') is None

    @pytest.mark.asyncio
    async def test_register_replaces_existing(self, tmp_home):
        async with StateDB() as db:
            await db.register('x', 1, 5565, '3.0.0', 'user')
            await db.register('x', 2, 5566, '3.1.0', 'sdk')
            inst = await db.get('x')
            assert inst['pid'] == 2
            assert inst['port'] == 5566


class TestIsPidAlive:
    def test_current_process_is_alive(self):
        assert _is_pid_alive(os.getpid()) is True

    def test_nonexistent_pid_is_not_alive(self):
        assert _is_pid_alive(999999999) is False


# ── resolver ─────────────────────────────────────────────────────


class TestResolver:
    def test_get_compat_range_reads_pyproject(self):
        result = get_compat_range()
        # Should match what's in pyproject.toml
        assert '>=3.0.0' in result
        assert '<4.0.0' in result

    @pytest.mark.asyncio
    async def test_resolve_compatible_version_picks_latest(self):
        mock_releases = [
            {'tag_name': 'server-v3.2.0'},
            {'tag_name': 'server-v3.1.0'},
            {'tag_name': 'server-v3.0.5'},
            {'tag_name': 'server-v4.0.0'},  # Outside range
            {'tag_name': 'unrelated-tag'},
        ]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_releases)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch('rocketride._engine.resolver.aiohttp.ClientSession', return_value=mock_session):
            version = await resolve_compatible_version('>=3.0.0,<4.0.0')
            assert version == '3.2.0'

    @pytest.mark.asyncio
    async def test_resolve_compatible_version_raises_on_no_match(self):
        mock_releases = [
            {'tag_name': 'server-v4.0.0'},
            {'tag_name': 'server-v5.0.0'},
        ]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_releases)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch('rocketride._engine.resolver.aiohttp.ClientSession', return_value=mock_session):
            with pytest.raises(EngineNotFoundError, match='No engine release found'):
                await resolve_compatible_version('>=3.0.0,<4.0.0')

    @pytest.mark.asyncio
    async def test_resolve_compatible_version_raises_on_http_error(self):
        mock_resp = AsyncMock()
        mock_resp.status = 403
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch('rocketride._engine.resolver.aiohttp.ClientSession', return_value=mock_session):
            with pytest.raises(EngineNotFoundError, match='HTTP 403'):
                await resolve_compatible_version('>=3.0.0,<4.0.0')


# ── downloader ───────────────────────────────────────────────────


class TestDownloader:
    @pytest.mark.asyncio
    async def test_download_skips_if_binary_exists(self, tmp_path):
        binary = tmp_path / 'rocketride-engine'
        binary.write_text('fake')

        with patch('rocketride._engine.downloader.engine_binary', return_value=binary):
            result = await download_engine('3.1.0')
            assert result == binary

    @pytest.mark.asyncio
    async def test_download_raises_on_http_error(self, tmp_path):
        binary = tmp_path / 'rocketride-engine'

        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch('rocketride._engine.downloader.engine_binary', return_value=binary),
            patch('rocketride._engine.downloader.ensure_dirs'),
            patch('rocketride._engine.downloader.asset_name', return_value='test.tar.gz'),
            patch('rocketride._engine.downloader.release_tag', return_value='server-v3.1.0'),
            patch('rocketride._engine.downloader.aiohttp.ClientSession', return_value=mock_session),
        ):
            with pytest.raises(EngineNotFoundError, match='HTTP 404'):
                await download_engine('3.1.0')


# ── process ──────────────────────────────────────────────────────


class TestWaitHealthy:
    @pytest.mark.asyncio
    async def test_wait_healthy_returns_on_success(self):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch('rocketride._engine.process.aiohttp.ClientSession', return_value=mock_session):
            await wait_healthy(5565, timeout=2.0)

    @pytest.mark.asyncio
    async def test_wait_healthy_raises_on_timeout(self):
        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=OSError('connection refused'))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch('rocketride._engine.process.aiohttp.ClientSession', return_value=mock_session):
            with pytest.raises(EngineError, match='did not become healthy'):
                await wait_healthy(5565, timeout=1.0)


# ── manager ──────────────────────────────────────────────────────


class TestEngineManager:
    def test_initial_state(self):
        mgr = EngineManager()
        assert mgr.we_started is False
        assert mgr.uri is None

    def test_uri_property(self):
        mgr = EngineManager()
        mgr._port = 5565
        assert mgr.uri == 'http://127.0.0.1:5565'

    @pytest.mark.asyncio
    async def test_ensure_running_reuses_existing(self, tmp_path):
        """When state.db has a live instance, reuse it without spawning."""
        existing = {
            'id': 'existing-1',
            'pid': os.getpid(),
            'port': 5565,
            'version': '3.1.0',
            'started_at': '2026-01-01T00:00:00',
            'owner': 'user',
        }

        mock_db = AsyncMock(spec=StateDB)
        mock_db.find_running = AsyncMock(return_value=existing)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mgr = EngineManager()

        with patch('rocketride._engine.manager.StateDB', return_value=mock_db):
            uri, we_started = await mgr.ensure_running()

        assert we_started is False
        assert uri == 'http://127.0.0.1:5565'
        assert mgr._instance_id == 'existing-1'

    @pytest.mark.asyncio
    async def test_ensure_running_spawns_when_no_existing(self, tmp_path):
        """When no live instance, spawn a new one."""
        mock_db = AsyncMock(spec=StateDB)
        mock_db.find_running = AsyncMock(return_value=None)
        mock_db.register = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        fake_binary = tmp_path / 'rocketride-engine'
        fake_binary.write_text('fake')

        mgr = EngineManager()

        async def fake_resolve_binary():
            mgr._version = '3.1.0'
            return fake_binary

        with (
            patch('rocketride._engine.manager.StateDB', return_value=mock_db),
            patch.object(mgr, '_resolve_binary', side_effect=fake_resolve_binary),
            patch('rocketride._engine.manager.find_available_port', return_value=5570),
            patch('rocketride._engine.manager.spawn_engine', new_callable=AsyncMock, return_value=12345),
            patch('rocketride._engine.manager.wait_healthy', new_callable=AsyncMock),
            patch.object(mgr, '_register_signal_handlers'),
        ):
            uri, we_started = await mgr.ensure_running()

        assert we_started is True
        assert uri == 'http://127.0.0.1:5570'
        mock_db.register.assert_called_once()

    @pytest.mark.asyncio
    async def test_teardown_when_we_started(self, tmp_path):
        """Teardown stops the engine and unregisters when we started it."""
        inst_data = {
            'id': 'spawned-1',
            'pid': 99999,
            'port': 5570,
            'version': '3.1.0',
            'started_at': '2026-01-01T00:00:00',
            'owner': 'sdk',
        }

        mock_db = AsyncMock(spec=StateDB)
        mock_db.get = AsyncMock(return_value=inst_data)
        mock_db.unregister = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mgr = EngineManager()
        mgr._we_started = True
        mgr._instance_id = 'spawned-1'
        mgr._port = 5570

        with patch('rocketride._engine.manager.StateDB', return_value=mock_db), patch('rocketride._engine.manager.stop_engine', new_callable=AsyncMock) as mock_stop, patch.object(mgr, '_restore_signal_handlers'):
            await mgr.teardown()

        mock_stop.assert_called_once_with(99999)
        mock_db.unregister.assert_called_once_with('spawned-1')
        assert mgr._we_started is False
        assert mgr._instance_id is None

    @pytest.mark.asyncio
    async def test_teardown_noop_when_not_started(self):
        """Teardown does nothing when we didn't start the engine."""
        mgr = EngineManager()
        mgr._we_started = False
        # Should return immediately without any side effects
        await mgr.teardown()

    @pytest.mark.asyncio
    async def test_resolve_binary_finds_installed(self, tmp_path):
        """_resolve_binary picks the latest installed compatible version."""
        engines_root = tmp_path / 'engines'
        (engines_root / '3.0.0').mkdir(parents=True)
        (engines_root / '3.2.0').mkdir(parents=True)
        (engines_root / '3.1.0').mkdir(parents=True)

        # Create fake binaries
        for v in ['3.0.0', '3.1.0', '3.2.0']:
            (engines_root / v / 'rocketride-engine').write_text('fake')

        mgr = EngineManager()

        with patch('rocketride._engine.manager.get_compat_range', return_value='>=3.0.0,<4.0.0'), patch('rocketride._engine.manager.rocketride_home', return_value=tmp_path), patch('rocketride._engine.manager.engine_binary', side_effect=lambda v: engines_root / v / 'rocketride-engine'):
            result = await mgr._resolve_binary()

        assert '3.2.0' in str(result)
        assert mgr._version == '3.2.0'


# ── client auto-spawn integration ───────────────────────────────


class TestClientAutoSpawn:
    def test_engine_manager_created_when_no_uri(self):
        """Client creates EngineManager when no uri and no ROCKETRIDE_URI."""
        with patch.dict(os.environ, {}, clear=True), patch('rocketride.client.os.path.exists', return_value=False):
            from rocketride.client import RocketRideClient

            client = RocketRideClient(env={})
            assert client._engine_manager is not None
            assert client._uri == ''

    def test_no_engine_manager_when_uri_provided(self):
        """Client does not create EngineManager when uri is explicitly given."""
        with patch.dict(os.environ, {}, clear=True):
            from rocketride.client import RocketRideClient

            client = RocketRideClient(uri='http://localhost:5565', env={})
            assert client._engine_manager is None

    def test_no_engine_manager_when_env_uri_set(self):
        """Client does not create EngineManager when ROCKETRIDE_URI is in env."""
        with patch.dict(os.environ, {}, clear=True):
            from rocketride.client import RocketRideClient

            client = RocketRideClient(env={'ROCKETRIDE_URI': 'http://localhost:5565'})
            assert client._engine_manager is None


# ── exceptions ───────────────────────────────────────────────────


class TestExceptions:
    def test_unsupported_platform_error_is_exception(self):
        assert issubclass(UnsupportedPlatformError, Exception)
        assert not issubclass(UnsupportedPlatformError, EngineError)

    def test_engine_error_hierarchy(self):
        assert issubclass(EngineNotFoundError, EngineError)
        assert issubclass(EngineError, Exception)

    def test_exception_messages(self):
        e = UnsupportedPlatformError('test message')
        assert str(e) == 'test message'

        e = EngineNotFoundError('not found')
        assert str(e) == 'not found'


pytest_plugins = ['pytest_asyncio']
