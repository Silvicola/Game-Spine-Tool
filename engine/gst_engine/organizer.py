"""按 atlas 配对三件套，解密 PNG 并写入输出目录。

配对规则来自 ToolKit 的 02 解密器与 05 收集器：
  - atlas 文本里以 .png 结尾且后跟 size/format 等属性行的行是图片页
  - skel 与 atlas 同目录同主名
  - 输出保留相对目录结构，同角色落到 <ID>/默认/

实测修正（2026-11-06）：atlas 本身只出现在一个包，但同角色的 skel 与 png
有可能落在其他包，所以归档时优先取 atlas 所在包的整组文件，缺失才从全局补齐。

增量（2026-11-06）：写盘前比对 _meta 的 CDN 版本与源条目签名、_src 记录的
文件字节数，一致即跳过，只重跑新增、变化与上次失败的组。
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import protocol as P
from . import fetcher, imaging, screening, sources, zipindex

_PAGE_PROPS = ("size", "format", "filter", "repeat", "pma", "bounds")


def atlas_pages(raw: bytes) -> list[str]:
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    pages: list[str] = []
    for i, line in enumerate(lines):
        name = line.strip()
        if not name or name.startswith("#") or ":" in name:
            continue
        if not name.lower().endswith(".png"):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        keys = []
        for value in lines[j:j + 8]:
            value = value.strip()
            if not value or ":" not in value:
                break
            keys.append(value.split(":", 1)[0].strip().lower())
        if not keys or not any(key in _PAGE_PROPS for key in keys):
            continue
        pages.append(name.replace("\\", "/").rsplit("/", 1)[-1])
    return list(dict.fromkeys(pages))


def build_signatures(latest: dict) -> dict[str, str]:
    """按源目录聚合 CDN 条目指纹，用于识别同版本下资源是否变化。

    扫描阶段已拿到每个条目的压缩字节数，这里纯内存聚合，不发额外请求。
    """
    buckets: dict[str, list[tuple[str, int]]] = {}
    for name, (_, entry) in latest.items():
        directory, short = name.rsplit("/", 1)
        buckets.setdefault(directory, []).append((short, entry["csize"]))
    return {
        directory: hashlib.sha256(
            "\n".join(f"{short}:{size}" for short, size in sorted(items)).encode("utf-8")
        ).hexdigest()[:16]
        for directory, items in buckets.items()
    }


def _completed(name: str, signature: str, version: str, out: Path) -> int:
    """返回已完成组的文件数；0 表示需要重新归档。

    判定依据：_meta 的 CDN 版本与源条目签名都一致，且 _src 记录的每个
    文件仍在、字节数相符。任一条件不满足或文件损坏，一律重跑。
    """
    directory, filename = name.rsplit("/", 1)
    stem = filename[:-6]
    char_id = directory.rsplit("/", 1)[-1]
    suffix = "" if stem == char_id else f"_{stem}"
    target = out / char_id / "默认"
    try:
        meta = json.loads((target / f"_meta{suffix}.json").read_text(encoding="utf-8"))
        if meta.get("cdn_version") != version:
            return 0
        if meta.get("source_signature") != signature:
            return 0
        sizes = json.loads((target / f"_src{suffix}.json").read_text(encoding="utf-8"))
        if not sizes:
            return 0
        for short, size in sizes.items():
            if (target / short).stat().st_size != size:
                return 0
        return len(sizes)
    except Exception:
        return 0


def _scan_pack(url: str):
    try:
        info = zipindex.read_entries(url)
        targets = [e for e in info["entries"] if screening.is_target(e["name"])]
        return url, targets, info["index_bytes"], None
    except Exception as exc:
        return url, [], 0, f"{type(exc).__name__}: {exc}"


def collect(limit_packs: int = 0, workers: int = 12,
            plan: dict | None = None) -> tuple[dict, dict, dict, dict]:
    """并发扫描候选包。limit_packs 为 0 表示全部；按 URL 顺序覆盖取最新。"""
    if plan is None:
        plan = sources.plan()
    urls = [u for u in plan["urls"] if "update_lang" not in u]
    if limit_packs > 0:
        urls = urls[:limit_packs]

    latest: dict[str, tuple[str, dict]] = {}
    by_pack: dict[str, dict[str, dict]] = {}
    stats = {"packs": len(urls), "index_bytes": 0, "errors": []}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (url, targets, nbytes, err) in enumerate(pool.map(_scan_pack, urls), 1):
            if i % 100 == 0 or i == len(urls):
                P.progress(i, len(urls), "扫描包")
            if err:
                stats["errors"].append({"zip": url.rsplit("/", 1)[-1], "error": err})
                continue
            stats["index_bytes"] += nbytes
            pack = by_pack.setdefault(url, {})
            for entry in targets:
                latest[entry["name"]] = (url, entry)
                pack[entry["name"]] = entry

    stats["index_mb"] = round(stats["index_bytes"] / 1024 / 1024, 1)
    return plan, latest, by_pack, stats


def _archive_one(name: str, latest: dict, by_pack: dict,
                 plan: dict, out: Path, signature: str) -> dict:
    url, entry = latest[name]
    pack = by_pack.get(url, {})

    def pick(rel: str):
        local = pack.get(rel)
        if local is not None:
            return url, local, False
        other = latest.get(rel)
        if other is not None:
            return other[0], other[1], True
        return None, None, False

    atlas_data = fetcher.read_entry(url, entry)
    pages = atlas_pages(atlas_data)
    directory, filename = name.rsplit("/", 1)
    stem = filename[:-6]
    char_id = directory.rsplit("/", 1)[-1]

    wanted = [(name, url, entry, False)]
    missing_skel = 0
    missing_png = 0
    fallback: list[str] = []

    skel = f"{directory}/{stem}.skel"
    src_url, src_entry, borrowed = pick(skel)
    if src_entry is not None:
        wanted.append((skel, src_url, src_entry, borrowed))
        if borrowed:
            fallback.append(stem + ".skel")
    else:
        missing_skel = 1

    for page in pages:
        page_path = f"{directory}/{page}"
        src_url, src_entry, borrowed = pick(page_path)
        if src_entry is not None:
            wanted.append((page_path, src_url, src_entry, borrowed))
            if borrowed:
                fallback.append(page)
        else:
            missing_png += 1

    target = out / char_id / "默认"
    target.mkdir(parents=True, exist_ok=True)
    sizes: dict[str, int] = {}
    for rel, src_url, src_entry, _ in wanted:
        data = fetcher.read_entry(src_url, src_entry)
        if rel.lower().endswith(".png"):
            data = imaging.to_png(data)
        short = rel.rsplit("/", 1)[-1]
        (target / short).write_bytes(data)
        sizes[short] = len(data)

    meta = {
        "server": "global",
        "category": directory.split("/")[-1],
        "base_id": char_id,
        "source_id": char_id,
        "cdn_version": plan["version"],
        "source_zip": url.rsplit("/", 1)[-1],
        "source_signature": signature,
    }
    suffix = "" if stem == char_id else f"_{stem}"
    (target / f"_meta{suffix}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / f"_src{suffix}.json").write_text(
        json.dumps(sizes, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "char_id": char_id,
        "files": len(wanted),
        "pages": len(pages),
        "missing_skel": missing_skel,
        "missing_png": missing_png,
        "fallback": fallback,
    }


def archive(plan: dict, latest: dict, by_pack: dict, out: Path,
            limit_groups: int = 0, workers: int = 6,
            stats: dict | None = None) -> dict:
    atlases = sorted(n for n in latest if n.lower().endswith(".atlas"))
    found = len(atlases)
    if limit_groups > 0:
        atlases = atlases[:limit_groups]

    out.mkdir(parents=True, exist_ok=True)
    signatures = build_signatures(latest)
    version = plan["version"]
    todo: list[str] = []
    skipped_groups = 0
    skipped_files = 0
    for name in atlases:
        n = _completed(name, signatures.get(name.rsplit("/", 1)[0], ""), version, out)
        if n:
            skipped_groups += 1
            skipped_files += n
        else:
            todo.append(name)

    result = {
        "output_dir": str(out),
        "packs": stats["packs"] if stats else 0,
        "index_mb": stats["index_mb"] if stats else 0,
        "entries": len(latest),
        "atlas_found": found,
        "groups": skipped_groups,
        "files": skipped_files,
        "skipped_groups": skipped_groups,
        "skipped_files": skipped_files,
        "written_groups": 0,
        "missing_skel": 0,
        "missing_png": 0,
        "fallback_groups": 0,
        "errors": list(stats["errors"]) if stats else [],
        "failed": [],
    }
    if skipped_groups:
        P.log("INFO", f"跳过 {skipped_groups} 组已完成，待归档 {len(todo)} 组")
    if not todo:
        P.progress(len(atlases), len(atlases), "归档角色")
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_archive_one, n, latest, by_pack, plan, out,
                        signatures.get(n.rsplit("/", 1)[0], "")): n
            for n in todo
        }
        for i, future in enumerate(as_completed(futures), 1):
            name = futures[future]
            done = skipped_groups + i
            if done % 25 == 0 or done == len(atlases):
                P.progress(done, len(atlases), "归档角色")
            try:
                item = future.result()
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                result["errors"].append({"atlas": name, "error": detail})
                result["failed"].append(name.rsplit("/", 2)[-2])
                P.log("ERROR", f"{name} 归档失败: {detail}")
                continue

            result["groups"] += 1
            result["files"] += item["files"]
            result["written_groups"] += 1
            result["missing_skel"] += item["missing_skel"]
            result["missing_png"] += item["missing_png"]
            if item["fallback"]:
                result["fallback_groups"] += 1
                P.log("INFO", f"{item['char_id']} 从其他包补齐 {item['fallback']}")
            if i <= 3 or i % 50 == 0:
                P.log("INFO", f"{item['char_id']} 归档 {item['files']} 个文件 / {item['pages']} 页")

    return result


def organize(output_dir: str, limit_packs: int = 0, limit_groups: int = 0,
             scan_workers: int = 12, archive_workers: int = 6) -> dict:
    """兼容入口。界面走 __main__ 的 run，一次扫描复用给归档。"""
    plan, latest, by_pack, stats = collect(limit_packs, scan_workers)
    return archive(plan, latest, by_pack, Path(output_dir),
                   limit_groups, archive_workers, stats)
