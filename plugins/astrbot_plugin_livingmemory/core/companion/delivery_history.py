"""Reconcile interrupted private replies with what was actually delivered."""
from __future__ import annotations

import copy
import json
from astrbot.core.utils.session_lock import session_lock_manager


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(_text(p) for p in content)
    if isinstance(content, dict) and content.get('type') in ('text', 'input_text', 'output_text'):
        return str(content.get('text', ''))
    return ''


def corrected_history(history, full, actual, user_text):
    """Update exactly one plain assistant entry anchored to its user message.

    Ambiguous matches and assistant tool calls are left intact.
    """
    matches = []
    previous_user = ''
    for i, message in enumerate(history):
        if not isinstance(message, dict):
            continue
        if message.get('role') == 'user':
            previous_user = _text(message.get('content')).strip()
        if (message.get('role') == 'assistant' and not message.get('tool_calls')
            and _text(message.get('content')).strip() == full.strip()
            and previous_user == user_text.strip()):
            content = message.get('content')
            if isinstance(content, str) or (isinstance(content, list) and all(
                isinstance(p, dict) and p.get('type') in ('text', 'input_text', 'output_text') for p in content
            )):
                matches.append(i)
    if len(matches) != 1:
        return None
    result = copy.deepcopy(history)
    i = matches[0]
    if not actual.strip():
        result.pop(i)
    elif isinstance(result[i]['content'], str):
        result[i]['content'] = actual
    else:
        result[i]['content'] = [{'type': 'text', 'text': actual}]
    return result


async def reconcile_native_history(context, event, full, actual):
    """Wait for native history commits, then correct only this exact old turn."""
    scope = event.unified_msg_origin
    cid = event.get_extra('companion_delivery_cid')
    if not cid or not full:
        return False
    manager = context.conversation_manager
    async with session_lock_manager.acquire_lock(scope):
        if await manager.get_curr_conversation_id(scope) != cid:
            return False  # /reset or /new: do not restore or modify the new session
        conv = await manager.get_conversation(scope, cid)
        if conv is None:
            return False
        history = json.loads(conv.history) if isinstance(conv.history, str) else conv.history
        if not isinstance(history, list):
            return False
        corrected = corrected_history(history, full, actual, event.get_message_str())
        if corrected is None:
            return False
        await manager.update_conversation(scope, cid, history=corrected)
        return True
