"""按需安装可选的超分组件。

组件从 Real-ESRGAN 官方 Release 下载，校验 SHA-256 后只解压运行所需的四个文件，
其余模型与 Debug 版运行时不落盘。下载失败只影响超分，不影响整理主流程。

三个必须的细节：
  - 直连，不走任何代理（用户明确要求）
  - 断点续传：对 github.com 的链路实测在 45 KB/s 与 10 MB/s 之间跳动，
    失败后从 0 重来会永远下不完，所以半成品文件跨尝试保留
  - 可取消：下载过程中置位 cancel 事件，一个数据块内退出，已下部分保留
"""
from __future__ import annotations

import hashlib
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import protocol as P
from . import upscaler
from .httpclient import DownloadCancelled, download, probe_speed

SOURCES = (
    # 先国内镜像：直连 GitHub 实测在 45 KB/s 到 10 MB/s 之间跳动，镜像稳定得多
    ("Gitee 镜像",
     "https://gitee.com/DXFB/Real-ESRGAN-ncnn-vulkan/releases/download/v0.2.0/"
     "realesrgan-ncnn-vulkan-20220424-windows.zip"),
    ("GitHub 官方",
     "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
     "realesrgan-ncnn-vulkan-20220424-windows.zip"),
)
# 两个源的包实测字节完全一致，同一个白名单校验，换源不影响断点续传
COMPONENT_URL = SOURCES[0][1]
COMPONENT_SHA256 = "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"
COMPONENT_MB = 43.4
ATTEMPTS = 4
PROBE_BYTES = 2 << 20

_REQUIRED = (
    ("realesrgan-ncnn-vulkan.exe", "realesrgan-ncnn-vulkan.exe"),
    ("vcomp140.dll", "vcomp140.dll"),
    ("models/realesr-animevideov3-x2.bin", "models/realesr-animevideov3-x2.bin"),
    ("models/realesr-animevideov3-x2.param", "models/realesr-animevideov3-x2.param"),
)


def target_dir() -> Path:
    return upscaler.tool_dir()


def missing() -> list[str]:
    base = target_dir()
    return [dest for _, dest in _REQUIRED if not (base / dest).is_file()]


def partial_bytes() -> int:
    """已下载但尚未安装的字节数，用于界面提示「可续传」。"""
    cache = target_dir() / ".download" / "component.zip"
    return cache.stat().st_size if cache.is_file() else 0


def check() -> dict:
    lack = missing()
    return {
        "installed": not lack,
        "missing": lack,
        "url": COMPONENT_URL,
        "size_mb": COMPONENT_MB,
        "partial_mb": round(partial_bytes() / 1048576, 1),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _choose_source(cancel=None) -> list[tuple[str, str]]:
    """各取 2 MB 实测速度，按快慢排好源顺序。

    直连 GitHub 实测在 45 KB/s 到 10 MB/s 之间跳，先量一下比盲选稳。
    全部失败时保留原顺序，交给下载阶段的失败轮换。
    """
    speeds: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        futures = {pool.submit(probe_speed, url, PROBE_BYTES, cancel): label
                   for label, url in SOURCES}
        for future, label in futures.items():
            try:
                speeds[label] = future.result()
            except Exception:
                speeds[label] = 0.0

    for label, _ in SOURCES:
        P.log("INFO", f"测速 {label}：{speeds[label]:.2f} MB/s")
    return sorted(SOURCES, key=lambda s: -speeds[s[0]])


def install(cancel=None) -> dict:
    """下载并安装组件。已安装时直接返回，不重复下载。"""
    base = target_dir()
    if not missing():
        return {"installed": True, "already": True, "files": []}

    base.mkdir(parents=True, exist_ok=True)
    cache = base / ".download"
    cache.mkdir(exist_ok=True)
    archive = cache / "component.zip"

    ranked = _choose_source(cancel)
    P.log("INFO", f"选用 {ranked[0][0]} 下载")

    last = ""
    for attempt in range(1, ATTEMPTS + 1):
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled()
        label, url = ranked[(attempt - 1) % len(ranked)]
        try:
            have = archive.stat().st_size if archive.is_file() else 0
            head = f"下载超分组件 {COMPONENT_MB} MB（{label}）"
            if have:
                head += f"，从 {have / 1048576:.1f} MB 续传"
            P.log("INFO", f"{head}，第 {attempt} 次")

            # 进度最多 10 次/秒，避免把管道和界面刷满
            last_rate = [0.0]

            def report(done: int, total: int) -> None:
                now = time.monotonic()
                if now - last_rate[0] >= 0.1 or (total and done >= total):
                    last_rate[0] = now
                    P.progress(done, total, "下载超分组件")

            download(url, archive, report, cancel)
            digest = _sha256(archive)
            if digest != COMPONENT_SHA256:
                # 半成品或损坏，删掉重下，避免一直拿坏包续传
                archive.unlink(missing_ok=True)
                raise RuntimeError(
                    f"校验失败，期望 {COMPONENT_SHA256[:12]}，实际 {digest[:12]}")
            with zipfile.ZipFile(archive) as zf:
                for src, dest in _REQUIRED:
                    out = base / dest
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(zf.read(src))
            shutil.rmtree(cache, ignore_errors=True)
            P.log("INFO", f"超分组件已安装到 {base}")
            return {"installed": True, "already": False,
                    "files": [dest for _, dest in _REQUIRED]}
        except DownloadCancelled:
            done = archive.stat().st_size if archive.is_file() else 0
            P.log("INFO",
                  f"已取消，保留已下载的 {done / 1048576:.1f} MB，下次继续")
            raise
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            P.log("WARNING", f"超分组件安装失败: {last}")
            if attempt < ATTEMPTS:
                time.sleep(min(2.0, 0.5 * attempt))

    raise RuntimeError(last or "超分组件安装失败")
