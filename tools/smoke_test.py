"""独立验证 CDN 探测层，直接跑，不经过 stdio 协议。

用法: python tools/smoke_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from gst_engine import sources  # noqa: E402


def main() -> int:
    info = sources.probe()
    print("product      =", info["product"])
    print("version      =", info["version"])
    print("config urls  =", len(info["config_urls"]))
    for url in info["config_urls"]:
        print("   ", url)

    plan = sources.plan()
    print("host         =", plan["host"])
    print("candidate zips =", plan["count"])
    if plan["urls"]:
        print("first        =", plan["urls"][0])
        print("last         =", plan["urls"][-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
