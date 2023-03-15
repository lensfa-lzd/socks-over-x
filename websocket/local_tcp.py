import asyncio
import logging
import threading

from script import process_data_tcp, process_data_websocket


async def listener(reader, writer):
    """
    获取连接客户端的地址
    监听客户端发送的消息
    # 给客户端发送消息由另一个模块执行
    将socks获得的数据送入队列

    cache: 一个队列，用于缓存数据
    :return:
    """
    global client_writer_dict
    global tcp_to_websocket

    # 获取连接到的TCP客户端的地址，并将writer加入到到字典之中
    # type(client_addr) <class 'tuple'> ('127.0.0.1', 8888)
    client_addr_info = writer.get_extra_info('peername')
    logging.info(f"Received connection from {client_addr_info}")
    client_addr, client_port = client_addr_info[0], str(client_addr_info[1])

    client_id = client_addr + client_port
    with lock:
        client_writer_dict[client_id] = writer

    # asyncio.create_task(sender())

    while True:
        try:
            # 监听客户端发送的消息并将其发送到远程服务器
            recv_data = await reader.read(1024 * buffer)
            logging.info(f'Local Tcp: Recv data from client {len(recv_data)}')
        except Exception as err:
            logging.warning(f'Local Tcp: {str(err)}')
            logging.warning(f'Local Tcp: {client_id} connection close')
            # 连接中断，向远程websocket服务器发送空包，以断开对应的连接
            recv_data = b''

        # # 已经封装成了srt数据，并将数据传入队列
        send_data = process_data_tcp(client_id, recv_data)
        # print(type(tcp_to_websocket))
        await tcp_to_websocket.put(send_data)
        if not recv_data:
            break

    # 收到了TCP/socks发送过来的空包，代表需要关闭连接，并将id从字典中移除
    writer.close()
    # None 是为方式返回异常(key error)
    _ = client_writer_dict.pop(client_id, None)


async def sender():
    """
    将队列中的数据提取出来，发送给tcp连接

    :return:
    """
    global tcp_to_websocket
    global client_writer_dict
    global websocket_to_tcp

    while True:
        # qsize 为队列中元素的个数，而不是长度
        qsize = websocket_to_tcp.qsize()
        if qsize > 0:
            # print(qsize)
            # 一次将数据全部传完，并配合sleep以减少cpu占用
            for _ in range(qsize):
                recv_data = await websocket_to_tcp.get()
                logging.info(f'Local Tcp: Recv data from websocket cache {len(recv_data)}')

                if recv_data:
                    client_id, send_data = process_data_websocket(recv_data)
                    writer = client_writer_dict.get(client_id)
                    if writer:
                        # StreamWriter对象是有可能已经断开
                        try:
                            writer.write(send_data)
                            await writer.drain()
                        except Exception as err:
                            logging.warning(f'Local Tcp: ' + str(err))
                            logging.warning(f'Local Tcp: Tcp connection {client_id} close')
                else:
                    logging.warning('Local Tcp: Websocket connection close')
                    break

        # 实测发现25ms是一个比较合适的值，10ms会导致一个核心被一直占用
        await asyncio.sleep(wait_for_cache/1000)


async def start_tcp(tcp_to_websocket_, websocket_to_tcp_, client_writer_dict_, buffer_, wait_for_cache_):
    """
    启动TCP监听器
    将TCP收到的消息发送到远程websocket服务器
    将远程websocket服务器发送回来的消息发送到TCP客户端

    :return:
    """
    global tcp_to_websocket
    global websocket_to_tcp
    global client_writer_dict
    global buffer
    global wait_for_cache

    client_writer_dict = client_writer_dict_
    tcp_to_websocket = tcp_to_websocket_
    websocket_to_tcp = websocket_to_tcp_
    buffer = buffer_
    wait_for_cache = wait_for_cache_

    server = await asyncio.start_server(
        listener, '0.0.0.0', 8000)

    addr = server.sockets[0].getsockname()
    logging.warning(f'Serving on {addr}')
    # print(f'Serving on {addr}')

    asyncio.create_task(sender())
    async with server:
        await server.serve_forever()


# 创建一个线程锁, 存放用户连接的字典
lock = threading.Lock()

client_writer_dict = None
tcp_to_websocket = None
websocket_to_tcp = None
buffer = None
wait_for_cache = None
