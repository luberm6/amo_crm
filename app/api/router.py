"""
Main API router — registers all versioned sub-routers.
Adding a new resource means adding one line here.
"""
from fastapi import APIRouter
from app.api.v1 import admin_auth, agents, browser_calls, calls, health, knowledge_base, mango_webhooks, providers, transfers, webhooks
api_router = APIRouter()
# Health checks are not versioned — infrastructure tools expect stable paths
api_router.include_router(health.router)
# All domain routes live under /v1 for future API versioning
api_router.include_router(admin_auth.router, prefix="/v1")
api_router.include_router(agents.router, prefix="/v1")
api_router.include_router(knowledge_base.router, prefix="/v1")
api_router.include_router(providers.router, prefix="/v1")
api_router.include_router(calls.router, prefix="/v1")
api_router.include_router(browser_calls.router, prefix="/v1")
api_router.include_router(transfers.router, prefix="/v1")
# Webhooks are not versioned — Vapi has the URL baked into assistant config
api_router.include_router(webhooks.router)
api_router.include_router(mango_webhooks.router)
