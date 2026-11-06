"""UF 加密贴图还原为 PNG。

链路（2026-11-05 用真实条目实测确认）：
  UF 前 100 字节 XOR 混淆 -> CCZ! -> zlib 解压 -> PVR v3 / ETC2 RGBA8
  -> texture2ddecoder 解码 -> BGRA 重排与预乘 alpha 还原 -> PNG

算法与旧项目 crypto.py 一致，两边互相印证。
"""
from __future__ import annotations

import io
import struct
import zlib

import numpy as np
from PIL import Image
from texture2ddecoder import decode_etc2a8

UF_KEY = bytes([
    19, 91, 12, 13, 102, 22, 34, 43, 17, 25, 88, 64, 36, 16, 14, 66,
    49, 87, 56, 44, 53, 28, 11, 5, 116, 37, 58, 105, 20, 15, 77, 7, 29,
])
_XOR_LIMIT = 100
_CCZ_MAGIC = b"CCZ!\x00"
_PNG_MAGIC = b"\x89PNG\r"
_PREMUL_LUT = None


def _unxor(data: bytes, magic: bytes) -> bytearray:
    seed = data[4]
    out = bytearray(data)
    out[0:5] = magic
    for i in range(5, min(_XOR_LIMIT, len(data))):
        out[i] ^= UF_KEY[(i + seed) % 0x21]
    return out


def uf_restore(data: bytes) -> tuple[bytes, str]:
    if data[:2] != b"UF":
        return data, "raw"

    ccz = _unxor(data, _CCZ_MAGIC)
    try:
        zlib.decompress(bytes(ccz[16:]))
        return bytes(ccz), "ccz"
    except zlib.error:
        pass

    png = _unxor(data, _PNG_MAGIC)
    if bytes(png[:8]) == b"\x89PNG\r\n\x1a\n":
        return bytes(png), "png"

    raise RuntimeError("UF 还原失败，既不是 CCZ 也不是 PNG")


def _premul_lut():
    global _PREMUL_LUT
    if _PREMUL_LUT is None:
        values = np.arange(256, dtype=np.float64)
        lut = np.zeros((256, 256), dtype=np.uint8)
        for a in range(1, 256):
            lut[a] = np.minimum(255.0, np.round(values * 255.0 / a)).astype(np.uint8)
        _PREMUL_LUT = lut
    return _PREMUL_LUT


def pvr_to_png(pvr: bytes) -> bytes:
    if pvr[:4] != b"PVR\x03":
        raise RuntimeError("不是 PVR v3")

    (flags, pixel_format, _cs, _ct, height, width,
     _depth, _surf, _faces, _mips, metadata_size) = struct.unpack("<IQIIIIIIIII", pvr[4:52])
    body = pvr[52 + metadata_size:]
    raw = decode_etc2a8(body, width, height)

    arr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 4)
    if flags & 0x02:
        alpha = arr[:, 3]
        blue, green, red = arr[:, 0], arr[:, 1], arr[:, 2]
        lut = _premul_lut()
        out = np.empty((arr.shape[0], 4), dtype=np.uint8)
        out[:, 0] = lut[alpha, red]
        out[:, 1] = lut[alpha, green]
        out[:, 2] = lut[alpha, blue]
        out[:, 3] = alpha
    else:
        out = arr[:, [2, 1, 0, 3]].copy()

    image = Image.frombytes("RGBA", (width, height), out.tobytes())
    buffer = io.BytesIO()
    image.save(buffer, "PNG", compress_level=1)
    return buffer.getvalue()


def _trim_png(data: bytes) -> bytes:
    """按 IEND 截断 UF 还原后尾部的附加字节。

    ToolKit 的說明7 记录过这种样本，PNG 的 IEND 之后还跟 5 个额外字节。
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return data
    p = 8
    while p + 8 <= len(data):
        length = struct.unpack_from(">I", data, p)[0]
        end = p + 12 + length
        if end > len(data):
            break
        if data[p + 4:p + 8] == b"IEND":
            return data[:end]
        p = end
    return data


def to_png(data: bytes) -> bytes:
    restored, kind = uf_restore(data)
    if kind == "raw":
        return data
    if kind == "png":
        return _trim_png(restored)
    return pvr_to_png(zlib.decompress(restored[16:]))
