import asyncio
import logging
import struct
import time
from random import choice
from typing import Dict, List, Set

import websockets

from script import pack_data, unpack_data


class WebsocketHandler:
    def __init__(
            self,
            # 同时保有连接的数量
            uri: str,
            connections: int,
            retry: int,
            time_out: int,
            send_to_client,
    ) -> None:
        self.retry = retry
        self.uri = uri
        self.time_out = time_out
        self.num_connections = connections

        self.last_event_time = time.time()
        self.send_to_client = send_to_client

        # ws_id 即为列表的下标
        self.ws_status: List[bool] = []
        self.ws_ids: List[int] = []
        self.ws_connections: Dict = {}

        # websocket id与task id的对应关系表
        self.task_websocket: Dict[str, int] = {}
        self.websocket_task: Dict[int, Set[str]] = {}

        self.event_send_queue: Dict[int, asyncio.Queue[bytes]] = {}

        self.sleep = False
        self.event_cache: asyncio.Queue[bytes] = asyncio.Queue()

        self.sub_task = []

        # 初始化状态
        for i in range(connections):
            self.ws_status.append(False)
            self.ws_ids.append(i)
            self.event_send_queue[i] = asyncio.Queue()
            self.websocket_task[i] = set()

    def event_recv(self, event: bytes) -> None:
        # 每个task会绑定一个websocket id，也就是一直使用同一个websocket id进行传输
        self.last_event_time = time.time()
        client_id, event_data = unpack_data(event)
        task_id, task = unpack_data(event_data)

        if not self.sleep:
            ws_id_valid = [i for i in range(len(self.ws_status)) if self.ws_status[i]]
            if ws_id_valid:
                # 一切正常
                if task_id not in self.task_websocket:
                    ws_id = choice(ws_id_valid)
                    self.task_websocket[task_id] = ws_id
                    self.websocket_task[ws_id].add(task_id)

                ws_id = self.task_websocket.get(task_id, None)
                logging.debug(f'1 WebsocketServer: Drop task: {ws_id}')
                if ws_id is not None:
                    logging.debug(f'WebsocketHandler: Recv data from socks server, length {len(event)}')
                    self.event_send_queue[ws_id].put_nowait(event)
                else:
                    logging.debug(f'WebsocketServer: Drop task: {task_id}, length {len(task)}')

                if len(ws_id_valid) < self.num_connections:
                    # 部分正常，给主程序发送信息，告知有websocket断线
                    logging.info(f'WebsocketHandler: Some websocket connection lose, try to reconnect')
                    self.event_cache.put_nowait(b'')
            else:
                # 这里交由event_cache处理，断线应该直接拒绝数据，并返回空包表示连接断开
                self.event_cache.put_nowait(event)
                logging.debug(f'WebsocketHandler: No valid websocket connection, so pause data, length {len(event)}')

        else:
            # sleep状态下不对空包作相应
            if task:
                logging.debug(f'WebsocketHandler: Websocket handler sleep, so pause data, length {len(event)}')
                self.event_cache.put_nowait(event)

    async def connect(self, ws_id: int) -> None:
        num_retry = self.retry
        # 在已经连接上的情况下自动跳过
        while num_retry != 0 and self.ws_status[ws_id] is False:
            try:
                # 自动ipv6优先，前提是服务端要开启
                ws = await websockets.connect(self.uri, compression=None)
                logging.info(f"WebsocketHandler: Connect to {self.uri} success.")
                self.ws_status[ws_id] = True
                self.ws_connections[ws_id] = ws
                break
            except Exception as err:
                num_retry = num_retry - 1
                logging.info(str(err))
                logging.info(f"WebsocketHandler: Connect to {self.uri} fail.")
                self.ws_connections[ws_id] = None
                await asyncio.sleep(1)

    async def start_connect(self, ws_ids: list) -> None:
        connect_task = []
        for ws_id in ws_ids:
            connect_task.append(asyncio.create_task(self.connect(ws_id)))
        await asyncio.gather(*connect_task)

    async def listen_event(self, ws_id: int, websocket) -> None:
        # TODO 接受数据的校验？
        while True:
            try:
                task = await websocket.recv()
                logging.debug(f'WebsocketHandler: Recv data from remote, length {len(task)}')
            except Exception as err:
                logging.info(str(err))
                logging.warning(f"WebsocketHandler: Websocket connection {ws_id} lost.")
                self.ws_connections[ws_id] = None
                self.ws_status[ws_id] = False
                return

            # 接受event应该也影响睡眠时间
            self.last_event_time = time.time()
            task_id, task_data = unpack_data(task)
            self.send_to_client(task_id, task_data)

    async def send_event(self, ws_id: int, websocket) -> None:
        queue = self.event_send_queue[ws_id]
        # logging.warning(f'2 {str(self.ws_status)}')
        while True:
            send_event = await queue.get()
            try:
                await websocket.send(send_event)
                logging.debug(f'WebsocketHandler: Send data to remote, length {len(send_event)}')
            except Exception as err:
                logging.info(str(err))
                logging.warning(f"WebsocketHandler: Websocket connection {ws_id} lost.")
                self.ws_connections[ws_id] = None
                self.ws_status[ws_id] = False

                event_id, event = unpack_data(send_event)
                task_id, task = unpack_data(event)
                self.send_to_client(task_id, b'')
                break

    async def run_sub(self, ws_id: int) -> None:
        # websocket 子任务
        if self.ws_status[ws_id]:
            # 已经连接成功
            ws = self.ws_connections[ws_id]
            task_send = asyncio.create_task(self.send_event(ws_id, ws))
            task_listen = asyncio.create_task(self.listen_event(ws_id, ws))

            done, pending = await asyncio.wait([task_send, task_listen], return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()

            # 表明一个websocket连接断开，需要关闭绑定的task
            # self.send_to_client(b'', b'')
            task_ids = self.websocket_task.get(ws_id, None)
            if task_ids:
                for task_id in task_ids:
                    _ = self.task_websocket.pop(task_id, None)
                    # 只断开对应的task任务即可
                    self.send_to_client(task_id, b'')

            # 重置对照表
            self.websocket_task[ws_id] = set()

    async def goto_sleep(self):
        self.sleep = True
        # 进入睡眠，告知客户端断开连接
        self.send_to_client(b'', b'')

        logging.warning(f'WebsocketHandler: No task after {self.time_out} s, so sleep.')
        for ws_id in self.ws_ids:
            try:
                ws = self.ws_connections[ws_id]
                # 尽管好像websocket好像可以正常接收空包
                # 但是在正常关闭前发送空包可以显著会减少异常
                await ws.send(b'')
                await ws.close()

                logging.debug(f'WebsocketHandler: websocket connection {ws_id} close')
            except Exception as err:
                logging.debug(f'WebsocketHandler: {str(err)}')

            self.ws_connections[ws_id] = None
            # 先关闭连接再取消任务，不然会有奇怪的bug
            self.sub_task[ws_id].cancel()
            self.ws_status[ws_id] = False

    async def wake_up(self):
        await self.start_connect(self.ws_ids)
        for ws_id in self.ws_ids:
            self.sub_task.append(asyncio.ensure_future(self.run_sub(ws_id)))

            await asyncio.sleep(1)
            self.sleep = False
            logging.warning(f'WebsocketHandler: Wake up')

            for _ in range(self.event_cache.qsize()):
                pause_event = await self.event_cache.get()
                self.event_recv(pause_event)

    async def run(self) -> None:
        await self.start_connect(self.ws_ids)
        for ws_id in self.ws_ids:
            self.sub_task.append(asyncio.ensure_future(self.run_sub(ws_id)))

        while True:
            silent_time = time.time() - self.last_event_time

            if self.event_cache.qsize() > 0:
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
        event = await self.event_cache.get()

        if not event:
            # 部分websocket连接断线
            ws_id_invalid = [i for i in range(len(self.ws_status)) if not self.ws_status[i]]
            await self.start_connect(ws_id_invalid)
            for ws_id in ws_id_invalid:
                self.sub_task[ws_id] = asyncio.ensure_future(self.run_sub(ws_id))
        else:
            if True not in self.ws_status:
                await self.start_connect(self.ws_ids)
                for ws_id in self.ws_ids:
                    self.sub_task.append(asyncio.ensure_future(self.run_sub(ws_id)))
                await asyncio.sleep(1)

            if True in self.ws_status:
                self.event_recv(event)
            else:
                client_id, event_data = unpack_data(event)
                task_id, task = unpack_data(event_data)
                self.send_to_client(task_id, b'')
                logging.debug(f'WebsocketHandler: Fail to process task {task_id}, so drop task, length {len(task)}')


class SocksServer:
    def __init__(
            self,
            port: int,
            buffer: int,
            client_id: str,
            websocket_uri: str,
            num_ws_connections: int,
            retry: int,
            time_out: int,
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
            connections=num_ws_connections,
            uri=websocket_uri,
            send_to_client=self.task_recv
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
                task_data = pack_data(task_id, b'')
                event = pack_data(self.client_id, task_data)
                self.websocket_connection.event_recv(event)
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

            task_data = pack_data(task_id, send_data)
            event = pack_data(self.client_id, task_data)

            logging.debug(f"SocksServer: Auth success, send connection info to websocket handler")
            self.websocket_connection.event_recv(event)
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

                task_data = pack_data(task_id, recv_data)
                event = pack_data(self.client_id, task_data)
                self.websocket_connection.event_recv(event)

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
            await close_writer(writer)
            return b''
    except Exception as err:
        logging.debug(f'SocksServer: {str(err)}')
        await close_writer(writer)
        return b''

    # 接受支持的方法
    methods = []
    for i in range(nmethods):
        method = await reader.read(1)
        methods.append(ord(method))

    # 无需认证
    if 0 not in set(methods):
        await close_writer(writer)
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
        await close_writer(writer)
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
            await close_writer(writer)
            return b''
    else:
        await close_writer(writer)
        return b''

    port_data = await reader.read(2)

    return send_data + port_data


async def close_writer(writer) -> None:
    try:
        await writer.close()
    except Exception as err:
        logging.debug(f'SocksServer: {str(err)}')
