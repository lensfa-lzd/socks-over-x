import asyncio
import logging
import threading

from script import unpack, re_pack


async def listener(websocket_id, client_id, reader, writer):
    """
    从socks的tcp连接中获取信息，通过websocket发送给客户端

    :param websocket_id:
    :param client_id: 用于标记连接
    :param reader: tcp reader
    :param writer: tcp writer
    :return:
    """
    global tcp_to_websocket
    global websocket_to_tcp

    # 这是一个双层字典, 存放tcp连接的reader和writer
    global reader_writer_dict
    # 这是一个双层字典, 存放tcp连接对应的任务,用于在中断连接的时候取消对应任务
    global task_dict

    while True:
        raw_data = await reader.read(1024 * buffer)

        # 原始数据包中加上客户端的id
        # print(f'Remote: Send data to client {len(raw_data)}')
        logging.info(f'Remote Tcp: Recv data from socks server {len(raw_data)}')
        send_data = re_pack(websocket_id, client_id, raw_data)
        await tcp_to_websocket.put(send_data)

        # 收到了TCP/socks发送过来的空包，代表需要关闭连接，并结束循环
        if not raw_data:
            writer.close()
            # None 是为方式返回异常(key error)
            # 弹出连接任务
            _ = task_dict[websocket_id].pop(client_id, None)
            # 弹出连接任务对应的tcp reader和tcp writer
            _ = reader_writer_dict[websocket_id].pop(client_id, None)
            break


async def sender():
    """
    将队列中的数据提取出来，发送给tcp连接
    这是一个全局函数,也就时所有websocket都对应这个sender

    :return:
    """
    global tcp_to_websocket
    global websocket_to_tcp

    # 这是一个双层字典, 存放tcp连接的reader和writer
    global reader_writer_dict
    # 这是一个双层字典, 存放tcp连接对应的任务,用于在中断连接的时候取消对应任务
    global task_dict

    # 需要区分第一次建立的连接,以及websocket异常断开的连接信息
    while True:
        # qsize 为队列中元素的个数，而不是长度
        qsize = websocket_to_tcp.qsize()
        if qsize > 0:
            # print(qsize)
            # 一次将数据全部传完，并配合sleep以减少cpu占用
            for _ in range(qsize):
                recv_data = await websocket_to_tcp.get()
                logging.info(f'Remote Tcp: Recv data from websocket cache {len(recv_data)}')
                websocket_id, client_id, raw_data = unpack(recv_data)

                if raw_data != b'close':
                    # socks_tasks应该是一个字典,对应一个websocket连接
                    socks_reader_writers = reader_writer_dict.get(websocket_id)
                    if socks_reader_writers:
                        # 表明已经建立过websocket所对应的字典了
                        socks_info = socks_reader_writers.get(client_id)

                        if socks_info:
                            # 表名已经建立过同远程服务器的连接了
                            socks_reader, socks_writer = socks_info
                            # StreamWriter对象是有可能已经断开
                            try:
                                # print(f'Remote: Send data to socks {len(data)}')
                                socks_writer.write(raw_data)
                                await socks_writer.drain()
                            except Exception as err:
                                logging.warning(f'Remote Tcp: ' + str(err))
                                logging.warning(f'Remote Tcp: Tcp connection {websocket_id}*{client_id} close')
                        else:
                            # TODO 连接错误的处理，下个版本就应该要做分流了，架构应该会不一样，所以下次再处理。
                            socks_reader, socks_writer = await asyncio.open_connection(socks_addr, socks_port)
                            with lock:
                                # 应该直接修改最外层的字典才会生效
                                reader_writer_dict[websocket_id][client_id] = (socks_reader, socks_writer)

                            # 注意依旧需要将信息发送给新建的连接
                            socks_writer.write(raw_data)
                            await socks_writer.drain()

                            # 一样的, 应该直接修改最外层的字典才会生效
                            task_dict[websocket_id][client_id] = asyncio.create_task(
                                listener(websocket_id, client_id, socks_reader, socks_writer))
                    else:
                        # 表明还没有建立websocket所对应的字典了, 新的websocket连接
                        socks_reader, socks_writer = await asyncio.open_connection(socks_addr, socks_port)
                        with lock:
                            # 新建websocket连接的字典
                            reader_writer_dict[websocket_id] = {
                                client_id: (socks_reader, socks_writer)
                            }

                            # 注意依旧需要将信息发送给新建的连接
                            socks_writer.write(raw_data)
                            # TODO 好像这里有时候会提示错误，下次使用try语句试试
                            '''
                            2023-03-15 09:54:20,368 - asyncio - ERROR - Task exception was never retrieved
                            future: <Task finished name='Task-763' coro=<listener() done, defined at 
                            /home/lensfa/sox/websocket/remote_tcp_no_socks.py:8> exception=ConnectionResetError(104, 'Connection reset by peer')>
                            Traceback (most recent call last):
                              File "/home/lensfa/sox/websocket/remote_tcp_no_socks.py", line 87, in sender
                                await socks_writer.drain()
                            ...
                            '''
                            await socks_writer.drain()

                            task_dict[websocket_id] = {
                                client_id: asyncio.create_task(
                                    listener(websocket_id, client_id, socks_reader, socks_writer))
                            }
                else:
                    # websocket连接断线,需要清空对应的任务和连接
                    reader_writer_dict = reader_writer_dict.get(websocket_id)
                    tasks = task_dict.get(websocket_id)
                    # 也有可能从未建立过连接
                    if reader_writer_dict:
                        for client_id in reader_writer_dict:
                            socks_reader, socks_writer = reader_writer_dict[client_id]
                            task = tasks[client_id]
                            socks_writer.close()
                            task.cancel()

                        reader_writer_dict[websocket_id] = {}
                        task_dict[websocket_id] = {}

        # 实测发现25ms是一个比较合适的值，10ms会导致一个核心被一直占用
        await asyncio.sleep(wait_for_cache / 1000)


async def start_tcp(tcp_to_websocket_, websocket_to_tcp_, socks_addr_, socks_port_, buffer_, wait_for_cache_):
    """
    启动TCP监听器
    将TCP收到的消息发送到远程websocket服务器
    将远程websocket服务器发送回来的消息发送到TCP客户端

    :return:
    """
    global tcp_to_websocket
    global websocket_to_tcp
    global socks_addr
    global socks_port
    global buffer
    global wait_for_cache

    tcp_to_websocket = tcp_to_websocket_
    websocket_to_tcp = websocket_to_tcp_
    socks_addr = socks_addr_
    socks_port = socks_port_
    buffer = buffer_
    wait_for_cache = wait_for_cache_

    await asyncio.gather(
        sender()
    )


# 创建一个线程锁, 存放用户连接的字典
lock = threading.Lock()

task_dict = {}
reader_writer_dict = {}
tcp_to_websocket = None
websocket_to_tcp = None

socks_addr = None
socks_port = None
buffer = None
wait_for_cache = None
