"""Pure, bounded policies for retrieval, context and source validation."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

LOCAL_TZ = timezone(timedelta(hours=8))
SECRET_RE = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{12,}|bearer\s+[a-z0-9._-]{12,})\b"
    r"|\b(?:api[_ -]?key|access[_ -]?token|password|passwd|cookie|authorization|密码|密钥|令牌)"
    r"\s*[:=：]\s*[^\s，。；\n]+"
)
GREETING_RE = re.compile(
    r"^(?:嗯+|哦+|好[的呀吧]?|哈哈+|嘿嘿+|在[吗嘛]?|在不在|晚安|早[安呀]?|你好|小鱼|爸|收到|谢谢|[啊呀哦嗯哈嘿]+)[！？?!。，,.~～\s]*$"
)
HISTORY_CUE_RE = re.compile(
    r"记得|还记|之前|以前|上次|那次|当时|去年|前年|说过|约好|答应|记忆|偏好|喜欢什么"
)


def redact(text: Any) -> str:
    """Remove common credential forms before storing or displaying text."""
    return SECRET_RE.sub("[敏感凭据已隐藏]", str(text or ""))


def text_of(value: Any) -> str:
    """Read textual message parts without copying media payloads."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text_of(v) for v in value if v is not None)
    if isinstance(value, dict):
        return (
            str(value.get("text", ""))
            if value.get("type") in (None, "text", "input_text", "output_text")
            else ""
        )
    return str(getattr(value, "text", "") or "")


def matches_proactive_prompt(text: str, templates: list[str]) -> bool:
    """Keep configured synthetic task prompts out of imported user evidence."""
    for template in templates:
        if not template.strip():
            continue
        pattern = re.escape(template.strip())
        pattern = pattern.replace(re.escape("{{unanswered_count}}"), r"\d{1,10}")
        pattern = pattern.replace(re.escape("{{current_time}}"), r".{0,160}")
        if re.fullmatch(pattern, text.strip(), flags=re.DOTALL):
            return True
    return False


def token_estimate(text: str) -> int:
    """Estimate conservatively; the API usage remains the billing authority."""
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    emoji = len(re.findall(r"[\U0001f000-\U0001ffff]", text))
    return math.ceil(cjk * 1.15 + emoji * 3 + max(0, len(text) - cjk - emoji) * 0.4)


def clip_tokens(text: str, budget: int) -> str:
    """Bound a text fragment without assuming a character/token equivalence."""
    if budget <= 0:
        return ""
    if token_estimate(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        middle = (lo + hi + 1) // 2
        if token_estimate(text[:middle] + "…") <= budget:
            lo = middle
        else:
            hi = middle - 1
    return text[:lo] + "…" if lo else ""


def should_recall(query: str) -> bool:
    """Skip content-free greetings while retaining explicit memory questions."""
    clean = query.strip()
    return bool(
        clean
        and (
            HISTORY_CUE_RE.search(clean)
            or re.search(r"喜欢|讨厌|好累|难过|烦死|头疼", clean)
            or (len(clean) >= 4 and not GREETING_RE.fullmatch(clean))
        )
    )


def message_dict(message: Any) -> dict:
    """Normalize only the fields needed by the context policy."""
    if isinstance(message, dict):
        return message
    if hasattr(message, "model_dump"):
        return message.model_dump()
    return {
        "role": getattr(message, "role", ""),
        "content": getattr(message, "content", ""),
    }


def project_history(messages: list, budget: int, max_rounds: int) -> tuple[list, dict]:
    """Project whole rounds, preserving tool-call/result pairs and the source list.

    Args:
        messages: Host history; never mutated in place.
        budget: Conservative history token budget.
        max_rounds: Maximum number of complete user-initiated rounds.

    Returns:
        A copied request projection and its accounting trace.
    """
    prefix, rounds = [], []
    for message in messages or []:
        row = message_dict(message)
        role = row.get("role", "")
        if role == "system" and not rounds:
            prefix.append(message)
            continue
        if role == "user" or not rounds:
            rounds.append([])
        rounds[-1].append(message)
    selected, used = (
        [],
        sum(
            token_estimate(text_of(message_dict(m).get("content"))) + 8 for m in prefix
        ),
    )
    for block in reversed(rounds):
        cost = sum(
            token_estimate(json.dumps(message_dict(m), ensure_ascii=False, default=str))
            + 8
            for m in block
        )
        if selected and (used + cost > budget or len(selected) >= max_rounds):
            break
        selected.append(block)
        used += cost
    result = prefix + [m for block in reversed(selected) for m in block]
    return copy.deepcopy(result), {
        "rounds_before": len(rounds),
        "rounds_sent": len(selected),
        "estimated_tokens": used,
        "over_budget": used > budget,
    }


def source_hash(text: str) -> str:
    """Return a non-reversible content identifier for deduplication and tombstones."""
    return hashlib.sha256(redact(text).strip().encode("utf-8")).hexdigest()


def validate_companion_data(data: Any, messages: list) -> dict:
    """Accept user-supported observations, never unsupported assistant inventions.

    Args:
        data: Optional companion object returned in the existing summary call.
        messages: Original messages supplied to that same call.

    Returns:
        Normalized facts, continuity, and pending topics with real user evidence.
    """
    users = []
    for m in messages:
        role = getattr(m, "role", "") if not isinstance(m, dict) else m.get("role", "")
        role = getattr(role, "value", role)
        content = (
            getattr(m, "content", "")
            if not isinstance(m, dict)
            else m.get("content", "")
        )
        if str(role).lower() == "user":
            users.append(redact(text_of(content)))
    user_text = "\n".join(users)
    output = {"facts": [], "continuity": [], "purposes": []}
    if not isinstance(data, dict):
        return output
    for kind in output:
        items = data.get(kind, [])
        if not isinstance(items, list):
            continue
        for item in items[:8]:
            if not isinstance(item, dict):
                continue
            evidence = redact(item.get("evidence", "")).strip()
            content = redact(item.get("text", "")).strip()
            try:
                confidence = float(item.get("confidence", 0))
            except (ValueError, TypeError):
                continue
            if (
                len(evidence) < 4
                or evidence not in user_text
                or not content
                or not math.isfinite(confidence)
                or confidence < 0.8
            ):
                continue
            try:
                expires = min(365, max(1, int(item.get("expires_days", 7) or 7)))
            except (ValueError, TypeError, OverflowError):
                expires = 7
            output[kind].append(
                {
                    "key": str(item.get("key", ""))[:80] or source_hash(content)[:20],
                    "text": clip_tokens(content, 100),
                    "evidence": evidence[:240],
                    "confidence": min(confidence, 1),
                    "status": item.get("status")
                    if item.get("status") in ("open", "resolved", "cancelled")
                    else "open",
                    "expires_days": expires,
                }
            )
    return output


def price_for(model: str, rates: dict, at: float) -> tuple[dict | None, str]:
    """Resolve time-aware configured prices without inventing unsupported rates."""
    rule = rates.get(model)
    if not isinstance(rule, dict) or not rule.get("verified", False):
        return None, "unconfigured"
    date = datetime.fromtimestamp(at, UTC)
    peak = date.weekday() < 5 and (1 <= date.hour < 4 or 6 <= date.hour < 10)
    selected = rule.get("peak" if peak else "off_peak", rule)
    if not all(key in selected for key in ("input", "cached", "output")):
        return None, "unconfigured"
    return selected, str(rule.get("currency", "CNY"))
