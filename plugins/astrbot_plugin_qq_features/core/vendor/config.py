from typing import Any, Protocol

class PluginConfig(Protocol):
    client: Any
    timeout: int
    cookie_ttl: int

