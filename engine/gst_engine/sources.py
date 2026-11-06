"""Echocalypse 国际服 CDN 清单解析。

规则来自 Live2DHub 帖子 #62 楼（qingliu，2026.11.03 更新）：
  - opinfo 给出 lang_total_online.config 与 upgrade.config 的地址
  - 从地址里取 product-XXXXXXX 与版本号，不硬编码
  - lang 包按语种拼到 update_lang/<lang>/ 下
  - upgrade 包直接拼到 cdn1/ 下
  - 两个 host 可选，res-wx-studio（BASE 2）内容更多，优先使用
"""
from __future__ import annotations

import json
import re
import urllib.request

OPINFO_URL = (
    "https://version-jpjgame.gtarcade.com/opinfo"
    "?opGameId=2972&opId=2335&channel=0&md5="
)
HOSTS = ("res-wx-studio.gtarcade.com", "cdn-wx-studio.gtarcade.com")
LANGS = ("cn", "en", "jp", "id", "th", "tw")

_TIMEOUT = 25


def _get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "GameSpineTool/0.1"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read().decode("utf-8-sig")


def _get_json(url: str):
    return json.loads(_get_text(url))


def opinfo():
    return _get_json(OPINFO_URL)


def config_urls(info) -> list[str]:
    """取 android/ios 两组 config 地址，去重后返回。"""
    cfg = info.get("config", {})
    out: list[str] = []
    for key in (
        "android_mlang_total_config",
        "android_upgrade_config",
        "ios_mlang_total_config",
        "ios_upgrade_config",
    ):
        url = cfg.get(key)
        if url and url not in out:
            out.append(url)
    return out


def product_from(url: str) -> str | None:
    m = re.search(r"/(product-\d+)/", url)
    return m.group(1) if m else None


def version_from(url: str) -> str | None:
    m = re.search(r"/(\d+\.\d+\.\d+\.\d+)/", url)
    return m.group(1) if m else None


def probe() -> dict:
    """只探测元信息，不展开包列表。"""
    info = opinfo()
    urls = config_urls(info)
    lang_cfg = next((u for u in urls if u.endswith("lang_total_online.config")), None)
    upgrade_cfg = next((u for u in urls if u.endswith("upgrade.config")), None)
    head = upgrade_cfg or (urls[0] if urls else "")
    return {
        "opinfo_url": OPINFO_URL,
        "product": product_from(head),
        "version": version_from(head),
        "config_urls": urls,
        "lang_config": lang_cfg,
        "upgrade_config": upgrade_cfg,
    }


def _entries(node, lang=None) -> list[tuple[str | None, str]]:
    """递归抠出所有含 url 的条目，并记住最近的语种 key。"""
    found: list[tuple[str | None, str]] = []
    if isinstance(node, dict):
        url = node.get("url")
        if isinstance(url, str):
            found.append((lang, url))
        else:
            for key, value in node.items():
                found.extend(_entries(value, key if key in LANGS else lang))
    elif isinstance(node, list):
        for value in node:
            found.extend(_entries(value, lang))
    return found


def plan(host: str | None = None) -> dict:
    """生成候选 zip 地址。返回列表供后续按 zip 中央目录筛条目。"""
    info = probe()
    host = host or HOSTS[0]
    product = info["product"]
    urls: list[str] = []

    if info["lang_config"]:
        for lang, rel in _entries(_get_json(info["lang_config"])):
            if lang in LANGS:
                urls.append(f"https://{host}/{product}/cdn1/update_lang/{lang}/{rel}")

    if info["upgrade_config"]:
        for _lang, rel in _entries(_get_json(info["upgrade_config"])):
            urls.append(f"https://{host}/{product}/cdn1/{rel}")

    return {
        "host": host,
        "product": product,
        "version": info["version"],
        "count": len(urls),
        "urls": urls,
    }
