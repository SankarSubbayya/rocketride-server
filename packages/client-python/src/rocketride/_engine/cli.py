"""
CLI commands for engine management.

Registered as ``rocketride engine <command>`` (alias ``rocketride e <command>``).

Commands:
    list                    List tracked engine instances
    start [id]              Start an engine instance
    stop [id]               Stop a running engine instance
    install [version]       Download an engine binary
    delete [id]             Stop and deregister an engine instance (--purge to remove binary)
    logs [id]               Tail engine log output
    reconcile               Restart engines that should be running but crashed
    autostart               Manage OS startup hook for engine reconcile
    run <pipeline> [--engine id]  Run a pipeline with auto-managed engine
"""

import asyncio
import os
import shutil
import subprocess
import sys

from .downloader import download_engine
from .paths import engines_dir, engine_binary, logs_dir, rocketride_home
from .ports import find_available_port
from .process import spawn_engine, stop_engine, wait_healthy
from .resolver import get_compat_range, resolve_compatible_version
from .state import StateDB


def register_engine_commands(subparsers) -> None:
    """Register the ``engine`` (and ``e``) subcommand group."""

    def _add_engine_subparser(name, **kwargs):
        parser = subparsers.add_parser(name, **kwargs)
        engine_subs = parser.add_subparsers(dest='engine_command', metavar='COMMAND')
        _add_engine_subcommands(engine_subs)
        return parser

    _add_engine_subparser('engine', help='Manage engine instances')
    _add_engine_subparser('e', help='Manage engine instances (shorthand)')


def _add_engine_subcommands(subparsers) -> None:
    """Add all engine subcommands to the given subparser group."""
    # list
    subparsers.add_parser('list', help='List tracked engine instances')

    # start
    start_p = subparsers.add_parser('start', help='Start an engine instance')
    start_p.add_argument('id', nargs='?', default=None, help='Instance id (auto-generated if omitted)')
    start_p.add_argument('--port', type=int, default=None, help='Explicit port (default: auto)')
    start_p.add_argument('--version', default=None, help='Engine version to use')

    # stop
    stop_p = subparsers.add_parser('stop', help='Stop a running engine instance')
    stop_p.add_argument('id', help='Instance id to stop')

    # install
    install_p = subparsers.add_parser('install', help='Download an engine binary')
    install_p.add_argument('version', nargs='?', default=None, help='Version to install (default: latest compatible)')
    install_p.add_argument('--force', action='store_true', help='Skip compatibility check and install any available version')

    # delete
    delete_p = subparsers.add_parser('delete', help='Stop and remove an engine instance')
    delete_p.add_argument('id', help='Instance id to delete')
    delete_p.add_argument('--purge', action='store_true', help='Also remove the engine binary from disk')

    # logs
    logs_p = subparsers.add_parser('logs', help='Tail engine log output')
    logs_p.add_argument('id', help='Instance id')

    # reconcile
    subparsers.add_parser('reconcile', help='Restart engines that should be running but crashed')

    # autostart
    autostart_p = subparsers.add_parser('autostart', help='Manage OS startup hook for engine reconcile')
    autostart_group = autostart_p.add_mutually_exclusive_group()
    autostart_group.add_argument('--enable', action='store_true', help='Register OS startup hook')
    autostart_group.add_argument('--disable', action='store_true', help='Remove OS startup hook')
    autostart_group.add_argument('--status', action='store_true', default=True, help='Show whether autostart is enabled')

    # run
    run_p = subparsers.add_parser('run', help='Run a pipeline with auto-managed engine')
    run_p.add_argument('pipeline', help='Path to pipeline JSON file')
    run_p.add_argument('--engine', dest='engine_id', default=None, help='Use a specific engine instance')
    run_p.add_argument('--apikey', default=os.getenv('ROCKETRIDE_APIKEY'), help='API key for authentication')


