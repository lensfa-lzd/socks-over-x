import asyncio
import logging
import yaml

from server_script import WebsocketServer


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
