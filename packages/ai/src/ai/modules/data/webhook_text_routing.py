"""
Shared routing for plain text (text/*) uploads vs connected source lanes.

The webhook HTTP path runs through DataConn, which uses pipe.getListeners().
Node IInstance uses instance.hasListener(lane) for the same graph. This module
keeps one implementation so both stay aligned with product spec.
"""

from __future__ import annotations

from typing import List, Optional


def plain_text_lane_from_listener_names(listeners: List[str]) -> Optional[str]:
    """
    Pick the internal DataConn lane name for a text/* MIME when the source has
    the given outgoing listener lanes.

    Returns:
        'text_and_questions' if both text and questions are wired,
        'questions' if only questions,
        'text' if only text,
        None if neither (caller should fall through to other MIME rules / raw).
    """
    has_text = 'text' in listeners
    has_questions = 'questions' in listeners
    if has_text and has_questions:
        return 'text_and_questions'
    if has_questions:
        return 'questions'
    if has_text:
        return 'text'
    return None
