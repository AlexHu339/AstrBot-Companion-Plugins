"""Pure intent and ranking policies; source evidence, never invented anecdotes."""
import re
import time


def asks_for_observations(text):
    text = str(text or '').strip()
    return bool(re.search(
        r'(?:今天|最近|这几天|这两天|刚才|近来).{0,14}(?:见闻|看(?:了|过|到|到了)?(?:什么|啥)|逛了什么|有(?:什么|啥).{0,5}(?:趣|意思|好玩|新鲜))'
        r'|(?:有(?:什么|啥)|讲点|说点|聊点|分享点).{0,5}(?:有趣|有意思|好玩|新鲜|见闻)'
        r'|(?:你|小鱼).{0,8}(?:最近看|逛空间|空间见闻)'
        r'|(?:看|刷|逛)(?:到|了|过)?了?(?:什么|啥).{0,6}(?:动态|说说|有趣|好玩)', text))


def rank_observations(rows, *, recent_seconds=48*3600, now=None):
    now = time.time() if now is None else now
    candidates = []
    for row in rows:
        text = str(row.get('text') or '').strip()
        visual = str(row.get('visual_observation') or '').strip()
        if (not row.get('ref') or not row.get('url') or not 0 <= now-float(row.get('observed') or 0) <= recent_seconds
            or max(len(text), len(visual)) < 18
            or re.search(r'sk-[a-zA-Z0-9_-]{16,}|(?:Cookie|Authorization)\s*[:=]', text, re.I)):
            continue
        score = min(len(text),300)/30 + min(len(visual),120)/40
        score += 3*len(re.findall(r'发现|有趣|风景|极光|四叶草|好玩|惊艳|可爱|猫|创作|画|旅行', text+visual))
        candidates.append((score, float(row.get('observed') or 0), row))
    return [r for _,_,r in sorted(candidates, key=lambda value:(value[0],value[1]), reverse=True)]
