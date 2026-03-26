"""
CLI commands for engine management.

Registered as ``rocketride engine <command>`` (alias ``rocketride e <command>``).

Commands:
    list                    List tracked engine instances
    start [id]              Start an engine instance
    stop [id]               Stop a running engine instance
    install [version]       Download an engine binary
    delete [id]             Stop and remove an engine instance + binary
    logs [id]               Tail engine log output
    run <pipeline> [--engine id]  Run a pipeline with auto-managed engine
"""

import asyncio
import os
import shutil
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

    # delete
    delete_p = subparsers.add_parser('delete', help='Stop and remove an engine instance')
    delete_p.add_argument('id', help='Instance id to delete')

    # logs
    logs_p = subparsers.add_parser('logs', help='Tail engine log output')
    logs_p.add_argument('id', help='Instance id')

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
        print('Commands: list, start, stop, install, delete, logs, run')
        return 1

    handlers = {
        'list': _cmd_list,
        'start': _cmd_start,
        'stop': _cmd_stop,
        'install': _cmd_install,
        'delete': _cmd_delete,
        'logs': _cmd_logs,
        'run': _cmd_run,
    }

    handler = handlers.get(cmd)
    if not handler:
        print(f'Unknown engine command: {cmd}')
        return 1

    return await handler(args)


# ── Command handlers ──────────────────────────────────────────────


async def _cmd_list(args) -> int:
    async with StateDB() as db:
        instances = await db.get_all()

    if not instances:
        print('No engine instances registered.')
        return 0

    from .state import _is_pid_alive

    # ── Brand palette (ANSI 24-bit) ──────────────────────────────
    HORIZON = '\033[38;2;65;182;230m'  # #41b6e6 — borders, headers
    AMETHYST = '\033[38;2;95;33;103m'  # #5f2167 — title accent
    GREEN = '\033[38;2;80;220;100m'  # status: running
    RED = '\033[38;2;220;80;80m'  # status: dead
    DIM = '\033[2m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

    # ── Column definitions ───────────────────────────────────────
    cols = [
        ('id', 14),
        ('pid', 8),
        ('port', 7),
        ('version', 12),
        ('owner', 6),
        ('status', 10),
        ('started', 26),
    ]

    # Build rows
    rows = []
    for inst in instances:
        alive = _is_pid_alive(inst['pid'])
        status_text = 'running' if alive else 'stopped'
        status_color = GREEN if alive else RED
        rows.append(
            {
                'id': inst['id'],
                'pid': str(inst['pid']),
                'port': str(inst['port']),
                'version': inst['version'],
                'owner': inst['owner'],
                'status': (status_color, status_text),
                'started': inst['started_at'],
            }
        )

    # ── Render table ─────────────────────────────────────────────
    B = HORIZON  # border color

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

    return 0


async def _cmd_start(args) -> int:
    import uuid

    instance_id = getattr(args, 'id', None) or uuid.uuid4().hex[:12]
    version = getattr(args, 'version', None)
    explicit_port = getattr(args, 'port', None)

    # Resolve version
    if not version:
        compat = get_compat_range()
        # Check for installed versions first
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
    print(f'Starting engine v{version} on port {port} (id: {instance_id})...')

    pid = await spawn_engine(binary, port, instance_id)

    async with StateDB() as db:
        await db.register(instance_id, pid, port, version, 'user')

    try:
        await wait_healthy(port)
        print(f'Engine running. PID: {pid}, Port: {port}, ID: {instance_id}')
        return 0
    except Exception as e:
        print(f'Engine started but health check failed: {e}')
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
        await db.unregister(instance_id)

    print('Stopped.')
    return 0


async def _cmd_install(args) -> int:
    version = getattr(args, 'version', None)

    if not version:
        print('Resolving latest compatible version...')
        compat = get_compat_range()
        version = await resolve_compatible_version(compat)

    binary = engine_binary(version)
    if binary.exists():
        print(f'Engine v{version} is already installed at {binary}')
        return 0

    print(f'Downloading engine v{version}...')
    path = await download_engine(version)
    print(f'Installed engine v{version} at {path}')
    return 0


async def _cmd_delete(args) -> int:
    instance_id = args.id

    async with StateDB() as db:
        inst = await db.get(instance_id)
        if inst:
            from .state import _is_pid_alive

            if _is_pid_alive(inst['pid']):
                print(f'Stopping running engine {instance_id}...')
                await stop_engine(inst['pid'])
            await db.unregister(instance_id)

            # Remove the binary directory for this version
            version_dir = engines_dir(inst['version'])
            if version_dir.exists():
                shutil.rmtree(str(version_dir))
                print(f'Removed engine v{inst["version"]} from {version_dir}')
        else:
            print(f'No instance found with id: {instance_id}')
            return 1

    # Remove logs
    log_dir = logs_dir(instance_id)
    if log_dir.exists():
        shutil.rmtree(str(log_dir))

    print(f'Deleted instance {instance_id}.')
    return 0


async def _cmd_logs(args) -> int:
    instance_id = args.id
    log_file = logs_dir(instance_id) / 'stdout.log'

    if not log_file.exists():
        print(f'No log file found for instance {instance_id}')
        return 1

    print(f'Tailing {log_file} (Ctrl+C to stop)\n')

    try:
        with open(log_file, 'r') as f:
            # Print existing content
            content = f.read()
            if content:
                sys.stdout.write(content)
                sys.stdout.flush()

            # Tail new content
            while True:
                line = f.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    await asyncio.sleep(0.2)
    except KeyboardInterrupt:
        print('\n')
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
