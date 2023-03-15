import asyncio
import struct
import select
import socket


def generate_failed_reply(address_type, error_number):
    return struct.pack("!BBBBIH", 5, error_number, 0, address_type, 0, 0)


async def listener(reader, writer):
    # 协商
    # 从客户端读取并解包两个字节的数据
    header = await reader.read(2)
    version, nmethods = struct.unpack("!BB", header)
    # 设置socks5协议，METHODS字段的数目大于0
    SOCKS_VERSION = 5

    assert version == SOCKS_VERSION
    assert nmethods > 0

    # 接受支持的方法
    methods = []
    for i in range(nmethods):
        method = await reader.read(1)
        methods.append(ord(method))

    # 无需认证
    if 0 not in set(methods):
        writer.close()
        return
    # 发送协商响应数据包
    writer.write(struct.pack("!BB", SOCKS_VERSION, 0))
    await writer.drain()
    # 请求
    res_data = await reader.read(4)
    version, cmd, _, address_type = struct.unpack("!BBBB", res_data)

    if address_type == 1:  # IPv4
        res_data = await reader.read(4)
        address = socket.inet_ntoa(res_data)
    elif address_type == 3:  # Domain name
        res_data = await reader.read(1)
        domain_length = res_data[0]
        address = await reader.read(domain_length)
        # address = socket.gethostbyname(address.decode("UTF-8"))  # 将域名转化为IP，这一行可以去掉
    elif address_type == 4:  # IPv6
        addr_ip = await reader.read(16)
        address = socket.inet_ntop(socket.AF_INET6, addr_ip)
    else:
        writer.close()
        return
    port_data = await reader.read(2)
    port = struct.unpack('!H', port_data)[0]

    # 响应，只支持CONNECT请求
    try:
        if cmd == 1:  # CONNECT
            # remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # remote_reader, remote_writer = await asyncio.open_connection(address, port)
            #
            # # # 暂时不支持ipv6
            # remote.connect((address, port))
            # bind_address = remote.getsockname()

            remote_reader, remote_writer = await asyncio.open_connection(address, port)
        #     把请求发送到本地交换服务器

        else:
            writer.close()

        # addr = struct.unpack("!I", socket.inet_aton(bind_address[0]))[0]
        # port = bind_address[1]
        # reply = struct.pack("!BBBBIH", SOCKS_VERSION, 0, 0, address_type, addr, port)
        # 注意：按照标准协议，返回的应该是对应的address_type，但是实际测试发现，当address_type=3，也就是说是域名类型时，会出现卡死情况，但是将address_type该为1，则不管是IP类型和域名类型都能正常运行
        reply = struct.pack("!BBBBIH", SOCKS_VERSION, 0, 0, 1, 0, 0)
    except Exception as err:
        # logging.error(err)
        print('err!!', err)
        # 响应拒绝连接的错误
        reply = generate_failed_reply(address_type, 5)

    writer.write(reply)
    await writer.drain()

    # 建立连接成功，开始交换数据
    if reply[1] == 0 and cmd == 1:
        task_reader = asyncio.create_task(ex_reader(reader, remote_writer))
        task_writer = asyncio.create_task(ex_writer(remote_reader, writer))
        await asyncio.gather(
            task_reader,
            task_writer
        )
        try:
            task_reader.cancel()
            task_writer.cancel()
        except:
            pass

    writer.close()


async def ex_reader(reader, remote_writer):
    while True:
        data = await reader.read(1024 * 16)
        if data:
            remote_writer.write(data)
            await remote_writer.drain()
        else:
            break


async def ex_writer(remote_reader, writer):
    while True:
        data = await remote_reader.read(1024 * 16)
        if data:
            writer.write(data)
            await writer.drain()
        else:
            break


async def start_socks():
    server = await asyncio.start_server(
        listener, '0.0.0.0', 9001)

    addr = server.sockets[0].getsockname()
    # logging.warning(f'Serving on {addr}')
    # print(f'Serving on {addr}')

    async with server:
        await server.serve_forever()

asyncio.run(start_socks())
