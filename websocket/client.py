import asyncio
import logging
import yaml

from client_script import SocksServer


async def main(
        port: int,
        buffer: int,
        client_id: str,
        websocket_uri: str,
        num_ws_connections: int,
        retry: int,
        time_out: int,
) -> None:
    server = SocksServer(
        port=port,
        buffer=buffer,
        client_id=client_id,
        websocket_uri=websocket_uri,
        num_ws_connections=num_ws_connections,
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

    with open('test_cfg.yaml', 'r', encoding='utf-8') as file:
        cfg = yaml.safe_load(file)['local']

    try:
        asyncio.run(
            main(
                port=cfg['server_port'],
                buffer=cfg['buffer'],
                client_id=cfg['client_id'],
                websocket_uri=cfg['websocket_uri'],
                num_ws_connections=cfg['num_ws_connections'],
                retry=cfg['retry'],
                time_out=cfg['time_out'],
            )
        )
    except KeyboardInterrupt:
        pass
