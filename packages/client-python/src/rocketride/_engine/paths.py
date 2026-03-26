"""
Filesystem paths for the ~/.rocketride directory structure.

Layout:
    ~/.rocketride/
        engines/{version}/rocketride-engine(.exe)
        instances/state.db
        logs/{id}/stdout.log, stderr.log
"""

import sys
from pathlib import Path


def rocketride_home() -> Path:
    return Path.home() / '.rocketride'


def engines_dir(version: str) -> Path:
    return rocketride_home() / 'engines' / version


def engine_binary(version: str) -> Path:
    name = 'rocketride-engine.exe' if sys.platform == 'win32' else 'rocketride-engine'
    return engines_dir(version) / name


def state_db_path() -> Path:
    return rocketride_home() / 'instances' / 'state.db'


def logs_dir(instance_id: str) -> Path:
    return rocketride_home() / 'logs' / instance_id


def ensure_dirs() -> None:
    """Create the ~/.rocketride directory tree if it doesn't exist."""
    rocketride_home().mkdir(parents=True, exist_ok=True)
    (rocketride_home() / 'engines').mkdir(exist_ok=True)
    (rocketride_home() / 'instances').mkdir(exist_ok=True)
    (rocketride_home() / 'logs').mkdir(exist_ok=True)
