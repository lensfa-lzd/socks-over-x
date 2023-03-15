# -*- coding: UTF-8 -*-

import base64

import json
import logging


def process_data_tcp(client_id, raw_data):
    # 尝试不使用json和base64来提升性能, 结果性能没有多少变化，/(ㄒoㄒ)/~~
    """

    :param client_id: srt
    :param raw_data: bytes
    :return:
    """
    # b64_encoded_str = base64.b64encode(raw_data).decode('utf-8')
    # json_data = {
    #     'client_id': client_id,
    #     'data': b64_encoded_str
    # }
    # json_srt = json.dumps(json_data)
    # # 原始数据包中加上客户端的id
    # return json_srt
    # logging.warning(str(client_id))

    # 前三个字节存放client_id的长度，考虑到128位的ipv6，也应该足够了
    client_id = client_id.encode('UTF-8')
    len_client_id = str(len(client_id)).zfill(3).encode('UTF-8')

    process_data = len_client_id + client_id + raw_data
    return process_data


def process_data_websocket(raw_data):
    # json_data = json.loads(raw_data)
    # # 从原始数据包中解析出客户端的id
    # client_id = json_data['client_id']
    # b64_encoded = json_data['data'].encode('utf-8')
    # raw_data = base64.b64decode(b64_encoded)
    #
    # return client_id, raw_data

    # 前三个字节存放client_id的长度，考虑到128位的ipv6，也应该足够了
    len_client_id = int(raw_data[:3].decode('UTF-8'))

    client_id = raw_data[3:len_client_id+3].decode('UTF-8')
    data = raw_data[len_client_id+3:]
    return client_id, data


def re_pack(websocket_id, client_id, raw_data):
    """
    将原始数据加上websocket的id，以便识别

    :param client_id:
    :param websocket_id:
    :param raw_data:
    :return:
    """
    # b64_encoded_str = base64.b64encode(raw_data).decode('utf-8')
    # # 在两个id之间加上便于识别的分隔符
    # json_data = {
    #     'client_id': websocket_id + '*' + client_id,
    #     'data': b64_encoded_str
    # }
    # json_srt = json.dumps(json_data)
    # # 原始数据包中加上客户端的id
    # return json_srt
    ids = websocket_id + '*' + client_id

    ids = ids.encode('UTF-8')
    len_ids = str(len(ids)).zfill(3).encode('UTF-8')

    process_data = len_ids + ids + raw_data
    return process_data


def unpack(raw_data):
    # json_data = json.loads(raw_data)
    # b64_encoded = json_data['data'].encode('utf-8')
    # raw_data = base64.b64decode(b64_encoded)
    # websocket_client_id = json_data['client_id']
    # websocket_id, client_id = websocket_client_id.split('*')
    #
    # return websocket_id, client_id, raw_data

    len_client_id = int(raw_data[:3].decode('UTF-8'))
    # len_data = int(raw_data[len_client_id+1])

    ids = raw_data[3:len_client_id+3].decode('UTF-8')
    data = raw_data[len_client_id + 3:]

    websocket_id, client_id = ids.split('*')
    return websocket_id, client_id, data

