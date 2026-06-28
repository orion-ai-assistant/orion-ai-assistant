#!/usr/bin/env python3
import asyncio
import socketio


async def main():
    sio = socketio.AsyncClient(logger=True, engineio_logger=True)

    @sio.event
    async def connect():
        print('connected')

    @sio.event
    async def disconnect():
        print('disconnected')

    @sio.on('connected')
    async def on_connected(data):
        print('event connected', data)

    @sio.on('chat:message:received')
    async def on_message_received(data):
        print('event chat:message:received', data)

    @sio.on('chat:agent:response')
    async def on_agent_response(data):
        print('event chat:agent:response', data)

    @sio.on('chat:agent:done')
    async def on_agent_done(data):
        print('event chat:agent:done', data)

    @sio.on('chat:agent:error')
    async def on_agent_error(data):
        print('event chat:agent:error', data)

    @sio.on('chat:error')
    async def on_error(data):
        print('event chat:error', data)

    @sio.on('chat:joined')
    async def on_joined(data):
        print('event chat:joined', data)

    await sio.connect('http://localhost:8000?user_id=debuguser&token=testtoken', transports=['websocket'])

    await sio.emit('chat:message', {'message':{'text':'Selam 1'}, 'chatId':'loadtest-chat'})

    await asyncio.sleep(15)
    await sio.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
