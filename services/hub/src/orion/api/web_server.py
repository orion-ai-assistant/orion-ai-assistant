"""TLS network listeners and an explicit guarded loopback HTTP UI exception."""
import argparse
import os
import uvicorn
from orion.kernel.local_security import tls_identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--public', action='store_true')
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--http-loopback', action='store_true', help='Explicit local HTTP exception, never public/LAN')
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    args = parser.parse_args()
    host = args.host or ('0.0.0.0' if args.public else '127.0.0.1')
    if args.http_loopback and (args.public or host not in {'127.0.0.1', '::1', 'localhost'}):
        raise ValueError('Plain HTTP is restricted to explicit loopback local UI')
    if not args.public and host not in {'127.0.0.1', '::1', 'localhost'} and os.getenv('ORION_PRIVATE_DOCKER_LISTENER') != 'true':
        raise ValueError('Owner listener must bind to loopback')
    port = args.port or int(os.getenv('HUB_CONTAINER_PORT', os.getenv('ORION_PUBLIC_PORT', '8911') if args.public else os.getenv('ORION_LOCAL_PORT', '8910') if args.local else os.getenv('HUB_PORT', '8909')))
    if args.public and not args.port and port == int(os.getenv('ORION_LOCAL_PORT', '8910')):
        port = 8911
    if not 1 <= port <= 65535:
        raise ValueError('HTTPS port must be between 1 and 65535')
    target = 'orion.api.main:local_app' if args.local else 'orion.api.main:public_app' if args.public else 'orion.api.main:app'
    if args.http_loopback:
        if not args.local:
            raise ValueError('Use --local for the guarded local HTTP app')
        uvicorn.run(target, host=host, port=port, proxy_headers=False)
        return
    identity = tls_identity()
    uvicorn.run(target, host=host, port=port,
                ssl_certfile=identity['cert'], ssl_keyfile=identity['key'], proxy_headers=False)


if __name__ == '__main__':
    main()
