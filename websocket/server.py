import asyncio
import logging
import yaml

from server_script import WebsocketServer


# TODO 在服务器参数中设置compression=None, 也就是禁用 WebSocket 消息压缩，并且默认启用TCP_NODELAY选项
# 之后，极大地增加了服务器的带宽，单线程可以跑到700Mbps，完全满足日常需求
# 同时，多条websocket同时连接并不能获得明显的速度提升。于是在下个版本计划移除多条websocket连接的功能
# 一个客户端只发起一条连接

# task = task_id + task_data
async def main(
        port: int,
        buffer: int,
        clients: list
) -> None:
    server = WebsocketServer(
        port=port,
        buffer=buffer,
        clients=clients,
    )

    # 相当于阻塞
    await server.serve()


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.WARNING
    )

    with open('test_cfg.yaml', 'r', encoding='utf-8') as file:
        cfg = yaml.safe_load(file)['remote']

    try:
        asyncio.run(
            main(
                port=cfg['websocket_port'],
                buffer=cfg['buffer'],
                clients=cfg['clients']
            )
        )
    except KeyboardInterrupt:
        pass
