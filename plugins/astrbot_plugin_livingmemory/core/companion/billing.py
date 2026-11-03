"""Pure platform aggregation; platform balances remain separate from spending."""

from urllib.parse import urlsplit


def provider_platforms(providers) -> dict:
    """Use configured official hosts, not credentials or the displayed model name."""
    result = {}
    for provider in providers:
        config = getattr(provider, "provider_config", {}) or {}
        provider_id = str(config.get("id", ""))
        host = urlsplit(str(config.get("api_base", ""))).hostname
        if provider_id:
            result[provider_id] = {
                "api.deepseek.com": "deepseek",
                "api.xiaomimimo.com": "mimo",
            }.get(host, "other")
    return result


def platform_for(provider: str, aliases: dict | None = None) -> str:
    if aliases and provider in aliases:
        return aliases[provider]
    # Preserve the identity of older records whose provider was removed later.
    prefix = provider.split("/", 1)[0].lower()
    return prefix if prefix in ("deepseek", "mimo") else "other"


def platform_totals(today: list, month: list, aliases: dict | None = None) -> list:
    groups = {}
    for platform, name in (("deepseek", "DeepSeek"), ("mimo", "MiMo"), ("other", "其他／未识别平台")):
        groups[platform] = {"id": platform, "name": name}
        for period, rows in (("today", today), ("month", month)):
            selected = [r for r in rows if platform_for(r["provider"], aliases) == platform]
            groups[platform][period] = {
                "amount_cny": sum(float(r["amount"] or 0) for r in selected if r["currency"] == "CNY"),
                "calls": sum(r["calls"] for r in selected),
                "pending": sum(r["unknown"] for r in selected),
                "unconverted": sum(r["calls"] - r["unknown"] for r in selected if r["currency"] not in ("CNY", "unconfigured")),
            }
    return [v for k, v in groups.items() if k != "other" or v["month"]["calls"] or v["today"]["calls"]]
