import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db.main import engine
from app.db.models import Base
from app.main import app
from app.config import settings
from app.traffic_governance.core import create_redis_client


@pytest_asyncio.fixture(autouse=True)
async def isolated_database():
    """Give every backend test a fresh schema on its own async event loop."""
    await engine.dispose()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def isolated_traffic_governance():
    """Keep Redis limits independent across backend tests and test schemas."""
    redis = create_redis_client()
    app.state.traffic_redis = redis
    try:
        keys = [key async for key in redis.scan_iter(match=f"{settings.rate_limit_key_prefix}:*")]
        if keys:
            await redis.delete(*keys)
        yield
    finally:
        await redis.aclose()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
