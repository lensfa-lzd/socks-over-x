# -*- coding: UTF-8 -*-

import asyncio
import threading

import websockets

from script import process_data_websocket, process_data_tcp


async def handle_socks(client_id, reader, writer):
    global websocket_connection
    global socks_dict
    global socks_task_dict

    # print('Listening')
    while True:
        raw_data = await reader.read(1024*8)
        # print(f'Remote: Recv data from socks {len(raw_data)}')

        # 原始数据包中加上客户端的id
        send_data = process_data_tcp(client_id, raw_data)
        await websocket_connection.send(send_data)

        # 收到了TCP/socks发送过来的空包，代表需要关闭连接，并结束循环
        if not raw_data:
            writer.close()
            _ = socks_task_dict.pop(client_id, None)
            break


async def handle_websocket(websocket, path):
    global websocket_connection
    global socks_dict
    global socks_task_dict

    '''
    # 将新连接添加到客户端列表中
    # clients.append(websocket)
    websocket_addr, websocket_port = websocket.remote_address[0], str(websocket.remote_address[1])
    websocket_id = websocket_addr + websocket_port

    with lock:
        websocket_dict[websocket_id] = websocket
    '''
    websocket_addr, websocket_port = websocket.remote_address[0], str(websocket.remote_address[1])
    websocket_id = websocket_addr + websocket_port
    print('new connection from', websocket_id)

    # 将此连接赋予全局
    websocket_connection = websocket

    try:
        async for message in websocket:
            # 从原始数据包中解析出客户端的id
            # 使用client_id绑定与socks的连接
            # print(f'Remote: Recv data from client {len(message)}')
            client_id, data = process_data_websocket(message)
            socks_info = socks_dict.get(client_id)

            # print(f'Remote: Unpack data from client {len(data)}')
            # 表名已经建立过同远程服务器的连接了
            if socks_info:
                socks_reader, socks_writer = socks_info
                socks_writer.write(data)
                await socks_writer.drain()

                # 判断是否是空包
                if not data:
                    socks_writer.close()
                    await socks_writer.wait_closed()
                    # 字典的pop()方法从tasks字典中删除相应的任务
                    socks_task = socks_task_dict.pop(client_id, None)
                    if socks_task is not None:
                        socks_task.cancel()

            # 没有建立过连接就新建连接，并加入字典
            else:
                # print('Creating Connection')
                socks_reader, socks_writer = await asyncio.open_connection('192.168.35.115', 10810)
                # print('Waiting dict')
                with lock:
                    socks_dict[client_id] = (socks_reader, socks_writer)

                # print('Create Connection')
                # print(f'Remote: Send data from socks {len(data)}')
                socks_writer.write(data)
                await socks_writer.drain()
                socks_task_dict[client_id] = asyncio.create_task(handle_socks(client_id, socks_reader, socks_writer))
    except:
        # 断线了删除所有任务
        close_list = []
        for socks_task_close in socks_task_dict:
            close_list.append(socks_task_close)
        for socks_task_close in close_list:
            # 字典的pop()方法从tasks字典中删除相应的任务
            socks_task = socks_task_dict.pop(socks_task_close, None)
            if socks_task is not None:
                socks_task.cancel()


async def start_server():
    async with websockets.serve(handle_websocket, "0.0.0.0", 9000):
        await asyncio.Future()


async def main():
    # 启动WebSocket服务器
    await asyncio.gather(
        start_server(),
    )


# 创建一个线程锁
lock = threading.Lock()

# 保存WebSocket客户端字典的全局变量
# 保存Socks连接的本地端口
# websocket_dict = {}
websocket_connection = None
socks_dict = {}
socks_task_dict = {}
asyncio.run(main())

