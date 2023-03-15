import asyncio
import logging

from remote_websocket import start_websocket
from remote_tcp_no_socks import start_tcp


async def start():
    """
    将所有需要启动的线程在这里一起启动
    :return:
    """
    await asyncio.gather(
        start_websocket(tcp_to_websocket, websocket_to_tcp, websocket_port, wait_for_cache),
        start_tcp(tcp_to_websocket, websocket_to_tcp, socks_addr, socks_port, buffer, wait_for_cache)
    )


if __name__ == '__main__':
    # 直接在终端输出
    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # 创建队列对象
    tcp_to_websocket = asyncio.Queue()
    websocket_to_tcp = asyncio.Queue()
    socks_addr = '192.168.35.99'
    socks_port = 1080
    websocket_port = 9000
    # 单次数据包的最大大小
    buffer = 16
    wait_for_cache = 25

    # 存放用户连接的字典
    client_writer_dict = {}
    asyncio.run(start())