async def handle_engine_command(args) -> int:
    """Dispatch to the appropriate engine subcommand handler."""
    cmd = getattr(args, 'engine_command', None)
    if not cmd:
        print('Usage: rocketride engine <command>')
        print('Commands: list, start, stop, install, delete, logs, reconcile, autostart, run')
        return 1

    handlers = {
        'list': _cmd_list,
        'start': _cmd_start,
        'stop': _cmd_stop,
        'install': _cmd_install,
        'delete': _cmd_delete,
        'logs': _cmd_logs,
        'reconcile': _cmd_reconcile,
        'autostart': _cmd_autostart,
        'run': _cmd_run,
    }

    handler = handlers.get(cmd)
    if not handler:
        print(f'Unknown engine command: {cmd}')
        return 1

    result = await handler(args)

    # Show the table after every command except logs and list
    if cmd not in ('logs', 'list'):
        print()
        await _cmd_list(args)

    return result


# ── Command handlers ──────────────────────────────────────────────


async def _cmd_list(args) -> int:
    async with StateDB() as db:
        instances = await db.get_all()

    from .state import _get_process_memory, _is_pid_alive

    # ── TTY detection ─────────────────────────────────────────────
    import sys

    use_color = sys.stdout.isatty()

    # ── Brand palette (ANSI 24-bit) ──────────────────────────────
    if use_color:
        HORIZON = '\033[38;2;65;182;230m'  # #41b6e6 — headers
        AMETHYST = '\033[38;2;95;33;103m'  # #5f2167 — title accent
        GREEN = '\033[38;2;80;220;100m'  # status: running
        RED = '\033[38;2;220;80;80m'  # status: stopped
        DIM = '\033[2m'
        BOLD = '\033[1m'
        RESET = '\033[0m'
    else:
        HORIZON = AMETHYST = GREEN = RED = DIM = BOLD = RESET = ''

    # ── Column definitions (minimum widths) ────────────────────────
    col_names = ['version', 'id', 'pid', 'port', 'owner', 'status', 'restarted', 'uptime', 'memory']
    min_widths = {
        'version': 7,
        'id': 2,
        'pid': 3,
        'port': 4,
        'owner': 5,
        'status': 6,
        'restarted': 9,
        'uptime': 6,
        'memory': 6,
    }

    # Build rows
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    rows = []
    for inst in instances:
        pid = inst['pid']
        alive = _is_pid_alive(pid)
        status_text = 'online' if alive else 'stopped'
        status_color = GREEN if alive else RED

        # Calculate uptime
        if alive:
            try:
                started = datetime.fromisoformat(inst['started_at'])
                delta = now - started
                total_secs = int(delta.total_seconds())
                if total_secs < 60:
                    uptime = f'{total_secs}s'
                elif total_secs < 3600:
                    uptime = f'{total_secs // 60}m'
                elif total_secs < 86400:
                    uptime = f'{total_secs // 3600}h {(total_secs % 3600) // 60}m'
                else:
                    uptime = f'{total_secs // 86400}d {(total_secs % 86400) // 3600}h'
            except Exception:
                uptime = '-'
        else:
            uptime = '0s'

        # Memory usage
        mem_bytes = _get_process_memory(pid) if alive else None
        if mem_bytes is not None:
            if mem_bytes < 1024 * 1024:
                memory = f'{mem_bytes / 1024:.0f} KB'
            elif mem_bytes < 1024 * 1024 * 1024:
                memory = f'{mem_bytes / (1024 * 1024):.1f} MB'
            else:
                memory = f'{mem_bytes / (1024 * 1024 * 1024):.1f} GB'
        else:
            memory = '-'

        restart_count = inst.get('restart_count', 0)

        rows.append(
            {
                'version': inst['version'],
                'id': inst['id'],
                'pid': str(pid) if pid else '-',
                'port': str(inst['port']) if inst['port'] else '-',
                'owner': inst['owner'],
                'status': (status_color, status_text),
                'restarted': str(restart_count),
                'uptime': uptime,
                'memory': memory,
            }
        )

    # ── Compute dynamic column widths ──────────────────────────────
    col_widths = {name: max(min_widths[name], len(name)) for name in col_names}
    for row in rows:
        for name in col_names:
            val = row[name]
            text = val[1] if isinstance(val, tuple) else val
            col_widths[name] = max(col_widths[name], len(text))

    cols = [(name, col_widths[name]) for name in col_names]

    # ── Render table ─────────────────────────────────────────────
    if use_color:
        GRAY = '\033[38;2;100;100;100m'  # border color
        B = GRAY

        # Build horizontal rules
        def h_rule(left, mid, right, fill='─'):
            segments = [fill * (w + 2) for _, w in cols]
            return f'{B}{left}{mid.join(segments)}{right}{RESET}'

        top = h_rule('┌', '┬', '┐')
        sep = h_rule('├', '┼', '┤')
        bot = h_rule('└', '┴', '┘')

        # Header row
        header_cells = []
        for name, w in cols:
            header_cells.append(f' {BOLD}{HORIZON}{name.upper():<{w}}{RESET} ')
        header = f'{B}│{RESET}{f"{B}│{RESET}".join(header_cells)}{B}│{RESET}'

        # Title
        print(f'{AMETHYST}{BOLD} RocketRide{RESET} {DIM}Engine instances{RESET}')
        print(top)
        print(header)

        if not instances:
            print(bot)
            return 0

        print(sep)

        # Data rows
        for row in rows:
            cells = []
            for name, w in cols:
                val = row[name]
                if isinstance(val, tuple):
                    color, text = val
                    cells.append(f' {color}{text:<{w}}{RESET} ')
                else:
                    cells.append(f' {val:<{w}} ')
            print(f'{B}│{RESET}{f"{B}│{RESET}".join(cells)}{B}│{RESET}')

        print(bot)
    else:
        # Plain text table for non-TTY (piped) output
        header_parts = [f'{name.upper():<{w}}' for name, w in cols]
        print('  '.join(header_parts))

        if not instances:
            return 0

        for row in rows:
            parts = []
            for name, w in cols:
                val = row[name]
                text = val[1] if isinstance(val, tuple) else val
                parts.append(f'{text:<{w}}')
            print('  '.join(parts))

    return 0


