"""带连接池与自动重试的 Range 请求。

实测记录：urllib 每请求新建
opener 约 43 req/s，requests 连接池约 297 req/s，差 6.87 倍，探测阶段从
17 分钟降到 2.5 分钟。这里每线程一个 PoolManager，避免跨线程共享连接。

重试覆盖连接断开、读超时与 429/5xx，对应运行日志里的
RemoteDisconnected 与 TimeoutError。

下载大文件（超分组件）另有一套语义：走直连、支持断点续传、可随时取消。
断点是必要的，对 github.com 的链路实测在 45 KB/s 与 10 MB/s 之间跳动，
一次完整下载经常拿不到稳定窗口，失败后从 0 重来会永远下不完。
"""
from __future__ import annotations

import os
import re
import threading
import time
from urllib.parse import urljoin

import urllib3

_USER_AGENT = "GameSpineTool/0.1"
_local = threading.local()

# 下载用：连接给足时间，读放宽到 60 秒，慢链路也不至于被判超时
_DOWNLOAD_TIMEOUT = urllib3.Timeout(connect=8, read=60)
_CHUNK = 1 << 16
_MAX_REDIRECT = 8


class DownloadCancelled(Exception):
    """用户取消了下载。"""


def _pool() -> urllib3.PoolManager:
    pool = getattr(_local, "pool", None)
    if pool is None:
        pool = urllib3.PoolManager(
            maxsize=16,
            block=True,
            retries=urllib3.Retry(
                total=3,
                connect=3,
                read=3,
                backoff_factor=0.3,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset(["GET"]),
                raise_on_status=False,
            ),
            timeout=urllib3.Timeout(connect=10, read=30),
        )
        _local.pool = pool
    return pool


def _download_pool() -> urllib3.PoolManager:
    """下载专用池：不自动重试。

    重定向由 download() 自己跟，因为 urllib3 的 Retry 把「跟随重定向」算进
    重试预算，total=0 时连第一次 302 都会被判成 too many redirects。
    重试也由调用方做，这样能带上 Range 从断点续传，而不是悄悄从 0 重来。
    """
    pool = getattr(_local, "dl_pool", None)
    if pool is None:
        pool = urllib3.PoolManager(
            maxsize=2,
            retries=False,
            timeout=_DOWNLOAD_TIMEOUT,
        )
        _local.dl_pool = pool
    return pool


def _open(url: str, headers: dict, cancel=None, timeout=None):
    """手动跟随重定向，返回 (最终 URL, 响应)。"""
    for _ in range(_MAX_REDIRECT):
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled()
        resp = _download_pool().request(
            "GET", url,
            headers=headers,
            preload_content=False,
            decode_content=False,
            redirect=False,
            retries=False,
            timeout=timeout or _DOWNLOAD_TIMEOUT,
        )
        if resp.status in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise RuntimeError(f"HTTP {resp.status} 但缺少 Location")
            url = urljoin(url, location)
            continue
        return url, resp
    raise RuntimeError("重定向次数过多")


def probe_speed(url: str, size: int = 2 << 20, cancel=None,
                budget: float = 4.0) -> float:
    """取前 size 字节测速，返回 MB/s，取不到返回 0。

    慢链路上等满 size 可能要几十秒，所以按时间封顶：到点就按已收到的字节
    算速度，读超时也设为 8 秒，避免把界面卡住。
    """
    start = time.monotonic()
    try:
        _, resp = _open(
            url,
            {"User-Agent": _USER_AGENT, "Range": f"bytes=0-{size - 1}"},
            cancel,
            timeout=urllib3.Timeout(connect=6, read=8),
        )
    except Exception:
        return 0.0

    got = 0
    try:
        while got < size:
            if cancel is not None and cancel.is_set():
                return 0.0
            if time.monotonic() - start > budget:
                break
            chunk = resp.read(65536)
            if not chunk:
                break
            got += len(chunk)
    except Exception:
        pass
    finally:
        resp.close()

    elapsed = time.monotonic() - start
    if got <= 0:
        return 0.0
    # 极快的源可能在 20 ms 内读完，给最低分母避免除零，也避免把快判成 0
    return got / 1048576 / max(elapsed, 0.001)


def _parse_total(content_range: str, content_length: int, have: int) -> int:
    m = re.search(r"/(\d+)\s*$", content_range or "")
    if m:
        return int(m.group(1))
    return have + content_length if have else content_length


def download(url: str, target, on_progress=None, cancel=None) -> int:
    """流式下载到本地文件，已存在则从其末尾续传。返回文件总字节数。

    cancel 为 threading.Event，置位后在下一个数据块处抛 DownloadCancelled。
    目标文件不完整时保留下来，下次调用接着下。
    """
    target = str(target)
    have = 0
    try:
        if os.path.exists(target):
            have = os.path.getsize(target)
    except OSError:
        have = 0

    headers = {"User-Agent": _USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"

    _, resp = _open(url, headers, cancel)
    try:
        if resp.status == 416:
            # 已下满，续传请求越界。交由调用方校验完整性
            return have
        if resp.status == 206:
            total = _parse_total(
                resp.headers.get("Content-Range", ""),
                int(resp.headers.get("Content-Length") or 0), have)
            mode = "ab"
        elif resp.status == 200:
            # 服务端不认 Range，只能从头写
            have = 0
            total = int(resp.headers.get("Content-Length") or 0)
            mode = "wb"
        else:
            raise RuntimeError(f"HTTP {resp.status}")

        done = have
        with open(target, mode) as f:
            for chunk in resp.stream(_CHUNK):
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled()
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
        if total and done != total:
            raise RuntimeError(f"下载不完整 {done}/{total}")
        return done
    finally:
        resp.release_conn()


def get_range(url: str, start: int, end: int) -> tuple[int, str, bytes]:
    resp = _pool().request(
        "GET",
        url,
        headers={"Range": f"bytes={start}-{end}", "User-Agent": _USER_AGENT},
        preload_content=True,
    )
    return resp.status, resp.headers.get("Content-Range", ""), resp.data
