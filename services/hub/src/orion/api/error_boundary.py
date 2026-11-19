"""An exception that escapes a route must still reach the browser as a normal 500 WITH the CORS headers.

Starlette's ServerErrorMiddleware is the outermost layer: it turns an unhandled exception into a 500 AFTER the CORS middleware
has been passed, so that response carries no `Access-Control-Allow-Origin` and the browser reports a CORS error instead of the
real failure (seen on POST /memory/profile/compact when the background model failed). This middleware sits INSIDE the CORS one
(it is added before it) and answers such an exception itself; the CORS middleware then adds its headers to it."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


class ErrorBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def watch(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, watch)
        except Exception:
            if started:
                raise  # Half an answer is out: nothing honest can be added.
            logger.exception("Unhandled error in %s %s", scope.get("method"), scope.get("path"))
            body = json.dumps({"detail": "Sunucu hatası."}).encode()
            await send({"type": "http.response.start", "status": 500,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
