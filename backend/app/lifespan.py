import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from loguru import logger

from app.db.checkpointer import close_connection, get_checkpointer
from app.db.main import init_db
from app.traffic_governance.core import close_redis_client, create_redis_client
from app.leave.workflow import expire_and_resume, retry_pending


async def _leave_recovery_loop() -> None:
    while True:
        try:
            await expire_and_resume()
            await retry_pending()
        except Exception:
            logger.exception("Leave workflow recovery pass failed")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Running lifespan before the application startup!")
    app.state.traffic_redis = create_redis_client()
    await init_db()
    app.state.checkpointer = await get_checkpointer()
    recovery_task = asyncio.create_task(_leave_recovery_loop())
    try:
        yield
    finally:
        logger.info("Running lifespan after the application shutdown!")
        await close_connection()
        await close_redis_client(getattr(app.state, "traffic_redis", None))
        recovery_task.cancel()
        with suppress(asyncio.CancelledError):
            await recovery_task