async def _cmd_start(args) -> int:
    instance_id = getattr(args, 'id', None)
    from .platform import normalize_version

    version = getattr(args, 'version', None)
    explicit_port = getattr(args, 'port', None)

    if version:
        version = normalize_version(version)

    async with StateDB() as db:
        if not instance_id and version:
            existing = await db.find_by_version(version)
            if existing:
                instance_id = existing['id']
            else:
                print(f'No instance found for version {version}.')
                print('Use "rocketride engine install" to create one first.')
                return 1
        elif not instance_id:
            instance_id = await db.next_id()

        # If an ID was explicitly provided, it must already exist in the DB
        existing = None
        if instance_id:
            existing = await db.get(instance_id)
            if not existing:
                print(f'No instance found with id: {instance_id}')
                print('Use "rocketride engine install" to create a new instance first.')
                return 1
            if not version:
                version = existing['version']

        # Resolve version if still unknown (new instance, no --version)
        if not version:
            compat = get_compat_range()
            from packaging.specifiers import SpecifierSet
            from packaging.version import Version

            spec = SpecifierSet(compat)
            engines_root = rocketride_home() / 'engines'
            installed = []
            if engines_root.exists():
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
                installed.sort(key=lambda x: x[0], reverse=True)
                version = installed[0][1]
                print(f'Using installed engine v{version}')
            else:
                print('No compatible engine installed. Downloading...')
                version = await resolve_compatible_version(compat)
                await download_engine(version)
                print(f'Downloaded engine v{version}')

        binary = engine_binary(version)
        if not binary.exists():
            print(f'Engine binary not found for v{version}. Run: rocketride engine install {version}')
            return 1

        port = explicit_port or find_available_port()

        # Track restarts: first start = 0, subsequent starts increment.
        # Log files from a previous run prove the instance ran before.
        restart_count = 0
        if existing:
            prev_count = existing.get('restart_count', 0)
            has_run_before = prev_count > 0 or (logs_dir(instance_id) / 'stdout.log').exists()
            restart_count = prev_count + 1 if has_run_before else 0

        print(f'Starting engine v{version} on port {port} (id: {instance_id})...')
        pid = await spawn_engine(binary, port, instance_id)

    try:
        log_file = logs_dir(instance_id) / 'stderr.log'
        await wait_healthy(port, pid=pid, log_file=log_file)
        # Only persist pid/port once the engine is confirmed healthy
        async with StateDB() as db:
            await db.register(instance_id, pid, port, version, 'cli', restart_count=restart_count, desired_state='running')
        return 0
    except Exception as e:
        print(f'\nEngine started but health check failed: {e}')
        # Kill the orphaned process so it doesn't escape tracking
        await stop_engine(pid)
        # Reset to stopped state — don't leave stale pid/port in DB
        async with StateDB() as db:
            await db.register(instance_id, 0, 0, version, 'cli', restart_count=restart_count)
        return 1


