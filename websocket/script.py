# -*- coding: UTF-8 -*-
import socket
import struct


def pack_data(data_id: str, raw_data: bytes) -> bytes:
    # 前三个字节存放client_id的长度，考虑到128位的ipv6，也应该足够了
    data_id = data_id.encode('UTF-8')
    # len_client_id = str(len(client_id)).zfill(3).encode('UTF-8')
    # 将数字转换为一个字节，即一个元素的字节串, 小于256的非负整数
    len_id = bytes([len(data_id)])
    process_data = len_id + data_id + raw_data
    return process_data


def unpack_data(raw_data: bytes) -> (str, bytes):
    len_id = raw_data[0]
    data_id = raw_data[1: len_id + 1].decode('UTF-8')
    data = raw_data[len_id + 1:]
    return data_id, data


def unpack_socks_data(raw_data: bytes) -> (str, int):
    address_type = raw_data[0]

    if address_type == 1:  # IPv4
        res_data = raw_data[1: 5]
        address = socket.inet_ntoa(res_data)
        data_length = 5
    elif address_type == 3:  # Domain name
        res_data = raw_data[1: 2]
        domain_length = res_data[0]
        address = raw_data[2: 2 + domain_length]
        data_length = 2 + domain_length
    elif address_type == 4:  # IPv6
        addr_ip = raw_data[1: 17]
        address = socket.inet_ntop(socket.AF_INET6, addr_ip)
        data_length = 17
    else:
        return None, None

    port_data = raw_data[data_length:]
    port = struct.unpack('!H', port_data)[0]

    return address, port


def make_socks_pkg(ip_address: str, port: int) -> bytes:
    ip_strs = ip_address.split('.')
    ip_data = bytes([1])
    for ip_str in ip_strs:
        ip_data = ip_data + bytes([int(ip_str)])

    return ip_data + struct.pack('!H', port)