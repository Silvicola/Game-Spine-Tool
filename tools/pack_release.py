"""打便携版 zip。

从 Release 构建产物组装一份干净目录再压缩，而不是直接压 dist。dist 是给本机
运行用的，里面会积累 history/、__pycache__ 和下载下来的超分组件，直接压会把
这些一起发出去（本轮就踩了两次）。

用法：python tools/pack_release.py [--no-build]
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "src" / "GameSpineTool"
BUILD = PROJECT / "bin" / "Release" / "net10.0-windows10.0.26100.0" / "win-x64"
STAGE = ROOT / "output" / "pack" / "GameSpineTool"
README = ROOT / "tools" / "README_portable.txt"
DIST = ROOT / "dist"

# 绝不允许出现在发行包里的东西：第三方二进制、运行历史、字节码缓存
FORBIDDEN = ("tools/realesrgan", "history/", "__pycache__/")


def seven_zip() -> str:
    for cand in (os.environ.get("SEVEN_ZIP"), shutil.which("7z"), shutil.which("7z.exe"),
                 r"C:\Program Files\7-Zip\7z.exe"):
        if cand and Path(cand).is_file():
            return cand
    raise SystemExit("找不到 7z，请安装 7-Zip 或设置环境变量 SEVEN_ZIP 指向 7z.exe")


def build() -> None:
    print("== dotnet build Release self-contained ==")
    subprocess.run(
        ["dotnet", "build", "-c", "Release", "-p:SelfContained=true", "-v", "minimal"],
        cwd=PROJECT, check=True,
    )


def stage() -> None:
    if not (BUILD / "GameSpineTool.exe").is_file():
        raise SystemExit(f"找不到构建产物：{BUILD}")
    print(f"== 组装干净目录 {STAGE} ==")
    if STAGE.exists():
        shutil.rmtree(STAGE)
    shutil.copytree(BUILD, STAGE)
    shutil.copy2(README, STAGE / "README.txt")
    files = sum(1 for p in STAGE.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in STAGE.rglob("*") if p.is_file())
    print(f"   {files} 个文件，{size / 1048576:.0f} MB")


def verify(target: Path) -> None:
    archive = str(target)
    for name in FORBIDDEN:
        out = subprocess.run([seven_zip(), "l", archive],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        if name in (out.stdout or ""):
            raise SystemExit(f"包里出现了不该有的内容：{name}")


def pack(name: str) -> Path:
    out = DIST / name
    DIST.mkdir(exist_ok=True)
    out.unlink(missing_ok=True)
    print(f"== 压缩 {out.name} ==")
    subprocess.run([seven_zip(), "a", "-tzip", "-mx=7", "-bso0", "-bsp0",
                    str(out), "GameSpineTool"],
                   cwd=STAGE.parent, check=True)
    verify(out)
    size = out.stat().st_size / 1048576
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"   体积 {size:.0f} MB")
    print(f"   SHA-256 {digest}")
    return out


def main() -> int:
    args = sys.argv[1:]
    name = f"GameSpineTool_win-x64_{date.today():%Y%m%d}.zip"
    if "--name" in args:
        name = args[args.index("--name") + 1]
    if "--no-build" not in args:
        build()
    stage()
    pack(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
