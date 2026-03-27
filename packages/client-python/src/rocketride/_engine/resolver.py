"""
Version resolution for engine binaries.

Reads the compatibility range from package metadata and queries
GitHub releases to find the latest compatible engine version.
"""

import re
from typing import Optional

import aiohttp
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from ..core.exceptions import EngineNotFoundError

_GITHUB_API = 'https://api.github.com/repos/rocketride-org/rocketride-server/releases'
_TAG_PATTERN = re.compile(r'^server-v(.+)$')


def get_compat_range() -> str:
    """Read the engine-compatible range from package metadata.

    The value is set in pyproject.toml under [tool.rocketride].
    We read it at runtime via importlib.metadata so it works in
    both installed and editable installs.
    """
    # importlib.metadata doesn't expose [tool.*] sections directly.
    # We read pyproject.toml from the package directory instead.
    from pathlib import Path

    # Walk up from this file to find pyproject.toml
    pkg_dir = Path(__file__).resolve().parent.parent.parent.parent
    pyproject = pkg_dir / 'pyproject.toml'

    if pyproject.exists():
        try:
            # Use tomllib (3.11+) or tomli as fallback
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib

            with open(pyproject, 'rb') as f:
                data = tomllib.load(f)

            return data['tool']['rocketride']['engine-compatible']
        except (KeyError, Exception):
            pass

    # Hardcoded fallback matching pyproject.toml
    return '>=3.0.0,<4.0.0'


async def resolve_compatible_version(
    compat_range: Optional[str] = None,
) -> str:
    """Query GitHub releases and return the latest version matching the compat range.

    Raises EngineNotFoundError if no compatible release is found.
    """
    if compat_range is None:
        compat_range = get_compat_range()

    spec = SpecifierSet(compat_range)

    async with aiohttp.ClientSession() as session:
        async with session.get(
            _GITHUB_API,
            headers={'Accept': 'application/vnd.github+json'},
        ) as resp:
            if resp.status != 200:
                raise EngineNotFoundError(f'Failed to query GitHub releases (HTTP {resp.status})')
            releases = await resp.json()

    candidates = []
    for release in releases:
        tag = release.get('tag_name', '')
        match = _TAG_PATTERN.match(tag)
        if not match:
            continue
        version_str = match.group(1)
        try:
            version = Version(version_str)
        except Exception:
            continue
        if version in spec:
            candidates.append(version)

    if not candidates:
        raise EngineNotFoundError(f'No engine release found matching {compat_range}')

    best = max(candidates)
    return str(best)
