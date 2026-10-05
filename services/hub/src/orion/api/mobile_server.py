"""Separate TLS listener; legacy public/private listeners keep their own ports."""
import asyncio
import os
import socket
import uvicorn

from orion.kernel.local_security import tls_identity, local_address


def advertise(identity, port):
    from zeroconf import Zeroconf, ServiceInfo
    address = local_address()
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    zc = Zeroconf()
    info = ServiceInfo('_orion-mobile._tcp.local.', identity['id'] + '._orion-mobile._tcp.local.',
                       addresses=[socket.inet_pton(family, address)], port=port,
                       properties={'id': identity['id'], 'name': identity['name'], 'v': '1', 'scheme': 'https', 'fp': identity['fp']},
                       server=identity['id'] + '.local.')
    try:
        zc.register_service(info)
    except BaseException:
        zc.close()
        raise
    return zc, info


async def serve():
    identity = tls_identity()
    port = int(os.getenv('ORION_MOBILE_PORT', '8443'))
    if not 1 <= port <= 65535:
        raise ValueError('ORION_MOBILE_PORT must be between 1 and 65535')
    host = os.getenv('ORION_MOBILE_BIND_HOST', '0.0.0.0')
    # ASGI TLS schemes come only from the TLS socket, never forwarded headers.
    config = uvicorn.Config('orion.api.main:mobile_app', host=host, port=port, proxy_headers=False,
                            ssl_keyfile=identity['key'], ssl_certfile=identity['cert'])
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    while not server.started and not serving.done():
        await asyncio.sleep(0.05)
    if serving.done():
        return await serving
    zc, info = None, None
    async def refresh_discovery():
        nonlocal zc, info
        from orion.kernel.pinned_router import resolve_trusted_routers
        while True:
            try:
                if zc is None:
                    zc, info = await asyncio.to_thread(advertise, identity, port)
                from orion.kernel.router_bootstrap import bootstrap_local_router
                await bootstrap_local_router()
                await resolve_trusted_routers()
                current = local_address()
                family = socket.AF_INET6 if ':' in current else socket.AF_INET
                addresses = [socket.inet_pton(family, current)]
                if [current] != info.parsed_addresses():
                    info.addresses = addresses
                    await asyncio.to_thread(zc.update_service, info)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Local discovery refresh failed')
            await asyncio.sleep(15)
    refresh = asyncio.create_task(refresh_discovery())
    try:
        await serving
    finally:
        refresh.cancel()
        await asyncio.gather(refresh, return_exceptions=True)
        if zc:
            await asyncio.to_thread(zc.unregister_service, info)
            await asyncio.to_thread(zc.close)


if __name__ == '__main__':
    asyncio.run(serve())
