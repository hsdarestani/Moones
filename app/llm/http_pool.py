"""App-owned connection pool. Tests/CLI without a lifespan remain self-contained."""
import httpx

_client: httpx.AsyncClient | None = None


def shared_client() -> httpx.AsyncClient | None:
    return _client if _client is not None and not _client.is_closed else None


async def start_pool() -> None:
    global _client
    if shared_client() is None:
        _client = httpx.AsyncClient(timeout=9, limits=httpx.Limits(
            max_connections=40, max_keepalive_connections=10, keepalive_expiry=30))


async def close_pool() -> None:
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()
