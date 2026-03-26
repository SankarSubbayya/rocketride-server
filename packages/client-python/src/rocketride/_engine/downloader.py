"""
Engine binary downloader.

Downloads release assets from GitHub and extracts them to
~/.rocketride/engines/{version}/.
"""

import os
import stat
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import aiohttp

from ..core.exceptions import EngineNotFoundError
from .paths import engines_dir, engine_binary, ensure_dirs
from .platform import asset_name, release_tag

_GITHUB_DOWNLOAD = 'https://github.com/rocketride-org/rocketride-server/releases/download'


async def download_engine(version: str) -> Path:
    """Download and extract the engine binary for the given version.

    Returns the path to the engine binary. No-ops if the binary already exists.
    """
    binary = engine_binary(version)
    if binary.exists():
        return binary

    ensure_dirs()

    asset = asset_name(version)
    tag = release_tag(version)
    url = f'{_GITHUB_DOWNLOAD}/{tag}/{asset}'

    # Download to a temp file first
    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.download')
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise EngineNotFoundError(f'Failed to download engine v{version}: HTTP {resp.status} from {url}')
                with os.fdopen(tmp_fd, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
                # fd is now closed, don't close again in finally
                tmp_fd = -1

        # Extract
        dest = engines_dir(version)
        dest.mkdir(parents=True, exist_ok=True)

        if asset.endswith('.tar.gz'):
            with tarfile.open(tmp_path, 'r:gz') as tar:
                tar.extractall(path=str(dest))
        elif asset.endswith('.zip'):
            with zipfile.ZipFile(tmp_path, 'r') as zf:
                zf.extractall(path=str(dest))

        # Set executable permission on Unix
        if sys.platform != 'win32' and binary.exists():
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        if not binary.exists():
            # The archive might have a different internal structure.
            # Look for any executable in the extracted directory.
            raise EngineNotFoundError(f'Downloaded and extracted v{version} but binary not found at {binary}. Check the release asset structure.')

        return binary

    finally:
        # Clean up temp file
        if tmp_fd >= 0:
            os.close(tmp_fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
