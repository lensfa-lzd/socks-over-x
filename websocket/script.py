# -*- coding: UTF-8 -*-

import base64

import json


def unpack_data(raw_data):
    return raw_data


def pack_data(raw_data):
    return raw_data


def process_data_tcp(client_id, raw_data):
    """

    :param client_id: srt
    :param raw_data: bytes
    :return:
    """
    b64_encoded_str = base64.b64encode(raw_data).decode('utf-8')
    json_data = {
        'client_id': client_id,
        'data': b64_encoded_str
    }
    json_srt = json.dumps(json_data)
    # 原始数据包中加上客户端的id
    return json_srt


def process_data_websocket(raw_data):
    json_data = json.loads(raw_data)
    # 从原始数据包中解析出客户端的id
    client_id = json_data['client_id']
    b64_encoded = json_data['data'].encode('utf-8')
    raw_data = base64.b64decode(b64_encoded)

    return client_id, raw_data
