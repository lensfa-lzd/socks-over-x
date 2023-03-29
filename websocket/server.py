import argparse
import asyncio
import yaml

from typing import List
from script.script import log_level
from script.server_script import WebsocketServer


# task = task_id + task_data
async def main(
        port: int,
        buffer: int,
        clients: list,
        network: List[int]
) -> None:
    server = WebsocketServer(
        port=port,
        buffer=buffer,
        clients=clients,
        network=network
    )

    # 相当于阻塞
    await server.serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Sox websocket server')
    parser.add_argument('--config', type=str, default='config.yaml', help='config file location')
    args = parser.parse_args()

    with open(args.config, 'r', encoding='utf-8') as file:
        cfg = yaml.safe_load(file)['remote']

    log_level(cfg['log_level'])

    try:
        asyncio.run(
            main(
                port=cfg['websocket_port'],
                buffer=cfg['buffer'],
                clients=cfg['clients'],
                network=cfg['network']
            )
        )
    except KeyboardInterrupt:
        pass
