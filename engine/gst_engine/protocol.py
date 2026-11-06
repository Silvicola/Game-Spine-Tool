"""stdio JSON Lines 协议。

约定：
  - 一行一条 JSON，stdout 只跑协议，其余输出走 stderr
  - 请求 {"id": 1, "op": "probe", "args": {}}
  - 应答 {"id": 1, "ok": true, "data": ...} 或 {"id": 1, "ok": false, "error": {...}}
  - 事件 {"ev": "phase"|"log"|"progress", ...}，不带 id

与常见的 stdio JSON Lines 引擎协议同构。
"""
from __future__ import annotations

import json
import sys

_out = sys.stdout


def hide_stdout() -> None:
    """把 sys.stdout 指向 stderr，库的 print 不会冲坏协议行。"""
    sys.stdout = sys.stderr


def send(obj) -> None:
    _out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _out.flush()


def event(kind: str, **kw) -> None:
    send({"ev": kind, **kw})


def log(level: str, text: str) -> None:
    event("log", level=level, text=text)


def phase(name: str, index: int, total: int) -> None:
    event("phase", name=name, index=index, total=total)


def progress(done: int, total: int, label: str = "") -> None:
    event("progress", done=done, total=total, label=label)


def ok(rid, data=None) -> None:
    send({"id": rid, "ok": True, "data": data})


def fail(rid, kind: str, message: str) -> None:
    send({"id": rid, "ok": False, "error": {"kind": kind, "message": message}})
