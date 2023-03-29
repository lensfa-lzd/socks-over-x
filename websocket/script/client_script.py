import asyncio
import logging
import socket
import struct
import sys
import time
from typing import Dict, List

import websockets

from random import choice
from script.script import pack_data, unpack_data

# 在windows上不支持
if 'win' not in sys.platform:
    try:
        import uvloop
        uvloop.install()
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except Exception as err_:
        logging.info(str(err_))


class WebsocketHandler:
    def __init__(
            self,
            uris: List[str],
            retry: int,
            time_out: int,
            client_id: str,
            send_to_client,
            network: List[int]
    ) -> None:
        self.retry = retry
        self.uris = uris
        self.time_out = time_out
        self.client_id = client_id
        self.network = network

        self.last_event_time = time.time()
        self.send_to_client = send_to_client

        self.task_send_queue: asyncio.Queue = asyncio.Queue()
        self.task_cache: asyncio.Queue[bytes] = asyncio.Queue()

        # 初始化状态
        self.websocket = None
        self.sleep: bool = False

    def task_recv(self, task: bytes) -> None:
        self.last_event_time = time.time()

        if not self.sleep:
            if self.websocket is not None:
                # 一切正常
                logging.debug(f'WebsocketHandler: Recv task from socks server, length {len(task)}')
                self.task_send_queue.put_nowait(task)

            else:
                # 这里交由task_cache处理，断线应该直接拒绝数据，并返回空包表示连接断开
                self.task_cache.put_nowait(task)
                logging.debug(f'WebsocketHandler: No valid websocket connection, so pause data, length {len(task)}')

        else:
            # sleep状态下不对空包作相应, 应该是对task_data进行判断
            task_id, task_data = unpack_data(task)
            if task_data:
                logging.debug(f'WebsocketHandler: Websocket handler sleep, so pause data, length {len(task)}')
                self.task_cache.put_nowait(task)

    async def connect(self) -> None:
        num_retry = self.retry
        # 在已经连接上的情况下自动跳过
        while num_retry != 0 and self.websocket is None:
            uri = choice(self.uris)
            try:
                # 自动ipv6优先，前提是服务端要开启
                # headers = {'Authorization': 'Bearer my_token'} # 可以自定义header
                # ws = await websockets.connect(self.uri, compression=None, extra_headers=headers)

                if 4 in self.network and 6 in self.network:
                    ws = await websockets.connect(uri, compression=None)
                elif 4 in self.network:
                    ws = await websockets.connect(uri, compression=None, family=socket.AF_INET)
                elif 6 in self.network:
                    ws = await websockets.connect(uri, compression=None, family=socket.AF_INET6)
                else:
                    logging.warning(f"WebsocketHandler: Network setting error.")
                    raise SyntaxError

                # 远程连接认证
                await ws.send(self.client_id.encode('UTF-8'))
                auth_status = await ws.recv()
                if auth_status != b'OK':
                    raise ConnectionError("Websocket服务端认证失败")
                logging.warning(f"WebsocketHandler: Connect to {uri} success.")
                self.websocket = ws
                break
            except Exception as err:
                num_retry = num_retry - 1
                logging.info(str(err))
                logging.warning(f"WebsocketHandler: Connect to {uri} fail.")
                self.websocket = None
                await asyncio.sleep(1)

    async def listen_task(self) -> None:
        # TODO 接受数据的校验？ 用在客户端认证失败等场景
        while True:
            try:
                task = await self.websocket.recv()
                logging.debug(f'WebsocketHandler: Recv data from remote, length {len(task)}')
            except Exception as err:
                logging.info(str(err))
                logging.warning(f"WebsocketHandler: Websocket connection lost.")
                self.websocket = None
                return

            # 接受event应该也影响睡眠时间
            self.last_event_time = time.time()
            task_id, task_data = unpack_data(task)
            self.send_to_client(task_id, task_data)

    async def send_task(self) -> None:
        while True:
            send_task = await self.task_send_queue.get()
            try:
                await self.websocket.send(send_task)
                logging.debug(f'WebsocketHandler: Send task to remote, length {len(send_task)}')
            except Exception as err:
                logging.info(str(err))
                logging.warning(f"WebsocketHandler: Websocket connection lost.")
                self.websocket = None

                # event_id, event = unpack_data(send_event)
                task_id, task = unpack_data(send_task)
                self.send_to_client(task_id, b'')
                break

    async def run_sub(self) -> None:
        # websocket 子任务
        if self.websocket is not None:
            # 已经连接成功
            task_send = asyncio.create_task(self.send_task())
            task_listen = asyncio.create_task(self.listen_task())

            done, pending = await asyncio.wait([task_send, task_listen], return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()

            # 表明websocket连接断开，需要关闭当前的task
            self.send_to_client(b'', b'')

    async def goto_sleep(self):
        self.sleep = True
        # 进入睡眠，告知客户端断开连接
        self.send_to_client(b'', b'')

        logging.warning(f'WebsocketHandler: No task after {self.time_out} s, so sleep.')

        try:
            # 尽管好像websocket好像可以正常接收空包
            # 但是在正常关闭前发送空包可以显著会减少异常
            await self.websocket.send(b'')
            await self.websocket.close()
            logging.debug(f'WebsocketHandler: websocket connection close')
        except Exception as err:
            logging.debug(f'WebsocketHandler: {str(err)}')

        self.websocket = None

    async def wake_up(self):    
        logging.warning(f'WebsocketHandler: Wake up.')
        await self.connect()
        asyncio.ensure_future(self.run_sub())
        await asyncio.sleep(1)
        self.sleep = False

        # 快速处理积压任务
        for _ in range(self.task_cache.qsize()):
            pause_event = await self.task_cache.get()
            self.task_recv(pause_event)

    async def run(self) -> None:
        await self.connect()
        asyncio.ensure_future(self.run_sub())

        while True:
            silent_time = time.time() - self.last_event_time

            if self.task_cache.qsize() > 0:
                if self.sleep:
                    await self.wake_up()
                    # 处理被挂起的数据
                else:
                    await self.process_pause_event()
            else:
                # 进入睡眠状态
                if silent_time >= self.time_out and not self.sleep:
                    await self.goto_sleep()

            # 这个不用太频繁执行
            await asyncio.sleep(0.1)

    async def process_pause_event(self) -> None:
        task = await self.task_cache.get()

        if self.websocket is None:
            # websocket 断开尝试重连
            await self.connect()
            if self.websocket is not None:
                # 连接成功
                asyncio.ensure_future(self.run_sub())
                await asyncio.sleep(1)
                self.task_recv(task)
            else:
                # 重连失败丢弃数据
                # client_id, event_data = unpack_data(event)
                task_id, task_data = unpack_data(task)
                self.send_to_client(task_id, b'')
                logging.debug(f'WebsocketHandler: Fail to process task {task_id}, so drop task, length {len(task_data)}')
        else:
            # 已经有活跃的连接，直接送至远程处理
            self.task_recv(task)


class SocksServer:
    def __init__(
            self,
            port: int,
            buffer: int,
            client_id: str,
            websocket_uris: List[str],
            retry: int,
            time_out: int,
            network: List[int]
    ) -> None:
        self.port = port
        self.buffer = buffer
        self.client_id = client_id

        self.socks_version = 5

        # 以task id（端口号）存储对应的reader和writer
        self.socks_tasks: Dict[str] = {}
        self.tasks_from_remote: Dict[str, asyncio.Queue[bytes]] = {}

        self.websocket_connection: WebsocketHandler = WebsocketHandler(
            retry=retry,
            time_out=time_out,
            uris=websocket_uris,
            client_id=client_id,
            send_to_client=self.task_recv,
            network=network
        )

    def generate_reply(self, reply_code: int) -> bytes:
        # 0x00表示成功
        # 0x01普通SOCKS服务器连接失败
        # 0x02现有规则不允许连接
        # 0x03网络不可达
        # 0x04主机不可达
        # 0x05连接被拒
        # 0x06 TTL超时
        # 0x07不支持的命令
        # 0x08不支持的地址类型
        # 0x09 - 0xFF未定义

        # 连接失败时一般回应5，连接被拒
        # 注意：按照标准协议，返回的应该是对应的address_type，但是实际测试发现，当address_type=3，
        # 也就是说是域名类型时，会出现卡死情况，但是将address_type该为1，则不管是IP类型和域名类型都能正常运行
        return struct.pack("!BBBBIH", self.socks_version, reply_code, 0, 1, 0, 0)

    def task_recv(self, task_id: str, task_data: bytes) -> None:
        if task_id:
            queue = self.tasks_from_remote.get(task_id)
            if queue:
                queue.put_nowait(task_data)
            else:
                logging.debug(f'SocksServer: Drop task: {task_id}, length {len(task_data)}')

                # 连接中断，向远程websocket服务器发送空包，以断开对应的连接
                task = pack_data(task_id, b'')
                self.websocket_connection.task_recv(task)
        else:
            # websocket连接进入睡眠状态或者异常断开
            for task_id in self.socks_tasks:
                queue = self.tasks_from_remote.get(task_id)
                if queue:
                    queue.put_nowait(b'')

    async def serve(self) -> None:
        server = await asyncio.start_server(
            # 定义handler函数
            self.socks_handler,
            # 定义监听地址，None为监听所有地址
            None,
            # 定义端口
            self.port
        )

        addr = server.sockets[0].getsockname()
        logging.warning(f'SocksServer: Serving on {addr}')

        # 启动websocket handler
        asyncio.ensure_future(self.websocket_connection.run())

        async with server:
            await server.serve_forever()

    async def socks_handler(self, reader, writer) -> None:
        # 每个socks连接都会启动一个
        client_addr_info = writer.get_extra_info('peername')
        logging.info(f"SocksServer: Received connection from {client_addr_info}")
        client_addr, client_port = client_addr_info[0], str(client_addr_info[1])

        # 将地址+端口作为task id
        task_id = client_addr + client_port

        # 向字典注册任务
        self.socks_tasks[task_id] = writer

        # 注册发送队列
        self.tasks_from_remote[task_id] = asyncio.Queue()

        # 开始处理socks连接的认证
        send_data = await socks_auth(reader, writer)

        if send_data:
            # 认证成功, 启动发送协程
            sender_task = asyncio.ensure_future(self.socks_sender(task_id))

            task = pack_data(task_id, send_data)

            logging.debug(f"SocksServer: Auth success, send connection info to websocket handler")
            self.websocket_connection.task_recv(task)
            # 发送任务由WebsocketHandler调用
            while True:
                try:
                    # 监听客户端发送的消息并将其发送到远程服务器
                    recv_data = await reader.read(1024 * self.buffer)
                    logging.debug(f'SocksServer: Recv data from client {task_id}, length {len(recv_data)}')
                except Exception as err:
                    logging.debug(f'SocksServer: {str(err)}')
                    logging.info(f'SocksServer: Client {task_id} connection close')
                    # 连接中断，向远程websocket服务器发送空包，以断开对应的连接
                    recv_data = b''

                task = pack_data(task_id, recv_data)
                self.websocket_connection.task_recv(task)

                if not recv_data:
                    # 收到空包，代表任务结束
                    sender_task.cancel()
                    break
        else:
            # 认证失败，向客户端发送回复
            reply = self.generate_reply(reply_code=5)
            try:
                # 有可能writer在socks_auth中已经关闭
                writer.write(reply)
                await writer.drain()
            except Exception as err:
                logging.debug(f'SocksServer: {str(err)}')

        # 认证失败或者任务结束
        # 需要关闭writer并向注销任务(writer.close()可以多次执行)
        _ = self.socks_tasks.pop(task_id, None)
        _ = self.tasks_from_remote.pop(task_id, None)
        writer.close()

    async def socks_sender(self, task_id: str) -> None:
        # 每一个task都应该启动一个
        queue = self.tasks_from_remote.get(task_id)
        writer = self.socks_tasks.get(task_id)
        if writer is not None and queue is not None:
            while True:
                task_data = await queue.get()
                logging.debug(f'SocksServer: Send data to {task_id}, length {len(task_data)}')
                writer.write(task_data)
                await writer.drain()

                if not task_data:
                    break

            # 需要关闭writer并向注销任务
            _ = self.socks_tasks.pop(task_id, None)
            _ = self.tasks_from_remote.pop(task_id, None)
            writer.close()
        logging.info(f'SocksServer: Task {task_id} end')


async def socks_auth(reader, writer) -> bytes:
    socks_version = 5
    # 协商
    # 从客户端读取并解包两个字节的数据
    header = await reader.read(2)

    try:
        version, nmethods = struct.unpack("!BB", header)
        if version != socks_version or not nmethods > 0:
            writer.close()
            return b''
    except Exception as err:
        logging.debug(f'SocksServer: {str(err)}')
        writer.close()
        return b''

    # 接受支持的方法
    methods = []
    for i in range(nmethods):
        method = await reader.read(1)
        methods.append(ord(method))

    # 无需认证
    if 0 not in set(methods):
        writer.close()
        return b''

    # 发送协商响应数据包
    writer.write(struct.pack("!BB", socks_version, 0))
    await writer.drain()

    # 请求
    res_data = await reader.read(4)

    try:
        version, cmd, _, address_type = struct.unpack("!BBBB", res_data)
    except Exception as err:
        logging.debug(f'SocksServer: {str(err)}')
        writer.close()
        return b''

    if cmd == 1:
        if address_type == 1:  # IPv4
            res_data = await reader.read(4)
            send_data = bytes([address_type]) + res_data
        elif address_type == 3:  # Domain name
            res_data = await reader.read(1)
            domain_length = res_data[0]
            res_data = await reader.read(domain_length)
            send_data = bytes([address_type]) + bytes([domain_length]) + res_data
        elif address_type == 4:  # IPv6
            res_data = await reader.read(16)
            send_data = bytes([address_type]) + res_data
        else:
            writer.close()
            return b''
    else:
        # await writer.close()
        writer.close()
        return b''

    port_data = await reader.read(2)

    return send_data + port_data
