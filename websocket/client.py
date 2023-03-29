import asyncio
import argparse
import yaml

from typing import List
from script.client_script import SocksServer
from script.script import log_level


# TODO 考虑对udp代理的支持
# task = task_id + task_data
async def main(
        port: int,
        buffer: int,
        client_id: str,
        websocket_uris: List[str],
        retry: int,
        time_out: int,
        network: List[int]
) -> None:
    server = SocksServer(
        port=port,
        buffer=buffer,
        client_id=client_id,
        websocket_uris=websocket_uris,
        retry=retry,
        time_out=time_out,
        network=network
    )

    # 相当于阻塞
    await server.serve()


# 可以部署在azure container app上/支持websocket
# pyinstaller -F test.py
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Sox websocket client')
    parser.add_argument('--config', type=str, default='config.yaml', help='config file location')
    args = parser.parse_args()

    with open(args.config, 'r', encoding='utf-8') as file:
        cfg = yaml.safe_load(file)['local']

    log_level(cfg['log_level'])

    try:
        asyncio.run(
            main(
                port=cfg['server_port'],
                buffer=cfg['buffer'],
                client_id=cfg['client_id'],
                websocket_uris=cfg['websocket_uris'],
                retry=cfg['retry'],
                time_out=cfg['time_out'],
                network=cfg['network'],
            )
        )
    except KeyboardInterrupt:
        pass
