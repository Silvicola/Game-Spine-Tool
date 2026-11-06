"""限量扫描候选包，量化条目级筛选的收益与失败情况。"""
from __future__ import annotations

from . import protocol as P
from . import screening, sources, zipindex


def scan(limit: int = 12) -> dict:
    plan = sources.plan()
    urls = [u for u in plan["urls"] if "update_lang" not in u][:limit]

    result = {
        "scanned": 0,
        "entries_seen": 0,
        "target_files": 0,
        "target_bytes": 0,
        "zip_bytes": 0,
        "index_bytes": 0,
        "errors": [],
    }

    for i, url in enumerate(urls, 1):
        P.progress(i, len(urls), "扫描包")
        short = url.rsplit("/", 1)[-1]
        try:
            info = zipindex.read_entries(url)
        except Exception as exc:
            result["errors"].append({"zip": short, "error": f"{type(exc).__name__}: {exc}"})
            P.log("WARNING", f"{short} 跳过: {type(exc).__name__}")
            continue

        hits = [e for e in info["entries"] if screening.is_target(e["name"])]
        result["scanned"] += 1
        result["entries_seen"] += len(info["entries"])
        result["target_files"] += len(hits)
        result["target_bytes"] += sum(e["csize"] for e in hits)
        result["zip_bytes"] += info["total"]
        result["index_bytes"] += info["index_bytes"]

    return result
