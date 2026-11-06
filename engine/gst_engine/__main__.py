"""引擎入口：python -u -m gst_engine，stdin/stdout 走 JSON Lines。"""
from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from pathlib import Path

from . import protocol as P
from . import installer, organizer, scanner, sources, upscaler

# 下载超分组件放到工作线程，主循环继续读 stdin，才能接住「取消」
_install_cancel = threading.Event()
_install_guard = threading.Lock()
_installing = False


def _install_worker(rid) -> None:
    global _installing
    try:
        P.ok(rid, installer.install(_install_cancel))
    except installer.DownloadCancelled:
        P.fail(rid, "Cancelled", "已取消下载")
    except Exception as exc:
        P.fail(rid, "InstallFailed", str(exc))
    finally:
        with _install_guard:
            _installing = False
        _install_cancel.clear()


def handle(req: dict) -> None:
    rid = req.get("id")
    op = req.get("op")

    if op == "run":
        args = req.get("args") or {}
        output_dir = args.get("output_dir", "")
        if not output_dir.strip():
            P.fail(rid, "MissingOutput", "输出目录为空")
            return

        do_upscale = bool(args.get("upscale", False))
        total = 6 if do_upscale else 5
        marks: list[tuple[str, float]] = []

        def mark(name: str, index: int) -> None:
            marks.append((name, time.monotonic()))
            P.phase(name, index, total)

        mark("探测版本", 1)
        info = sources.probe()
        P.log("INFO", f"当前版本 {info['version']} / {info['product']}")

        mark("生成下载计划", 2)
        plan = sources.plan()
        P.log("INFO", f"候选包 {plan['count']} 个，来源 {plan['host']}")

        mark("扫描包清单", 3)
        limit_packs = int(args.get("limit_packs", 0))
        limit_groups = int(args.get("limit_groups", 0))
        plan, latest, by_pack, stats = organizer.collect(limit_packs, 12, plan)
        P.log("INFO",
              f"扫描 {stats['packs']} 个包 / 索引 {stats['index_mb']}MB / "
              f"命中 {len(latest)} 个文件 / 失败 {len(stats['errors'])}")

        mark("解密与归档", 4)
        result = organizer.archive(plan, latest, by_pack, Path(output_dir),
                                   limit_groups, 6, stats)
        skipped = result["skipped_groups"]
        P.log("INFO",
              f"归档 {result['groups']} 组 / {result['files']} 个文件 -> {output_dir}"
              + (f"（跳过 {skipped} 组已完成，新写 {result['written_groups']} 组）"
                 if skipped else ""))
        if result["failed"]:
            P.log("WARNING", f"{len(result['failed'])} 组归档失败，重新运行可补齐")

        candidates = {}
        upscale_result = None
        if do_upscale:
            mark("2倍超分", 5)
            upscale_result = upscaler.upscale(output_dir)
            if upscale_result["groups"] == 0:
                P.log("INFO", "没有待超分的模型，跳过超分")
            if upscale_result["failed"]:
                P.log("WARNING", f"{len(upscale_result['failed'])} 页超分失败")
        else:
            candidates = upscaler.count_candidates(output_dir)
            if candidates["pages"]:
                P.log("INFO",
                      f"发现 {candidates['pages']} 页可超分（{candidates['groups']} 组）")

        mark("整理完成", total)
        P.log("INFO",
              f"完成：{result['groups']} 组 / {result['files']} 个文件 / "
              f"新写 {result['written_groups']} 组 / "
              f"跳过 {result['skipped_groups']} 组 / "
              f"报错 {len(result['errors'])} 条")
        timings = [
            {"name": name,
             "seconds": round((marks[i + 1][1] if i + 1 < len(marks)
                               else time.monotonic()) - at, 2)}
            for i, (name, at) in enumerate(marks)
        ]
        P.ok(rid, {**result, "version": plan["version"],
                   "upscale_candidates": candidates,
                   "upscale_result": upscale_result,
                   "phase_timings": timings})
        return

    if op == "probe":
        P.phase("探测版本", 1, 4)
        data = sources.probe()
        P.log("INFO", f"当前版本 {data['version']} / {data['product']}")
        P.ok(rid, {
            "product": data["product"],
            "version": data["version"],
            "config_count": len(data["config_urls"]),
        })
        return

    if op == "plan":
        P.phase("生成下载计划", 2, 4)
        data = sources.plan()
        P.log("INFO", f"候选包 {data['count']} 个，来源 {data['host']}")
        P.ok(rid, {
            "host": data["host"],
            "product": data["product"],
            "version": data["version"],
            "count": data["count"],
        })
        return

    if op == "scan":
        P.phase("筛选 Spine 条目", 3, 4)
        args = req.get("args") or {}
        result = scanner.scan(int(args.get("limit", 12)))
        P.log("INFO", f"扫描 {result['scanned']} 个包，命中 {result['target_files']} 个三件套文件")
        P.ok(rid, result)
        return

    if op == "check_upscaler":
        P.ok(rid, installer.check())
        return

    if op == "install_upscaler":
        global _installing
        with _install_guard:
            if _installing:
                P.fail(rid, "Busy", "已有下载在进行")
                return
            _installing = True
            _install_cancel.clear()
        threading.Thread(target=_install_worker, args=(rid,), daemon=True).start()
        return

    if op == "cancel_install":
        _install_cancel.set()
        P.ok(rid, {"cancelled": True})
        return

    if op == "count_candidates":
        args = req.get("args") or {}
        P.ok(rid, upscaler.count_candidates(args.get("output_dir", "")))
        return

    if op == "upscale":
        args = req.get("args") or {}
        output_dir = args.get("output_dir", "")
        if not output_dir.strip():
            P.fail(rid, "MissingOutput", "输出目录为空")
            return
        P.phase("2倍超分", 1, 2)
        result = upscaler.upscale(output_dir, int(args.get("limit_groups", 0)))
        P.phase("超分完成", 2, 2)
        P.ok(rid, result)
        return

    if op == "organize":
        P.phase("解密与归档", 4, 4)
        args = req.get("args") or {}
        result = organizer.organize(
            args.get("output_dir", ""),
            int(args.get("limit_packs", 0)),
            int(args.get("limit_groups", 0)),
        )
        P.log("INFO", f"归档 {result['groups']} 组 / {result['files']} 个文件")
        P.ok(rid, result)
        return

    P.fail(rid, "UnknownOp", f"未知操作: {op}")


def main() -> int:
    P.hide_stdout()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            P.log("ERROR", f"协议行解析失败: {exc}")
            continue
        try:
            handle(req)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            P.log("ERROR", f"{type(exc).__name__}: {exc}")
            P.fail(req.get("id"), type(exc).__name__, str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
