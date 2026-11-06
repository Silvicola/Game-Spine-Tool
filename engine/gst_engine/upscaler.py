"""把归档后的 Spine 图集放大 2 倍，另存到输出目录的 Upscaled/。

规则（2026-11-06 用户裁决）：
  - 只处理最长边 < 2048 的整套模型；整套任一页达到 2048 则整组跳过
  - 三件套统一加 _2x 后缀，atlas 内的页名行同步改写，其余行一字不动
  - 原目录不改动；产物为 Upscaled/<相对路径>/ 下的完整可加载模型
  - 调用随包的 realesrgan-ncnn-vulkan.exe，不依赖 Python 深度学习栈
  - 已有 2 倍产物且与当前源一致时跳过，不覆盖 Upscaled 内的文件

Spine 运行时按 atlas 的 size: 行归一化 UV，不读 PNG 实际像素尺寸，
所以放大贴图后 atlas 的 size 与所有坐标行必须保持原值。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import protocol as P
from .organizer import atlas_pages

SUFFIX = "_2x"
# 最长边达到该值即整组跳过（严格小于才超分）
MAX_EDGE = 2048
OUTPUT_DIR_NAME = "Upscaled"
MODEL_NAME = "realesr-animevideov3"
BATCH = 40
_PAGE_SIZE_RE = re.compile(r"^\s*size\s*:", re.I)


def tool_dir() -> Path:
    """开发期与随包发布都在 <engine 上级>/tools/realesrgan。"""
    return Path(__file__).resolve().parent.parent.parent / "tools" / "realesrgan"


def _suffix_name(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return f"{stem}{SUFFIX}.{ext}" if dot else f"{name}{SUFFIX}"


def _clean_edges(source: Path, produced: Path) -> None:
    """消除超分模型在零件边界生成的黑色描边。

    实测（80023001）：模型把全透明区域 RGB=(0,0,0) 当成内容，
    在边界带造出暗线，边界带内 7.13% 的像素比纯插值暗 60 以上，
    不透明内部只有 0.014%。做法是用 Lanczos 基线覆盖边界带，
    只在腐蚀后的不透明内部保留 AI 结果，alpha 也用基线值，
    避免模型把原本透明的像素点亮。
    """
    import numpy as np
    from PIL import Image, ImageFilter

    with Image.open(source) as src, Image.open(produced) as up:
        baseline = src.convert("RGBA").resize(up.size, Image.LANCZOS)
        ai = up.convert("RGBA")
        alpha = baseline.getchannel("A")
        inner = np.asarray(alpha.filter(ImageFilter.MinFilter(9))) > 250

        base_arr = np.asarray(baseline).copy()
        ai_arr = np.asarray(ai)
        base_arr[inner, :3] = ai_arr[inner, :3]
        Image.fromarray(base_arr, "RGBA").save(produced, "PNG", compress_level=1)


def _rewrite_atlas(raw: bytes) -> bytes:
    """只改页名行，保留其余每一行与原始换行。"""
    text = raw.decode("utf-8", "replace")
    lines = text.split("\n")
    result = list(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (not stripped.lower().endswith(".png") or ":" in stripped
                or stripped.startswith("#")):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and _PAGE_SIZE_RE.match(lines[j]):
            result[i] = line.replace(stripped, _suffix_name(stripped))
    return "\n".join(result).encode("utf-8", "replace")


def _upscaled_complete(atlas: Path, skel: Path | None,
                       page_files: list[tuple[Path, str]], destination: Path) -> bool:
    """已有 2 倍产物且与当前源一致时跳过，避免重复超分与覆盖产物。

    atlas 逐字节比对重写结果，skel 比对字节数，图片页比对是否为源的 2 倍。
    源资源变化或产物缺失都会返回 False，触发重新超分。
    """
    from PIL import Image

    try:
        out_atlas = destination / _suffix_name(atlas.name)
        if not out_atlas.is_file():
            return False
        if out_atlas.read_bytes() != _rewrite_atlas(atlas.read_bytes()):
            return False
        if skel is not None:
            out_skel = destination / _suffix_name(skel.name)
            if not out_skel.is_file() or out_skel.stat().st_size != skel.stat().st_size:
                return False
        for src, page_name in page_files:
            dst = destination / page_name
            if not dst.is_file():
                return False
            with Image.open(src) as original, Image.open(dst) as doubled:
                if doubled.size != (original.width * 2, original.height * 2):
                    return False
        return True
    except Exception:
        return False


def _groups(root: Path) -> tuple[list[dict], list[str]]:
    """扫描归档目录，返回可超分的模型组与跳过原因。"""
    from PIL import Image

    groups: list[dict] = []
    skipped: list[str] = []
    for atlas in sorted(root.rglob("*.atlas")):
        if OUTPUT_DIR_NAME in atlas.parts:
            continue
        raw = atlas.read_bytes()
        pages = atlas_pages(raw)
        if not pages:
            skipped.append(f"{atlas.name}: 未解析到图片页")
            continue

        source = atlas.parent
        stem = atlas.stem
        skel = source / f"{stem}.skel"
        page_files: list[tuple[Path, str]] = []
        too_large = False
        for page in pages:
            png = source / page
            if not png.exists():
                skipped.append(f"{atlas.name}: 缺 {page}")
                too_large = True
                break
            with Image.open(png) as im:
                if max(im.size) >= MAX_EDGE:
                    too_large = True
                    break
            page_files.append((png, _suffix_name(page)))
        if too_large:
            continue

        destination = root / OUTPUT_DIR_NAME / source.relative_to(root)
        skel_file = skel if skel.exists() else None
        if _upscaled_complete(atlas, skel_file, page_files, destination):
            skipped.append(f"{atlas.name}: 已有 2 倍产物")
            continue
        groups.append({
            "atlas": atlas,
            "skel": skel_file,
            "stem": stem,
            "pages": page_files,
            "destination": destination,
        })
    return groups, skipped


def count_candidates(output_dir: str) -> dict:
    root = Path(output_dir)
    if not root.exists():
        return {"groups": 0, "pages": 0}
    groups, skipped = _groups(root)
    return {
        "groups": len(groups),
        "pages": sum(len(g["pages"]) for g in groups),
        "skipped": len(skipped),
    }


def upscale(output_dir: str, limit_groups: int = 0) -> dict:
    root = Path(output_dir)
    if not root.exists():
        raise FileNotFoundError(f"输出目录不存在: {output_dir}")

    tools = tool_dir()
    exe = tools / "realesrgan-ncnn-vulkan.exe"
    models = tools / "models"
    if not exe.exists() or not (models / f"{MODEL_NAME}-x2.param").exists():
        raise FileNotFoundError(f"超分组件缺失: {tools}")

    groups, skipped = _groups(root)
    if limit_groups > 0:
        groups = groups[:limit_groups]

    result = {
        "output_dir": str(root / OUTPUT_DIR_NAME),
        "groups": len(groups),
        "pages": 0,
        "failed": [],
        "skipped": skipped,
    }
    if not groups:
        return result

    # 先复制并改名 atlas / skel，坐标行与二进制内容都保持原样
    jobs: list[tuple[Path, Path]] = []
    for group in groups:
        dest = group["destination"]
        dest.mkdir(parents=True, exist_ok=True)
        (dest / _suffix_name(group["atlas"].name)).write_bytes(
            _rewrite_atlas(group["atlas"].read_bytes()))
        if group["skel"] is not None:
            shutil.copy2(group["skel"], dest / _suffix_name(group["skel"].name))
        for src, page_name in group["pages"]:
            jobs.append((src, dest / page_name))
    result["pages"] = len(jobs)
    P.log("INFO", f"超分 {len(groups)} 组 / {len(jobs)} 页，产物 -> {result['output_dir']}")

    with tempfile.TemporaryDirectory(prefix="gst_upscale_") as tmp:
        work_in = Path(tmp) / "in"
        work_out = Path(tmp) / "out"
        for start in range(0, len(jobs), BATCH):
            batch = jobs[start:start + BATCH]
            work_in.mkdir(parents=True, exist_ok=True)
            work_out.mkdir(parents=True, exist_ok=True)
            for index, (src, _) in enumerate(batch):
                shutil.copy2(src, work_in / f"{index:04d}_{src.name}")

            try:
                proc = subprocess.run(
                    [str(exe), "-i", str(work_in), "-o", str(work_out),
                     "-m", str(models), "-n", MODEL_NAME, "-s", "2", "-f", "png"],
                    capture_output=True, timeout=900,
                )
                if proc.returncode != 0:
                    raise RuntimeError(
                        (proc.stderr or proc.stdout).decode("utf-8", "replace")[-300:])
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                for _, dst in batch:
                    result["failed"].append({"file": dst.name, "error": detail})
                    P.log("ERROR", f"超分批次失败: {detail}")
            else:
                for index, (src, dst) in enumerate(batch):
                    produced = work_out / f"{index:04d}_{src.name}"
                    if produced.exists():
                        try:
                            _clean_edges(src, produced)
                        except Exception as exc:
                            P.log("WARNING",
                                  f"{dst.name} 边界修复失败: {type(exc).__name__}: {exc}")
                        shutil.move(str(produced), str(dst))
                    else:
                        result["failed"].append(
                            {"file": dst.name, "error": "未产出结果文件"})
                        P.log("ERROR", f"超分缺文件: {dst.name}")

            shutil.rmtree(work_in, ignore_errors=True)
            shutil.rmtree(work_out, ignore_errors=True)
            P.progress(min(start + BATCH, len(jobs)), len(jobs), "超分图片")

    P.log("INFO", f"超分完成：{result['pages'] - len(result['failed'])} / {result['pages']} 页成功")
    return result