async def _cmd_stop(args) -> int:
    instance_id = args.id

    async with StateDB() as db:
        inst = await db.get(instance_id)
        if not inst:
            print(f'No instance found with id: {instance_id}')
            return 1

        print(f'Stopping engine {instance_id} (PID: {inst["pid"]})...')
        await stop_engine(inst['pid'])
        await db.register(
            instance_id,
            0,
            0,
            inst['version'],
            inst['owner'],
            restart_count=inst.get('restart_count', 0),
            desired_state='stopped',
        )

    print('Stopped.')
    return 0


async def _cmd_install(args) -> int:
    from .platform import normalize_version

    version = getattr(args, 'version', None)
    force = getattr(args, 'force', False)

    if version:
        version = normalize_version(version)

        # Check if already installed
        binary = engine_binary(version)
        async with StateDB() as db:
            existing = await db.find_by_version(version)
        if binary.exists() and existing:
            print(f'Engine v{version} is already installed (id: {existing["id"]})')
            return 0

        # Validate against the compatibility range (unless --force)
        if not force:
            from .platform import _base_version

            compat = get_compat_range()
            try:
                from packaging.specifiers import SpecifierSet
                from packaging.version import Version

                base = _base_version(version)
                if Version(base) not in SpecifierSet(compat):
                    print(f'Engine v{version} is not compatible with this SDK (requires {compat})')
                    print('Use --force to install anyway.')
                    return 1
            except Exception:
                pass  # Non-PEP 440 versions (e.g. prereleases) skip validation
    else:
        print('Resolving latest compatible version...')
        compat = get_compat_range()
        version = await resolve_compatible_version(compat)

    binary = engine_binary(version)
    if not binary.exists():
        print(f'Downloading engine v{version}...')
        await download_engine(version)

    # Register in state DB so it shows up in `list`
    async with StateDB() as db:
        existing = await db.find_by_version(version)
        instance_id = existing['id'] if existing else await db.next_id()
        await db.register(instance_id, 0, 0, version, 'cli')

    print(f'Installed engine v{version} (id: {instance_id})')
    return 0


async def _cmd_delete(args) -> int:
    instance_id = args.id

    async with StateDB() as db:
        inst = await db.get(instance_id)
        if not inst:
            print(f'No instance found with id: {instance_id}')
            return 1

    from .state import _is_pid_alive

    pid = inst['pid']
    version = inst['version']

    # 1. Stop the process if running
    if pid and _is_pid_alive(pid):
        print(f'Stopping running engine {instance_id}...')
        await stop_engine(pid)

    # 2. Check if any OTHER instance is running from the same version's binary
    async with StateDB() as db:
        all_instances = await db.get_all()
    version_in_use = False
    for other in all_instances:
        if other['id'] == instance_id:
            continue
        if other['version'] == version and other['pid'] and _is_pid_alive(other['pid']):
            version_in_use = True
            break

    # 3. Remove the binary directory (only when --purge is set)
    purge = getattr(args, 'purge', False)
    version_dir = engines_dir(version)
    if purge:
        if version_in_use:
            print(f'Keeping engine v{version} binary (still in use by another instance).')
        elif version_dir.exists():
            for attempt in range(5):
                try:
                    shutil.rmtree(str(version_dir))
                    print(f'Removed engine v{version} from {version_dir}')
                    break
                except PermissionError:
                    if attempt < 4:
                        await asyncio.sleep(1)
                    else:
                        print(f'Could not remove {version_dir} — files may still be locked.')
                        print('The instance record has NOT been removed. Try again shortly.')
                        return 1

    # 4. Remove logs
    log_dir = logs_dir(instance_id)
    if log_dir.exists():
        shutil.rmtree(str(log_dir))

    # 5. Only remove the DB row after everything else succeeded
    async with StateDB() as db:
        await db.unregister(instance_id)

    print(f'Deleted instance {instance_id}.')
    return 0


