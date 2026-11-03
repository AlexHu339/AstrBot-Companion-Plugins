"""Private text delivery: prepare ahead, switch at safe send boundaries."""
from __future__ import annotations

import asyncio
import copy
import contextvars
import functools
from collections import deque

_PACING = contextvars.ContextVar('companion_reply_pacing', default=None)


class ReplySuperseded(Exception):
    """A newer prepared answer replaces the unsent tail of an older answer."""


class _PacingAsyncio:
    """Scope-aware sleep adapter used only by the native RespondStage module."""
    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        return getattr(self.original, name)

    async def sleep(self, delay, result=None):
        state = _PACING.get()
        if state is None or not state['pacing']:
            return await self.original.sleep(delay, result)
        changed = state['changed']
        if changed.is_set():
            raise ReplySuperseded()
        if delay <= 0:
            await self.original.sleep(delay)
            if changed.is_set():
                raise ReplySuperseded()
            return result
        timer = asyncio.create_task(self.original.sleep(delay))
        wake = asyncio.create_task(changed.wait())
        try:
            await asyncio.wait((timer, wake), return_when=asyncio.FIRST_COMPLETED)
            if changed.is_set():
                raise ReplySuperseded()
            return result
        finally:
            for task in (timer, wake):
                if not task.done():
                    task.cancel()
            await asyncio.gather(timer, wake, return_exceptions=True)


class TextDelivery:
    def __init__(self):
        self.queues = {}
        self.workers = {}
        self.active = {}
        self.closed = False
        self.queued = 0
        self.coalesced = 0
        self.interrupted = 0
        self.original = None
        self.wrapper = None

    @staticmethod
    def _can_interrupt(event):
        # Do not cut a response carrying an inline action/recall confirmation.
        return not (
            event.get_extra('qqf_controls', {}).get('recall')
            or event.get_extra('qqf_profile_inline')
            or event.get_extra('qqf_social_inline')
        )

    def attach(self):
        from astrbot.core.pipeline.respond import stage as host
        self.host = host
        RespondStage = host.RespondStage
        self.original = RespondStage.process
        original = self.original
        self.original_asyncio = host.asyncio
        self.pacing_asyncio = _PacingAsyncio(host.asyncio)
        host.asyncio = self.pacing_asyncio

        @functools.wraps(original)
        async def process(stage, event):
            from astrbot.core.message.components import Plain, Reply, At
            result = event.get_result()
            scope = event.unified_msg_origin
            eligible = (
                not self.closed and event.get_extra('companion_fast_text')
                and event.get_extra('companion_defer_current_response', True)
                and result is not None and result.is_model_result()
                and bool(result.chain)
                and any(isinstance(c, Plain) and c.text.strip() for c in result.chain)
                and all(isinstance(c, (Plain, Reply, At)) for c in result.chain)
            )
            if not eligible:
                if event.get_extra('companion_fast_text'):
                    await self.drain(scope)
                return await original(stage, event)
            queue = self.queues.setdefault(scope, deque())
            if len(queue) >= 8:
                await self.drain(scope)
                queue = self.queues.setdefault(scope, deque())
            snapshot = copy.copy(event)
            snapshot._result = copy.deepcopy(result)
            event.set_extra('companion_delivery_managed', True)
            event.set_extra('companion_delivered_parts', [])
            queue.append((stage, snapshot, contextvars.copy_context()))
            self.queued += 1
            state = self.active.get(scope)
            if state is not None and state['interruptible']:
                # Wake only a pacing wait. Never cancel an in-flight QQ send.
                state['changed'].set()
            if scope not in self.workers:
                self.workers[scope] = asyncio.create_task(self._run(scope, original))
            event.clear_result()

        self.wrapper = process
        RespondStage.process = process

    async def _run(self, scope, original):
        queue = self.queues[scope]
        try:
            while queue:
                stage, event, context = queue.popleft()
                await asyncio.create_task(self._deliver(stage, event, queue, original), context=context)
        finally:
            self.active.pop(scope, None)
            self.queues.pop(scope, None)
            self.workers.pop(scope, None)

    async def _deliver(self, stage, event, queue, original):
        from astrbot.api import logger
        from astrbot.core.message.components import Plain
        from astrbot.core.star.star_handler import EventType
        stage = copy.copy(stage)
        interval = stage._calc_comp_interval
        first = True
        sender = event.send
        scope = event.unified_msg_origin
        state = {'changed': asyncio.Event(), 'pacing': False, 'event': event,
                 'interruptible': self._can_interrupt(event)}
        self.active[scope] = state
        if queue and state['interruptible']:
            state['changed'].set()
        token = _PACING.set(state)

        async def calc_interval(comp):
            nonlocal first
            state['pacing'] = True
            if first:
                first = False
                return 0
            return await interval(comp)

        async def send(chain):
            state['pacing'] = False
            if state['changed'].is_set():
                # The native send loop catches Exception around send; the
                # pacing adapter raises outside it on the next iteration.
                event.set_extra('companion_delivery_interrupted', True)
                return False
            delivered = await sender(chain)
            if delivered is not False:
                event._has_send_oper = True
                event.set_extra('companion_text_delivered', True)
                parts = event.get_extra('companion_delivered_parts')
                parts.extend(c.text for c in chain.chain if isinstance(c, Plain) and c.text)
            return delivered

        stage._calc_comp_interval = calc_interval
        event.send = send
        done = event.get_extra('companion_text_done')
        try:
            await original(stage, event)
        except ReplySuperseded:
            state['pacing'] = False
            event.set_extra('companion_delivery_interrupted', True)
            self.interrupted += 1
            event.set_extra('qqf_social_topics_used', [])
            # Native after-send hooks now see the actual partial delivery.
            result = event.get_result()
            if result is not None:
                result.chain = [Plain(t) for t in event.get_extra('companion_delivered_parts', [])]
            await self.host.call_event_hook(event, EventType.OnAfterMessageSentEvent)
            event.clear_result()
        except Exception as exc:
            logger.warning('[聊天节奏] 文字交付异常，未自动重发 (%s)', type(exc).__name__)
        finally:
            state['pacing'] = False
            _PACING.reset(token)
            if self.active.get(scope) is state:
                self.active.pop(scope, None)
            if done is not None:
                done.set()

    def interrupt(self, scope):
        """A new substantive user message pauses stale tail text immediately."""
        state = self.active.get(scope)
        if state is not None and state['interruptible']:
            state['changed'].set()

    def progress(self, scope):
        state = self.active.get(scope)
        if not state or not state['interruptible']:
            return None
        return list(state['event'].get_extra('companion_delivered_parts', []))

    async def drain(self, scope):
        task = self.workers.get(scope)
        if task is not None and task is not asyncio.current_task():
            await asyncio.shield(task)

    async def close(self):
        self.closed = True
        if self.host.RespondStage.process is self.wrapper:
            self.host.RespondStage.process = self.original
        if self.workers:
            await asyncio.gather(*list(self.workers.values()), return_exceptions=True)
        if self.host.asyncio is self.pacing_asyncio:
            self.host.asyncio = self.original_asyncio

    def status(self):
        return {'active': not self.closed, 'sending': len(self.workers),
                'queued': self.queued, 'coalesced': 0, 'preserve_segments': True,
                'interrupt_when_new_ready': True, 'interrupted': self.interrupted}
