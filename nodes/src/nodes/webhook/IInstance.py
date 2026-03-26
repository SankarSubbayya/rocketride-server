# =============================================================================
# MIT License
# Copyright (c) 2026 Aparavi Software AG
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# =============================================================================

from typing import List, Optional

from ai.modules.data.webhook_text_routing import plain_text_lane_from_listener_names
from rocketlib import IInstanceBase


class IInstance(IInstanceBase):
    """
    Webhook source driver.

    HTTP uploads are handled by the data stack (DataConn) using the same listener
    graph as ``instance.hasListener(...)`` here. ``plain_text_lane_for_graph`` is
    the architectural entry that mirrors product routing for ``text/*`` MIME.
    """

    def plain_text_lane_for_graph(self) -> Optional[str]:
        """
        Resolve internal lane for plain text using ``hasListener('text')`` and
        ``hasListener('questions')``, matching DataConn (which uses
        ``pipe.getListeners()`` for the same pipeline wiring).
        """
        listeners: List[str] = []
        if self.instance.hasListener('questions'):
            listeners.append('questions')
        if self.instance.hasListener('text'):
            listeners.append('text')
        return plain_text_lane_from_listener_names(listeners)
