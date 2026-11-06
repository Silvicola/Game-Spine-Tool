"""按条目从远端 zip 取回单个文件的原始字节，不下载整包。

central directory 给出 local header 偏移，但条目数据的起点还要读 local header
里的文件名长度与扩展区长度，因此每个条目需要两次 Range。
"""
from __future__ import annotations

import struct
import zlib

from .httpclient import get_range as _range


def read_entry(url: str, entry: dict) -> bytes:
    offset = entry["offset"]
    _, _, head = _range(url, offset, offset + 29)
    if head[:4] != b"PK\x03\x04":
        raise RuntimeError("local file header 签名不符")

    method = struct.unpack_from("<H", head, 8)[0]
    name_len, extra_len = struct.unpack_from("<HH", head, 26)
    data_start = offset + 30 + name_len + extra_len
    csize = entry["csize"]

    _, _, raw = _range(url, data_start, data_start + csize - 1)
    if method == 0:
        return raw
    if method == 8:
        return zlib.decompress(raw, -15)
    raise RuntimeError(f"不支持的 zip 压缩方法: {method}")
