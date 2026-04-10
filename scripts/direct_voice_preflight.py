from __future__ import annotations

import asyncio
import json
import sys

from app.core.redis_client import close_redis, init_redis
from app.db.session import AsyncSessionLocal
from app.services.preflight_service import DirectVoicePreflightService


async def _main() -> int:
    await init_redis()
    try:
        async with AsyncSessionLocal() as session:
            payload = await DirectVoicePreflightService(session).run()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if payload["status"] == "fail" else 0
    finally:
        await close_redis()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
