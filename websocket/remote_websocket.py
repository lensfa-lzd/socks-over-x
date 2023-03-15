import asyncio
import logging

import websockets

from script import re_pack, process_data_websocket, unpack, process_data_tcp


async def global_sender():
    # 这个函数应该是对应于全局的, 从队列中提取出多对应的消息,并将其发送到对应的websocket连接
    global tcp_to_websocket
    global websocket_dict

    while True:
        # qsize 为队列中元素的个数，而不是长度
        qsize = tcp_to_websocket.qsize()
        if qsize > 0:
            # 一次将数据全部传完，并配合sleep以减少cpu占用
            for _ in range(qsize):
                recv_data = await tcp_to_websocket.get()
                logging.info(f'Remote Websocket: Recv data from tcp cache {len(recv_data)}')
                websocket_id, client_id, data = unpack(recv_data)
                websocket = websocket_dict.get(websocket_id)
                if websocket:
                    send_data = process_data_tcp(client_id, data)
                    try:
                        await websocket.send(send_data)
                    except Exception as err:
                        logging.warning(f'Remote Websocket: ' + str(err))
                        logging.warning(f'Remote Websocket: err send data to {websocket_id}, drop data')

        # 不使用sleep的话，程序会一直监控队列，使用条件判断，占用较高的cpu
        # 实测发现25ms是一个比较合适的值，10ms会导致一个核心被一直占用
        # 考虑修改等待机制，也就当上面的执行时间超过25ms时，就不再sleep
        await asyncio.sleep(wait_for_cache / 1000)


async def handle_websocket(websocket, path):
    # 这个函数时对于单个websocket连接的
    # 也就是说, 每一个websocket连接都会启动一个独立的handle_websocket函数
    # 后续改名listener
    global websocket_dict
    global websocket_to_tcp

    websocket_addr, websocket_port = websocket.remote_address[0], str(websocket.remote_address[1])
    websocket_id = websocket_addr + websocket_port
    logging.warning('*' * 100)
    logging.warning(f'Remote Websocket: new connection from {websocket_id}')

    websocket_dict[websocket_id] = websocket

    try:
        async for message in websocket:
            # 从原始数据包中解析出客户端的id, 并将其加入连接的集合
            logging.info(f'Remote Websocket: Recv data from client {len(message)}')
            client_id, data = process_data_websocket(message)
            # websocket_client[websocket_id].add(client_id)

            # 重新封装上websocket连接的id, 并传送到缓冲区
            send_data = re_pack(websocket_id, client_id, data)
            await websocket_to_tcp.put(send_data)

    except Exception as err:
        logging.warning(f'Remote Websocket: ' + str(err))
        logging.warning(f'Remote Websocket: connection from {websocket_id} close')
        # 从字典中删除对应连接
        # None 是为方式返回异常(key error)
        _ = websocket_dict.pop(websocket_id, None)

        # 掉线处理, 应该要通知tcp端断开所有相关的连接
        # 重新封装上websocket连接的id, 并传送到缓冲区
        send_data = re_pack(websocket_id, 'None', b'close')
        await websocket_to_tcp.put(send_data)


async def start_websocket(tcp_to_websocket_, websocket_to_tcp_, websocket_port, wait_for_cache_):
    global tcp_to_websocket
    global websocket_to_tcp
    global wait_for_cache

    tcp_to_websocket = tcp_to_websocket_
    websocket_to_tcp = websocket_to_tcp_
    wait_for_cache = wait_for_cache_

    asyncio.create_task(global_sender())
    async with websockets.serve(handle_websocket, "0.0.0.0", websocket_port):
        logging.warning('Remote Websocket: listening on port ' + str(websocket_port))
        await asyncio.Future()


websocket_dict = {}
tcp_to_websocket = None
websocket_to_tcp = None
wait_for_cache = None
