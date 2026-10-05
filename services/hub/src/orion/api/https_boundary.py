"""Reject plaintext deployment mistakes; production runners bind TLS sockets."""
from starlette.responses import JSONResponse


class RequireTLS:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in {'http', 'websocket'} and scope.get('scheme') not in {'https', 'wss'}:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'detail': 'HTTPS is required'}, status_code=426)(scope, receive, send)
        return await self.app(scope, receive, send)


class LocalTransportBoundary:
    """Explicit plaintext exception for this PC only; never a LAN/tunnel app."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in {'http', 'websocket'}:
            from orion.api.access_boundary import is_local
            import ipaddress
            try:
                real_loopback = ipaddress.ip_address(scope['client'][0]).is_loopback
            except (KeyError, TypeError, ValueError):
                real_loopback = False
            # is_local includes peer, Host and absence of forwarding headers.
            if not real_loopback or not is_local(scope):
                if scope['type'] == 'websocket':
                    return await send({'type': 'websocket.close', 'code': 1008})
                return await JSONResponse({'detail': 'Local HTTP requires this PC loopback'}, status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)
