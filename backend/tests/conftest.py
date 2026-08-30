import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db.main import engine
from app.db.models import Base
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def isolated_database():
    """Give every backend test a fresh schema on its own async event loop."""
    await engine.dispose()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
