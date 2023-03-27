import asyncio
import logging
import yaml

from script.client_script import SocksServer


# TODO 在客户端添加指定ipv4连接的选项
# task = task_id + task_data
async def main(
        port: int,
        buffer: int,
        client_id: str,
        websocket_uri: str,
        retry: int,
        time_out: int,
) -> None:
    server = SocksServer(
        port=port,
        buffer=buffer,
        client_id=client_id,
        websocket_uri=websocket_uri,
        retry=retry,
        time_out=time_out,
    )

    # 相当于阻塞
    await server.serve()


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.WARNING
    )

    with open('config.yaml', 'r', encoding='utf-8') as file:
        cfg = yaml.safe_load(file)['local']

    try:
        asyncio.run(
            main(
                port=cfg['server_port'],
                buffer=cfg['buffer'],
                client_id=cfg['client_id'],
                websocket_uri=cfg['websocket_uri'],
                retry=cfg['retry'],
                time_out=cfg['time_out'],
            )
        )
    except KeyboardInterrupt:
        pass
