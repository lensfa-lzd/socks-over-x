import asyncio
import threading
import time

import websockets

from script import process_data_tcp, process_data_websocket


async def sender():
    """
    监听websocket服务器发送过来的消息
    并将其
    :return:
    """
    global remote_websocket
    global client_writer_dict

    while True:
        recv_data = await remote_websocket.recv()
        # print(f'Local: Recv data from websocket {len(recv_data)}')
        client_id, send_data = process_data_websocket(recv_data)

        writer = client_writer_dict.get(client_id)
        if writer:
            writer.write(send_data)

        # # 不向本地TCP/socks客户端发送空包，因为会中断连接
        # if send_data:
        #     # 使用id查找与客户端的连接, 并将数据发送回客户端
        #     writer = client_writer_dict.get(client_id)
        #     if writer:
        #         writer.write(send_data)


async def listener(reader, writer):
    """
    获取连接客户端的地址
    监听客户端发送的消息
    # 给客户端发送消息由另一个模块执行

    :return:
    """
    global remote_websocket
    global client_writer_dict

    # 获取连接到的TCP客户端的地址，并将writer加入到到字典之中
    # type(client_addr) <class 'tuple'> ('127.0.0.1', 8888)
    client_addr_info = writer.get_extra_info('peername')
    # print(f"Received connection from {client_addr_info}")
    client_addr, client_port = client_addr_info[0], str(client_addr_info[1])

    client_id = client_addr + client_port
    with lock:
        client_writer_dict[client_id] = writer

    while True:
        # 监听客户端发送的消息并将其发送到远程服务器
        recv_data = await reader.read(1024)
        # print(f'Local: Recv data from client {len(recv_data)}')
        send_data = process_data_tcp(client_id, recv_data)
        await remote_websocket.send(send_data)
        if not recv_data:
            break

    # 收到了TCP/socks发送过来的空包，代表需要关闭连接，并将id从字典中移除
    writer.close()
    del client_writer_dict[client_id]


async def start_server():
    """
    启动TCP监听器
    将TCP收到的消息发送到远程websocket服务器
    将远程websocket服务器发送回来的消息发送到TCP客户端

    :return:
    """
    global tcp_server
    if not tcp_server:
        server = await asyncio.start_server(
            listener, '0.0.0.0', 8000)

        addr = server.sockets[0].getsockname()
        print(f'Serving on {addr}')

        tcp_server = True
        async with server:
            await server.serve_forever()


async def main():
    global remote_websocket
    global client_writer_dict

    uri = "ws://127.0.0.1:9000/handle_websocket"
    while True:
        try:
            remote_websocket = await websockets.connect(uri)
            break
        except:
            await asyncio.sleep(1)
            print(f"1 Try ..., Wait for 1 seconds before retrying...")

    while True:
        try:
            # 启动WebSocket服务器
            await asyncio.gather(
                sender(),
                start_server(),
            )
        except:
            # 断线删除所有socks连接
            close_list = []
            for socks_task_close in client_writer_dict:
                close_list.append(socks_task_close)
            for socks_task_close in close_list:
                # 字典的pop()方法从tasks字典中删除相应的任务
                socks_task = client_writer_dict.pop(socks_task_close, None)
                socks_task.close()

            # 阻塞到连上为止
            while True:
                try:
                    remote_websocket = await websockets.connect(uri)
                    print('2 Connection Success')
                    break
                except:
                    time.sleep(1)
                    print(f"2 Try ..., Wait for 1 seconds before retrying...")


# 创建一个线程锁
lock = threading.Lock()
client_writer_dict = {}

tcp_server = False
# 在全局范围内定义 WebSocket 连接对象
remote_websocket = None
asyncio.run(main())
