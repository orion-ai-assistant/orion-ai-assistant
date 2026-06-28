"""Statistics and monitoring endpoints."""
from fastapi import APIRouter, Request

from admin_panel.app.services.stats_service import StatsService

router = APIRouter(prefix="/api/stats", tags=["Statistics"])

# Service instance
stats_service = StatsService()


def _get_token(request: Request) -> str:
    return request.cookies.get("admin_session", "")


@router.get("/")
async def get_system_stats(request: Request):
    """Get overall system statistics and health."""
    return await stats_service.get_system_stats(token=_get_token(request))


@router.get("/health")
async def health_check(request: Request):
    """Quick health check endpoint."""
    stats = await stats_service.get_system_stats(token=_get_token(request))
    return {
        "status": stats.get("status", "unknown"),
        "timestamp": stats.get("timestamp"),
        "backend_reachable": stats.get("backend", {}).get("reachable", False)
    }