async def _cmd_logs(args) -> int:
    instance_id = args.id
    log_file = logs_dir(instance_id) / 'stdout.log'

    if not log_file.exists():
        print(f'No log file found for instance {instance_id}')
        return 1

    print(f'Tailing {log_file} (Ctrl+C to stop)\n')

    # On Windows, asyncio.sleep doesn't get interrupted by Ctrl+C.
    # Use a threading.Event to bridge the signal into the async loop.
    import signal
    import threading

    stop = threading.Event()

    original_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    try:
        with open(log_file, 'r') as f:
            # Print existing content
            content = f.read()
            if content:
                sys.stdout.write(content)
                sys.stdout.flush()

            # Tail new content
            while not stop.is_set():
                line = f.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    stop.wait(0.2)
    finally:
        signal.signal(signal.SIGINT, original_handler)

    print()
    return 0


async def _cmd_reconcile(args) -> int:
    from .state import _is_pid_alive

    restarted = []
    skipped = []
    failed = []

    async with StateDB() as db:
        desired = await db.find_desired_running()

    for inst in desired:
        if _is_pid_alive(inst['pid']):
            skipped.append(inst)
            continue

        # Re-spawn with the same version
        version = inst['version']
        instance_id = inst['id']
        binary = engine_binary(version)

        if not binary.exists():
            print(f'  [{instance_id}] Binary missing for v{version}, skipping')
            failed.append(inst)
            continue

        port = find_available_port()
        restart_count = inst.get('restart_count', 0) + 1

        print(f'  [{instance_id}] Restarting v{version} on port {port}...')
        pid = await spawn_engine(binary, port, instance_id)

        try:
            log_file = logs_dir(instance_id) / 'stderr.log'
            await wait_healthy(port, pid=pid, log_file=log_file)
            async with StateDB() as db:
                await db.register(instance_id, pid, port, version, 'cli', restart_count=restart_count, desired_state='running')
            restarted.append(instance_id)
        except Exception as e:
            print(f'  [{instance_id}] Health check failed: {e}')
            await stop_engine(pid)
            failed.append(inst)

    # Summary
    if not desired:
        print('No engines marked for auto-restart.')
    else:
        if restarted:
            print(f'Restarted: {", ".join(restarted)}')
        if skipped:
            print(f'Already running: {", ".join(i["id"] for i in skipped)}')
        if failed:
            print(f'Failed: {", ".join(i["id"] for i in failed)}')

    return 1 if failed else 0


# ── Autostart helpers ─────────────────────────────────────────────


def _autostart_task_name():
    return 'RocketRideEngineReconcile'


def _autostart_launchagent_path():
    from pathlib import Path

    return Path.home() / 'Library' / 'LaunchAgents' / 'com.rocketride.engine.plist'


def _autostart_systemd_path():
    from pathlib import Path

    return Path.home() / '.config' / 'systemd' / 'user' / 'rocketride-engine.service'


def _find_rocketride_executable():
    """Return the path to the rocketride CLI executable."""
    exe = shutil.which('rocketride')
    if exe:
        return exe
    # Fallback: use sys.executable with -m
    return f'{sys.executable} -m rocketride'


async def _cmd_autostart(args) -> int:
    enable = getattr(args, 'enable', False)
    disable = getattr(args, 'disable', False)

    if enable:
        return await _autostart_enable()
    elif disable:
        return await _autostart_disable()
    else:
        return await _autostart_status()


