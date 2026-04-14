#!/usr/bin/env python3
"""
Resolve and optionally execute a Mango originate smoke from an agent-bound line.

Default mode is safe dry-run. Use `--live --to +7...` to place a real Mango
callback/originate request through the current tenant credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from app.core.config import settings  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.integrations.telephony.mango import MangoTelephonyAdapter  # noqa: E402
from app.integrations.telephony.mango_runtime import resolve_mango_from_ext  # noqa: E402
from app.models.agent_profile import AgentProfile  # noqa: E402
from app.services.telephony_routing_service import TelephonyRoutingService  # noqa: E402


async def _resolve_agent(agent_id: str | None) -> AgentProfile | None:
    async with AsyncSessionLocal() as session:
        if agent_id:
            return await session.get(AgentProfile, agent_id)
        result = await session.execute(
            select(AgentProfile)
            .where(
                AgentProfile.is_active.is_(True),
                AgentProfile.telephony_provider == "mango",
                AgentProfile.telephony_line_id.is_not(None),
            )
            .order_by(AgentProfile.updated_at.desc())
        )
        return result.scalars().first()


async def _build_resolution(agent_id: str | None) -> dict[str, Any]:
    agent = await _resolve_agent(agent_id)
    if agent is None:
        return {"agent_found": False}

    async with AsyncSessionLocal() as session:
        svc = TelephonyRoutingService(session)
        binding = await svc.resolve_outbound_binding(agent.id)
        if binding is None or binding.telephony_line is None:
            return {
                "agent_found": True,
                "agent_id": str(agent.id),
                "agent_name": agent.name,
                "line_found": False,
            }

        line = binding.telephony_line
        resolution = await resolve_mango_from_ext(
            explicit_from_ext=(binding.agent.telephony_extension or "").strip() or None,
            metadata={
                "telephony_remote_line_id": line.remote_line_id,
                "telephony_line_phone_number": line.phone_number,
                "telephony_extension": binding.agent.telephony_extension or line.extension,
            },
        )
        return {
            "agent_found": True,
            "agent_id": str(binding.agent.id),
            "agent_name": binding.agent.name,
            "line_found": True,
            "line_id": str(line.id),
            "remote_line_id": line.remote_line_id,
            "line_phone_number": line.phone_number,
            "line_label": line.label,
            "line_is_active": line.is_active,
            "resolved_from_ext": resolution.value,
            "from_ext_source": resolution.source,
            "originate_ready": bool(line.is_active and resolution.value and settings.mango_configured),
        }


async def _run_live(to_number: str, resolution: dict[str, Any]) -> dict[str, Any]:
    adapter = MangoTelephonyAdapter()
    try:
        result = await adapter.originate_call(
            to_number,
            metadata={
                "telephony_remote_line_id": resolution["remote_line_id"],
                "telephony_line_phone_number": resolution["line_phone_number"],
                "telephony_extension": resolution.get("resolved_from_ext"),
                "agent_id": resolution["agent_id"],
            },
        )
    finally:
        await adapter.aclose()
    return {
        "leg_id": result.leg_id,
        "provider_response": result.provider_response,
    }


async def main(agent_id: str | None, to_number: str | None, live: bool) -> int:
    resolution = await _build_resolution(agent_id)
    print(json.dumps({"resolution": resolution}, ensure_ascii=False, indent=2))

    if not resolution.get("agent_found"):
        print("BLOCKED: no active Mango-bound agent found.")
        return 2
    if not resolution.get("line_found"):
        print("BLOCKED: agent has no bound Mango line.")
        return 2
    if not resolution.get("originate_ready"):
        print("BLOCKED: originate path is not ready for this agent binding.")
        return 2
    if not live:
        print("Dry-run only. Re-run with --live --to +7... to place a real callback/originate.")
        return 0
    if not to_number:
        print("BLOCKED: --to is required with --live.")
        return 2

    result = await _run_live(to_number, resolution)
    print(json.dumps({"live_result": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resolve or execute Mango originate smoke from a bound agent line")
    parser.add_argument("--agent-id", help="Optional agent UUID; defaults to the most recently updated Mango-bound agent")
    parser.add_argument("--to", help="Destination phone number for a live originate smoke")
    parser.add_argument("--live", action="store_true", help="Actually place the Mango callback/originate request")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.agent_id, args.to, args.live)))
