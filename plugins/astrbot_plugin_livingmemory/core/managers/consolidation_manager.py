"""记忆库定期整合管理器。

把零散的低价值记忆聚合、整理、总结为更精炼的记忆，从源头控制记忆库规模。
聚合粒度（同会话 / 语义聚类）、旧记忆处理（归档 / 删除）、触发方式（每日 / 反思）
均由 memory_consolidation 配置控制。
"""

from __future__ import annotations

import time
import asyncio
import json
import hashlib
import os
from pathlib import Path
from typing import Any

from astrbot.api import logger


class MemoryConsolidationManager:
    """按配置定期整合记忆库。"""

    def __init__(self, memory_engine, memory_processor, config_manager):
        self.memory_engine = memory_engine
        self.memory_processor = memory_processor
        self.config_manager = config_manager
        # reflection 触发模式下的最小运行间隔（秒），避免每条消息都触发
        self._last_run_at = 0.0
        self._min_run_interval = 6 * 3600.0
        self._lock = asyncio.Lock()
        path = vars(memory_engine).get('db_path') if memory_engine else None
        self._state_path = Path(path).with_name('consolidation_state.json') if isinstance(path, (str, Path)) else None
        self._state = {}
        if self._state_path and self._state_path.is_file():
            try:
                self._state = json.loads(self._state_path.read_text('utf-8'))
                self._last_run_at = float(self._state.get('last_run_at', 0))
            except (OSError, ValueError):
                pass

    def status(self):
        return {'running': self._lock.locked(), **self._state}

    def _save_state(self, stats):
        self._state.update(last_run_at=self._last_run_at, last_result=stats)
        self._state['runs'] = [*self._state.get('runs', []), {'at':time.time(), **stats}][-20:]
        if self._state_path:
            temp = self._state_path.with_suffix('.tmp')
            temp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, self._state_path)

    @property
    def config(self) -> dict[str, Any]:
        return self.config_manager.get_section("memory_consolidation")

    async def maybe_run(self, trigger: str) -> dict[str, Any]:
        """按触发方式执行整合（不匹配或未启用时跳过）。

        Args:
            trigger: "daily" 或 "reflection"。
        """
        cfg = self.config
        if not cfg.get("enabled", False) or cfg.get("trigger", "daily") != trigger:
            return {"skipped": True}
        return await self.run_consolidation()

    async def run_consolidation(self, force: bool = False) -> dict[str, Any]:
        if self._lock.locked():
            return {'skipped': True, 'reason': 'already running'}
        async with self._lock:
            result = await self._run_consolidation(force)
            if not result.get('skipped'):
                self._save_state(result)
            return result

    async def _run_consolidation(self, force: bool = False) -> dict[str, Any]:
        """执行一轮记忆整合。返回统计信息。

        Args:
            force: 是否忽略最小运行间隔（每日定时触发时传入 True）。
        """
        cfg = self.config
        if not cfg.get("enabled", False):
            return {"skipped": True}
        if not self.memory_engine or not self.memory_processor:
            logger.warning("[记忆整合] 组件未就绪，跳过")
            return {"skipped": True, "reason": "components not ready"}

        now = time.time()
        if not force and now - self._last_run_at < self._min_run_interval:
            return {"skipped": True, "reason": "cooldown"}
        self._last_run_at = now

        try:
            candidates = await self._query_candidates(cfg)
            if not candidates:
                return {"candidates": 0, "groups": 0, "merged": 0}

            groups = await self._build_groups(candidates, cfg)
            if not groups:
                return {"candidates": len(candidates), "groups": 0, "merged": 0}

            max_groups = int(cfg.get("max_groups_per_run", 5))
            stats = {
                "candidates": len(candidates),
                "groups": 0,
                "merged": 0,
                "archived": 0,
                "deleted": 0,
                "failed": 0,
                "skipped_groups": 0,
                "model_calls": 0,
            }
            now = time.time()
            deferred = {k:v for k,v in self._state.get('deferred', {}).items() if now-float(v) < 7*86400}
            self._state['deferred'] = deferred
            for group in groups:
                stable = [{**m, 'metadata':{k:v for k,v in m['metadata'].items() if k not in {'access_count','last_access_time'}}} for m in group]
                signature = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if signature in deferred:
                    stats['skipped_groups'] += 1
                    continue
                if stats['model_calls'] >= max_groups:
                    break
                try:
                    stats['model_calls'] += 1
                    result = await self._consolidate_group(group, cfg)
                    if result.get('skipped'):
                        deferred[signature] = now
                        stats['skipped_groups'] += 1
                        continue
                    stats["groups"] += 1
                    stats["merged"] += result["merged"]
                    stats["archived"] += result["removed"]
                except Exception as e:
                    stats["failed"] += 1
                    logger.error(f"[记忆整合] 整合组失败: {e}", exc_info=True)

            self._state['deferred'] = dict(list(deferred.items())[-200:])
            logger.info(f"[记忆整合] 完成: {stats}")
            return stats
        except Exception as e:
            logger.error(f"[记忆整合] 运行失败: {e}", exc_info=True)
            return {"error": str(e)}

    async def _query_candidates(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        """查询符合整合条件的候选记忆（低重要度 + 足够旧 + 活跃状态）。"""
        max_importance = float(cfg.get("max_importance", 0.5))
        cutoff = time.time() - int(cfg.get("min_age_days", 7)) * 86400.0
        try:
            rows = await self.memory_engine.get_consolidation_candidates(
                max_importance, cutoff
            )
            return [m for m in rows if self._eligible(m, cfg)][:int(cfg.get('candidate_limit', 200))]
        except Exception as e:
            logger.error(f"[记忆整合] 查询候选记忆失败: {e}", exc_info=True)
            raise

    async def _build_groups(
        self, candidates: list[dict[str, Any]], cfg: dict[str, Any]
    ) -> list[list[dict[str, Any]]]:
        groups = []
        for scope_group in self._split_by_scope(candidates):
            if cfg.get('granularity', 'semantic') == 'semantic':
                try:
                    groups.extend(await self._group_semantic(scope_group, cfg))
                except Exception:
                    logger.warning('[记忆整理] 本作用域语义分组暂不可用，跳过，不混合所有话题')
            else:
                groups.append(scope_group)
        min_per = int(cfg.get("min_memories_per_group", 3))
        bounded = []
        for group in groups:
            current, size = [], 0
            for mem in group:
                length = len(str(mem.get('content',''))) + len(json.dumps(mem.get('metadata',{}), ensure_ascii=False))
                if len(current) >= int(cfg.get('max_group_size',8)) or size+length > 12000:
                    if len(current) >= min_per:
                        bounded.append(current)
                    current, size = [], 0
                if length <= 12000:
                    current.append(mem)
                    size += length
            if len(current) >= min_per:
                bounded.append(current)
        return sorted(bounded, key=len, reverse=True)

    def _eligible(self, mem, cfg):
        m = mem.get('metadata', {})
        threshold = float(self.config_manager.get('importance_decay.protected_importance_threshold', .8))
        return bool(m.get('session_id') and m.get('status','active') == 'active'
            and not m.get('pinned') and not m.get('protected') and not m.get('do_not_merge')
            and m.get('summary_quality') != 'low'
            and float(m.get('importance',.5)) < min(float(cfg.get('max_importance',.5)), threshold)
            and 0 < float(m.get('create_time',0)) < time.time()-int(cfg.get('min_age_days',7))*86400)

    @staticmethod
    def _scope(mem):
        m = mem['metadata']
        return (str(m.get('session_id') or ''), str(m.get('persona_id') or 'default'),
            str(m.get('source_type') or 'conversation'), str(m.get('memory_type') or ''),
            str(m.get('source_key') or '') if m.get('source_type') == 'qzone_observation' else '')

    @staticmethod
    def _split_by_scope(
        group: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        """按 (session_id, persona_id) 拆分候选，丢弃无会话作用域的旧记忆。"""
        buckets: dict[tuple[str, Any], list[dict[str, Any]]] = {}
        for mem in group:
            metadata = mem["metadata"]
            session_id = metadata.get("session_id")
            if not session_id:
                continue
            key = MemoryConsolidationManager._scope(mem)
            buckets.setdefault(key, []).append(mem)
        return list(buckets.values())

    def _group_by_session(
        self, candidates: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        return self._split_by_scope(candidates)

    async def _group_semantic(self, candidates, cfg):
        """Complete-link groups do not merge unrelated ends of a similarity chain."""
        threshold = float(cfg.get('semantic_similarity_threshold', .88))
        pairs = await self.memory_engine.find_similar_pairs([m['id'] for m in candidates], threshold)
        similarities = {frozenset((a,b)):s for a,b,s in pairs if s >= threshold}
        groups = []
        for mem in sorted(candidates, key=lambda m:m['id']):
            for group in groups:
                if len(group) < int(cfg.get('max_group_size',8)) and all(frozenset((mem['id'], other['id'])) in similarities for other in group):
                    group.append(mem)
                    break
            else:
                groups.append([mem])
        return groups

    @staticmethod
    def _union(values):
        result, seen = [], set()
        for value in values:
            key = json.dumps(value, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                result.append(value)
                seen.add(key)
        return result

    def _preserve_metadata(self, group, merged):
        result = {}
        for key in ('key_facts','participant_identities','participants','time_tags'):
            result[key] = self._union([v for mem in group for v in mem['metadata'].get(key,[])])
        result['key_facts'] = self._union([*result['key_facts'], *merged.get('key_facts',[])])
        result['companion'] = {key:self._union([v for mem in group for v in mem['metadata'].get('companion',{}).get(key,[])]) for key in ('facts','continuity','purposes')}
        from datetime import datetime
        def timestamp(value):
            if isinstance(value,(int,float)):
                return float(value)
            try:
                return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
            except ValueError:
                return 0
        for key, chooser in (('source_time_start',min),('source_time_end',max)):
            values = [m['metadata'][key] for m in group if m['metadata'].get(key)]
            if values:
                result[key] = chooser(values,key=timestamp)
        for key in ('source_type','memory_type','source_key'):
            if group[0]['metadata'].get(key):
                result[key] = group[0]['metadata'][key]
        result['consolidation_source_windows'] = [m['metadata']['source_window'] for m in group if m['metadata'].get('source_window')]
        return result

    async def _consolidate_group(self, group, cfg):
        if len({self._scope(mem) for mem in group}) != 1 or not all(self._eligible(mem,cfg) for mem in group):
            return {'skipped':True,'reason':'scope or protection'}
        merged = await self.memory_processor.merge_memories(group)
        if merged.get('merge_allowed') is not True:
            return {'skipped':True,'reason':'unrelated or conflicting'}
        metadata = self._preserve_metadata(group,merged)
        summary = str(merged['summary']).strip()
        if not summary:
            raise ValueError('Empty consolidation summary')
        rich_content = summary + (' | '+'；'.join(metadata['key_facts']) if metadata['key_facts'] else '')
        if len(rich_content) >= sum(len(str(m.get('content',''))) for m in group):
            return {'skipped':True,'reason':'no compression benefit'}
        metadata.update(topics=merged.get('topics',[]),persona_summary=summary,canonical_summary=rich_content,
            summary_schema_version='v2',consolidated_from=[m['id'] for m in group],consolidated_at=time.time())
        sources = self._union([source for mem in group for source in await self.memory_engine.get_memory_source(mem['id'])])
        session_id, persona_id = self._scope(group[0])[:2]
        new_id = await self.memory_engine.add_memory(content=rich_content,session_id=session_id,persona_id=persona_id,
            importance=max(float(merged.get('importance',.5)),*(float(m['metadata'].get('importance',.5)) for m in group)),
            metadata=metadata,source_messages=sources or None)
        old_ids = [m['id'] for m in group]
        try:
            removed = await self.memory_engine.archive_memories(old_ids,
                audit={'archive_reason':'consolidation','consolidated_into':new_id},
                consolidation_expected={m['id']:m for m in group})
            if removed != len(old_ids):
                raise RuntimeError('Consolidation source set changed')
        except Exception:
            # A downstream index error can occur after archive commits. Keep the
            # replacement whenever a source is archived, instead of losing both.
            originals = [await self.memory_engine.get_memory(i) for i in old_ids]
            if all(m and (json.loads(m['metadata']) if isinstance(m['metadata'],str) else m['metadata']).get('status','active') == 'active' for m in originals):
                await self.memory_engine.delete_memory(new_id)
            raise
        return {'new_id':new_id,'merged':len(group),'removed':removed}


__all__ = ["MemoryConsolidationManager"]