async def _autostart_enable() -> int:
    exe = _find_rocketride_executable()

    if sys.platform == 'win32':
        task_name = _autostart_task_name()
        cmd = f'{exe} engine reconcile'
        result = subprocess.run(
            ['schtasks', '/Create', '/SC', 'ONLOGON', '/TN', task_name, '/TR', cmd, '/F'],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f'Failed to create scheduled task: {result.stderr.strip()}')
            return 1
        print(f'Autostart enabled (Task Scheduler: {task_name})')

    elif sys.platform == 'darwin':
        plist_path = _autostart_launchagent_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)

        # Determine command parts for the plist
        if ' -m ' in exe:
            parts = exe.split(' ', 2)  # [python, -m, rocketride]
            program_args = f"""    <array>
        <string>{parts[0]}</string>
        <string>{parts[1]}</string>
        <string>{parts[2]}</string>
        <string>engine</string>
        <string>reconcile</string>
    </array>"""
        else:
            program_args = f"""    <array>
        <string>{exe}</string>
        <string>engine</string>
        <string>reconcile</string>
    </array>"""

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.rocketride.engine</string>
    <key>ProgramArguments</key>
{program_args}
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""
        plist_path.write_text(plist_content)
        print(f'Autostart enabled (LaunchAgent: {plist_path})')

    else:
        # Linux — systemd user unit
        unit_path = _autostart_systemd_path()
        unit_path.parent.mkdir(parents=True, exist_ok=True)

        unit_content = f"""[Unit]
Description=RocketRide Engine Reconcile
After=network.target

[Service]
Type=oneshot
ExecStart={exe} engine reconcile

[Install]
WantedBy=default.target
"""
        unit_path.write_text(unit_content)

        subprocess.run(['systemctl', '--user', 'daemon-reload'], capture_output=True)
        subprocess.run(['systemctl', '--user', 'enable', 'rocketride-engine.service'], capture_output=True)
        print(f'Autostart enabled (systemd user unit: {unit_path})')

    return 0


async def _autostart_disable() -> int:
    if sys.platform == 'win32':
        task_name = _autostart_task_name()
        result = subprocess.run(
            ['schtasks', '/Delete', '/TN', task_name, '/F'],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f'No autostart task found (or failed to remove): {result.stderr.strip()}')
            return 1
        print('Autostart disabled.')

    elif sys.platform == 'darwin':
        plist_path = _autostart_launchagent_path()
        if plist_path.exists():
            plist_path.unlink()
            print('Autostart disabled.')
        else:
            print('Autostart is not enabled.')

    else:
        unit_path = _autostart_systemd_path()
        if unit_path.exists():
            subprocess.run(['systemctl', '--user', 'disable', 'rocketride-engine.service'], capture_output=True)
            unit_path.unlink()
            subprocess.run(['systemctl', '--user', 'daemon-reload'], capture_output=True)
            print('Autostart disabled.')
        else:
            print('Autostart is not enabled.')

    return 0


async def _autostart_status() -> int:
    enabled = False

    if sys.platform == 'win32':
        task_name = _autostart_task_name()
        result = subprocess.run(
            ['schtasks', '/Query', '/TN', task_name],
            capture_output=True,
            text=True,
        )
        enabled = result.returncode == 0

    elif sys.platform == 'darwin':
        enabled = _autostart_launchagent_path().exists()

    else:
        enabled = _autostart_systemd_path().exists()

    print(f'Autostart: {"enabled" if enabled else "disabled"}')
    return 0


async def _cmd_run(args) -> int:
    pipeline_path = args.pipeline
    engine_id = getattr(args, 'engine_id', None)
    apikey = getattr(args, 'apikey', None)

    if not os.path.exists(pipeline_path):
        print(f'Pipeline file not found: {pipeline_path}')
        return 1

    if not apikey:
        print('Error: API key required. Use --apikey or set ROCKETRIDE_APIKEY')
        return 1

    from .manager import EngineManager

    manager = EngineManager()
    we_started = False

    try:
        if engine_id:
            # Use a specific existing instance
            async with StateDB() as db:
                inst = await db.get(engine_id)
                if not inst:
                    print(f'No instance found with id: {engine_id}')
                    return 1
                uri = f'http://127.0.0.1:{inst["port"]}'
        else:
            # Auto-manage engine
            uri, we_started = await manager.ensure_running()
            print(f'Engine available at {uri}')

        # Execute the pipeline
        from ..client import RocketRideClient

        async with RocketRideClient(uri=uri, auth=apikey) as client:
            result = await client.use(filepath=pipeline_path)
            token = result.get('token')
            if token:
                print(f'Pipeline started. Token: {token}')
                # Wait for completion by polling status
                while True:
                    status = await client.get_task_status(token)
                    state = status.get('state', '')
                    if state in ('completed', 'failed', 'stopped'):
                        print(f'Pipeline {state}.')
                        return 0 if state == 'completed' else 1
                    await asyncio.sleep(1)
            else:
                print('Failed to start pipeline.')
                return 1

    except KeyboardInterrupt:
        print('\nInterrupted.')
        return 1
    finally:
        if we_started:
            await manager.teardown()
