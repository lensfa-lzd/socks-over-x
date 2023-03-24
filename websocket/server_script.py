import asyncio
import logging
import struct
from typing import Dict, Set, Optional

import websockets

from script import pack_data, unpack_data, unpack_socks_data

import sys
# 在windows上不支持
if 'win' not in sys.platform:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


class SocksHandler:
    def __init__(
            self,
            task_id: str,
            buffer: int,
            sender,
    ) -> None:
        # 这里应该省去sock的前三步认证的工作，直接到连接的部分
        self.task_id = task_id
        self.buffer = buffer
        self.sender = sender

        self.socks_version = 5
        self.remote_address_type = 1
        self.remote_addr = 0
        self.remote_port = 0
        self.task_recv_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.task_send_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.reader = None
        self.writer = None

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

    def task_recv(self, raw_data: bytes) -> None:
        # 如果收到了代表终止的空包，无论是正常退出还是异常退出，都应该能在后续步骤中退出循环
        self.task_recv_queue.put_nowait(raw_data)

    def task_send(self, raw_data: bytes) -> None:
        # 如果收到了代表终止的空包，无论是正常退出还是异常退出，都应该能在后续步骤中退出循环
        self.task_send_queue.put_nowait(raw_data)

    async def send_to_remote(self) -> None:
        while True:
            data = await self.task_recv_queue.get()
            logging.debug(f'SocksHandler {self.task_id}: Recv task from task queue, length {len(data)}')
            self.writer.write(data)
            await self.writer.drain()

            # 空包也应该发送
            if not data:
                break

    async def recv_from_remote(self) -> None:
        while True:
            try:
                data = await self.reader.read(1024 * self.buffer)
            except Exception as err:
                data = b''
                logging.debug(f'SocksHandler: {str(err)}')

            logging.debug(f'SocksHandler {self.task_id}: Recv task from remote, length {len(data)}')
            task = pack_data(self.task_id, data)
            self.task_send(task)

            # 空包也应该发送
            if not data:
                break

    async def send_to_websocket(self) -> None:
        while True:
            task = await self.task_send_queue.get()
            logging.debug(f'SocksHandler {self.task_id}: Send task to client handler, length {len(task)}')
            await self.sender(task)

    async def initial(self, address: str, port: int) -> bool:
        try:
            self.reader, self.writer = await asyncio.open_connection(address, port)
            logging.info(f'SocksHandler: Connect to {address}:{port} success')
            return True
        except Exception as err:
            logging.info(f'SocksHandler: {str(err)}')
            logging.info(f'SocksHandler: Connect to {address}:{port} fail')
            return False

    async def run(self):
        logging.info(f'SocksHandler {self.task_id}: Socks handler start')
        initial_task = await self.task_recv_queue.get()
        if not initial_task:
            logging.info(f'Empty data, Socks handler {self.task_id} end')
            return

        address, port = unpack_socks_data(initial_task)
        resp = await self.initial(address, port)
        if resp:
            success_reply = struct.pack("!BBBBIH", self.socks_version, 0, 0, 1, 0, 0)
            # 向客户端发送成功连接信号
            task = pack_data(self.task_id, success_reply)
            await self.sender(task)
            task_send = asyncio.create_task(self.send_to_remote())
            task_recv = asyncio.create_task(self.recv_from_remote())
            task_sendto_ws = asyncio.create_task(self.send_to_websocket())
            done, pending = await asyncio.wait([task_send, task_recv, task_sendto_ws],
                                               return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
        else:
            # 连接失败，向客户端发送失败信号
            reply = self.generate_reply(reply_code=5)
            task = pack_data(self.task_id, reply)
            await self.sender(task)

        # 不管是否正常退出，都向ClientHandler发送空包，代表Socks运行结束，请注销对应字典
        task = pack_data(self.task_id, b'')
        await self.sender(task)
        logging.info(f'SocksHandler {self.task_id}: Socks handler end')

        # 最后应该尝试关闭连接
        if self.writer is not None:
            self.writer.close()


# 每个客户端启动一个
class ClientHandler:
    def __init__(
            self,
            buffer: int,
            client_id: str,
            websocket_sender,
    ) -> None:
        self.buffer = buffer
        # 这个类对应于单个客户端， 每个客户端还可以启动多个socks连接
        self.client_id = client_id
        self.event_from_websocket: asyncio.Queue[bytes] = asyncio.Queue()

        self.websocket_sender = websocket_sender

        # 每启动一个socks连接就启动一个handler/SocksHandler类
        self.socks_handlers: Dict[str, Optional[SocksHandler]] = {}

    def event_recv(self, raw_data: bytes) -> None:
        if raw_data:
            self.event_from_websocket.put_nowait(raw_data)
        else:
            # 空包代表websocket断线，需要清空所有socks任务
            for task_id in self.socks_handlers:
                handler = self.socks_handlers.get(task_id)
                if handler:
                    # 同样，发送空包代表结束task
                    handler.task_recv(b'')
                    self.socks_handlers[task_id] = None

    async def listen_event(self) -> None:
        while True:
            event = await self.event_from_websocket.get()
            logging.debug(f'ClientHandler {self.client_id}: Recv event from websocket queue, length {len(event)}')
            # task_id可以用客户端的端口来表示（socks是一个端口对应一个连接）
            task_id, task = unpack_data(event)

            # 前者为首次连接，后者为曾经连接过但是已经结束任务
            # if task_id not in self.socks_handlers or not self.socks_handlers[task_id]:
            if task_id not in self.socks_handlers or self.socks_handlers[task_id] is None:
                self.socks_handlers[task_id] = SocksHandler(
                    task_id=task_id,
                    buffer=self.buffer,
                    sender=self.send_event,
                )
                # 启动主程序
                asyncio.ensure_future(self.socks_handlers[task_id].run())

            handler = self.socks_handlers[task_id]
            handler.task_recv(task)

    async def send_event(self, event: bytes) -> None:
        await self.websocket_sender(self.client_id, event)

    async def run(self) -> None:
        logging.warning(f'ClientHandler {self.client_id}: Client handler start')
        await asyncio.gather(
            self.listen_event()
        )


# 一个客户端可以拥有多个websocket连接
# client id是全局唯一的
class WebsocketServer:
    def __init__(
            self,
            port: int,
            buffer: int,
            clients: list,
    ) -> None:
        self.port = port
        self.buffer = buffer
        # 合法的客户端
        self.clients = clients

        self.client_handlers: Dict[str, ClientHandler] = {}
        # 记录每一个唯一client id所包含的websocket连接的id，方便一个客户端通过不同的websocket连接发送数据
        self.client_register: Dict[str, Set[str]] = {}
        self.websocket_register: Dict = {}

        # websocket id与client id的对应关系表
        self.websocket_client: Dict[str, str] = {}

        # websocket id与task id的对应关系表
        self.task_websocket: Dict[str, str] = {}
        self.websocket_task: Dict[str, Set[str]] = {}

    async def serve(self) -> None:
        async with await websockets.serve(
                # 定义handler函数
                self.websocket_handler,
                # 定义监听地址，None为监听所有地址
                None,
                # 定义端口
                self.port,
                compression=None,
        ):
            logging.warning(f'WebsocketServer: Websocket listening on port {self.port}')
            await asyncio.Future()

    async def websocket_handler(self, websocket) -> None:
        # 每一个websocket连接都会启动一个
        websocket_addr, websocket_port = websocket.remote_address[0], str(websocket.remote_address[1])
        websocket_id = websocket_addr + websocket_port
        logging.info('*' * 100)
        logging.warning(f'WebsocketServer: New websocket connection from {websocket_id}')

        # 注册websocket连接
        self.websocket_register[websocket_id] = websocket

        try:
            async for event in websocket:
                if not event:
                    # 不对空包进行处理
                    continue

                try:
                    client_id, task = unpack_data(event)
                    task_id, task_data = unpack_data(task)
                except Exception as err:
                    logging.debug(f'WebsocketServer: ' + str(err))
                    logging.info(f'WebsocketServer: Illegal pkg')

                    await self.error_reply(websocket_id, websocket)
                    continue

                # 用前缀表示一组客户端: sox-001，sox-002
                prefix_id, client_name = client_id.split('-', 1)
                if prefix_id not in self.clients:
                    logging.info(f'WebsocketServer: Illegal client')
                    await self.error_reply(websocket_id, websocket)
                    continue

                logging.debug(f'WebsocketServer: Recv event from client {client_id}, length {len(event)}')

                # 一个全新的客户端连接
                if client_id not in self.client_handlers:
                    # 对唯一的客户端启动一个全新的类
                    self.client_handlers[client_id] = ClientHandler(
                        buffer=self.buffer,
                        client_id=client_id,
                        websocket_sender=self.send,
                    )
                    # 相当于启动对应类中的主程序
                    asyncio.ensure_future(self.client_handlers[client_id].run())

                    self.client_register[client_id] = set()

                # 新连接和再次连接都应该注册对照表
                self.websocket_client[websocket_id] = client_id
                self.client_register[client_id].add(websocket_id)

                if task_id not in self.task_websocket:
                    # 将task id与对应的websocket id记录在对应表中
                    self.task_websocket[task_id] = websocket_id

                if websocket_id not in self.websocket_task:
                    self.websocket_task[websocket_id] = set()

                self.websocket_task[websocket_id].add(task_id)

                handler = self.client_handlers[client_id]
                handler.event_recv(task)


        except Exception as err:
            # websocket 连接已经断开, 异常退出
            logging.info(f'WebsocketServer: ' + str(err))

        # 无论是异常退出还是正常退出都会执行
        await self.close(websocket_id)

    async def send(self, client_id: str, task: bytes) -> None:
        # 每个task会绑定一个websocket id，也就是一直使用同一个websocket id进行传输

        # websocket_ids_sets = self.client_register.get(client_id)
        # websocket_ids = []
        # for websocket_id in websocket_ids_sets:
        #     websocket_ids.append(websocket_id)

        task_id, task_data = unpack_data(task)
        ws_id = self.task_websocket.get(task_id, None)
        if ws_id:
            ws = self.websocket_register.get(ws_id, None)
            if ws:
                try:
                    await ws.send(task)
                except Exception as err:
                    logging.debug(f'WebsocketServer: ' + str(err))
                    logging.debug(f'WebsocketServer: Drop task: {client_id}, length {len(task)}')
            else:
                logging.debug(f'WebsocketServer: Drop task: {client_id}, length {len(task)}')

        else:
            logging.debug(f'WebsocketServer: Drop task: {client_id}, length {len(task)}')

        # if len(websocket_ids) != 0:
        #     # 随机抽取客户端
        #     websocket_id = choice(websocket_ids)
        #     ws = self.websocket_register.get(websocket_id)
        #     if ws:
        #         try:
        #             await ws.send(task)
        #         except Exception as err:
        #             logging.debug(f'WebsocketServer: ' + str(err))
        #             logging.debug(f'WebsocketServer: Drop task: {client_id}, length {len(task)}')
        #     else:
        #         logging.debug(f'WebsocketServer: Drop task: {client_id}, length {len(task)}')
        #
        # else:
        #     # 客户端断线，丢弃数据
        #     logging.debug(f'WebsocketServer: Drop task: {client_id}, length {len(task)}')

    async def error_reply(self, websocket_id: str, ws) -> None:
        # 对异常包进行回复
        await ws.send('hello word')
        await ws.send(b'')
        await ws.close()
        _ = self.websocket_register.pop(websocket_id, None)

    async def close(self, websocket_id: str) -> None:
        # 关闭websocket一个连接
        _ = self.websocket_register.pop(websocket_id, None)
        client_id = self.websocket_client.get(websocket_id)

        logging.warning(f'WebsocketServer: connection at {websocket_id} with client {client_id} close')
        if client_id:
            # 排除这个websocket连接/将连接标记为不可用
            self.client_register[client_id].discard(websocket_id)
            # 也要从对照表中排除
            _ = self.websocket_client.pop(websocket_id, None)

            task_ids = self.websocket_task.pop(websocket_id, None)

            handler = self.client_handlers[client_id]

            if task_ids:
                for task_id in task_ids:
                    _ = self.task_websocket.pop(task_id, None)
                    task = pack_data(task_id, b'')
                    # 只断开对应的task任务即可
                    handler.event_recv(task)

            # if len(self.client_register[client_id]) == 0:
            #     # 不等待直接清空数据
            #     handler = self.client_handlers[client_id]
            #     handler.event_recv(b'')

        else:
            # 说明该websocket连接从未传来过信息，不用处理
            pass
