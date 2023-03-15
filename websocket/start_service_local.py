import asyncio
import logging

import websockets

from local_tcp import start_tcp


async def listener():
    """
    获取连接客户端的地址
    监听客户端发送的消息
    # 给客户端发送消息由另一个模块执行
    将socks获得的数据送入队列

    cache: 一个队列，用于缓存数据
    :return:
    """
    global remote_websocket
    global websocket_to_tcp

    while True:
        recv_data = await remote_websocket.recv()
        logging.info(f'Local Websocket: Recv data from remote {len(recv_data)}')
        await websocket_to_tcp.put(recv_data)
        # print(f'Local Websocket: Put data to websocket cache {len(recv_data)}')


async def sender():
    global remote_websocket
    global tcp_to_websocket

    while True:
        # qsize 为队列中元素的个数，而不是长度
        qsize = tcp_to_websocket.qsize()
        if qsize > 0:
            # 一次将数据全部传完，并配合sleep以减少cpu占用
            for _ in range(qsize):
                recv_data = await tcp_to_websocket.get()
                logging.info(f'Local Websocket: Recv data from tcp cache {len(recv_data)}')
                # print(f'Local Websocket: Recv data from tcp cache {len(recv_data)}')
                await remote_websocket.send(recv_data)

        # 不使用sleep的话，程序会一直监控队列，使用条件判断，占用较高的cpu
        # 实测发现25ms是一个比较合适的值，10ms会导致一个核心被一直占用
        await asyncio.sleep(wait_for_cache/1000)


async def start():
    """
    将所有需要启动的线程在这里一起启动
    :return:
    """
    global tcp_to_websocket
    global websocket_to_tcp
    global remote_websocket
    global client_writer_dict

    # 第一次连接
    logging.info('Try to connect to remote websocket')
    while True:
        try:
            remote_websocket = await websockets.connect(uri)
            break
        except:
            logging.warning(f"1 Try ..., Wait for 1 seconds before retrying...")
            await asyncio.sleep(1)

    while True:
        try:
            task_tcp = asyncio.create_task(start_tcp(
                tcp_to_websocket, websocket_to_tcp, client_writer_dict, buffer, wait_for_cache))
            task_websocket_listener = asyncio.create_task(listener())
            task_websocket_sender = asyncio.create_task(sender())
            tasks = [task_tcp, task_websocket_listener, task_websocket_sender]
            await asyncio.gather(*tasks)
        except Exception as err:
            # 断线中断所有任务, 并删除所有socks连接
            logging.warning('Local Websocket: ' + str(err))
            logging.warning('Local Websocket: Websocket connection close')
            for task in tasks:
                try:
                    task.cancel()
                except:
                    continue
            await asyncio.gather(*tasks, return_exceptions=True)

            close_list = []
            for socks_task_close in client_writer_dict:
                close_list.append(socks_task_close)
            for socks_task_close in close_list:
                # 字典的pop()方法从tasks字典中删除相应的任务
                # None 是为方式返回异常(key error)
                socks_task = client_writer_dict.pop(socks_task_close, None)
                if socks_task:
                    socks_task.close()

            # 阻塞到连上为止
            logging.warning('Try to reconnect to remote websocket')
            await asyncio.sleep(10 / 1000)
            while True:
                try:
                    remote_websocket = await websockets.connect(uri)
                    logging.warning('2 Connection Success')

                    tcp_to_websocket = asyncio.Queue()
                    websocket_to_tcp = asyncio.Queue()
                    break
                except:
                    await asyncio.sleep(1)
                    logging.warning(f"2 Try ..., Wait for 1 seconds before retrying...")


if __name__ == '__main__':
    # 直接在终端输出
    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # 单次数据包的最大大小
    buffer = 16
    wait_for_cache = 25
    remote_websocket = None
    uri = "ws://192.168.35.85:9000/handle_websocket"

    # 创建队列对象
    tcp_to_websocket = asyncio.Queue()
    websocket_to_tcp = asyncio.Queue()

    # 存放用户连接的字典
    client_writer_dict = {}
    asyncio.run(start())