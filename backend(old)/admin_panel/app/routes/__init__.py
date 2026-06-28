"""Admin panel API route exports."""

from admin_panel.app.routes.config_routes import router as config_router
from admin_panel.app.routes.stats_routes import router as stats_router
from admin_panel.app.routes.user_routes import router as user_router

__all__ = ["config_router", "user_router", "stats_router"]
