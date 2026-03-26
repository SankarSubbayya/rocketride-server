"""
Port allocation for engine instances.

Scans from a base port upward to find the next available port.
"""

import socket

from ..core.constants import CONST_DEFAULT_WEB_PORT


def find_available_port(base: int = CONST_DEFAULT_WEB_PORT) -> int:
    """Find the next available TCP port starting from base.

    Tries binding to each port sequentially. Returns the first port
    that accepts a bind. Scans up to 100 ports above base before giving up.
    """
    for offset in range(100):
        port = base + offset
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue

    raise OSError(f'No available port found in range {base}-{base + 99}')
