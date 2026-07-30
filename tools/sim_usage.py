#!/usr/bin/env python3
"""Simulate normal Katana UI usage against the local API.

  .venv/bin/python tools/sim_usage.py
  .venv/bin/python tools/sim_usage.py --base http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def req(method: str, url: str, body: dict | None = None, timeout: float = 60.0) -> tuple[int, Any]:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            j = json.loads(raw) if raw else {"detail": e.reason}
        except Exception:
            j = {"detail": raw or str(e)}
        return e.code, j
    except Exception as e:
        return 0, {"detail": f"{type(e).__name__}: {e}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--skip-connect", action="store_true")
    ap.add_argument(
        "--autosave",
        action="store_true",
        help="also exercise PATCH persistence (default leaves preset files untouched)",
    )
    args = ap.parse_args()
    b = args.base.rstrip("/")
    fails = 0
    log: list[str] = []

    def step(name: str, code: int, body: Any, ok_codes=(200,)):
        nonlocal fails
        ok = code in ok_codes
        if not ok:
            fails += 1
        msg = f"{'OK ' if ok else 'FAIL'} {name} -> {code} {json.dumps(body, ensure_ascii=False)[:160]}"
        print(msg, flush=True)
        log.append(msg)
        return ok

    # 1 status
    c, j = req("GET", f"{b}/api/status", timeout=5)
    step("status", c, j)

    # 2 connect
    if not args.skip_connect:
        c, j = req("POST", f"{b}/api/connect", timeout=45)
        step("connect", c, j)
        if c != 200:
            print("\nABORT: not connected — wake BT-DUAL and rerun", flush=True)
            return 2

    # 3 list presets
    c, j = req("GET", f"{b}/api/presets", timeout=5)
    step("presets", c, j)
    presets = j if isinstance(j, list) else []
    # prefer non-tsl structured first for speed, then one tsl if present
    structured = [p for p in presets if not p.get("full") and "backup" not in p.get("id", "")]
    tsl = [p for p in presets if p.get("full")]
    order = (structured[:4] + tsl[:1]) or presets[:3]
    if not order:
        print("no presets")
        return 1

    # 4 load + tweak loop (user flow)
    for i, p in enumerate(order):
        pid = p["id"]
        c, j = req("POST", f"{b}/api/presets/{pid}/load", timeout=120)
        step(f"load[{i}] {pid}", c, j)
        if c != 200:
            continue
        # rapid volume (keyboard spam)
        for v in (28, 32, 36, 40, 38):
            c, j = req("POST", f"{b}/api/amp", {"field": "volume", "value": v}, timeout=15)
            step(f"  vol {v}", c, j)
            time.sleep(0.05)
        # eq tweak
        for field, val in (("gain", 45), ("presence", 50), ("middle", 55)):
            c, j = req("POST", f"{b}/api/amp", {"field": field, "value": val}, timeout=15)
            step(f"  {field}={val}", c, j)
            time.sleep(0.05)
        # pitch
        for s in (-1, 0, -1):
            c, j = req("POST", f"{b}/api/pitch", {"semitones": s}, timeout=15)
            step(f"  pitch {s}", c, j)
            time.sleep(0.05)
        # save back only when explicitly requested
        if args.autosave:
            c, j = req(
                "PATCH",
                f"{b}/api/presets/{pid}",
                {"amp": {"volume": 38, "gain": 45}, "pitch_semitones": -1},
                timeout=10,
            )
            step(f"  save {pid}", c, j)
        time.sleep(0.2)

    # 5 switch back to first quickly twice
    if len(order) >= 2:
        for _ in range(2):
            for p in order[:2]:
                c, j = req("POST", f"{b}/api/presets/{p['id']}/load", timeout=120)
                step(f"switch {p['id']}", c, j)
                time.sleep(0.15)

    # 6 final status
    c, j = req("GET", f"{b}/api/status", timeout=8)
    step("final status", c, j)

    print(f"\n=== done fails={fails}/{len(log)} ===")
    try:
        from pathlib import Path

        rep = Path(__file__).resolve().parents[1] / "tools" / "sim_usage_last.json"
        rep.write_text(json.dumps({"fails": fails, "log": log}, indent=2) + "\n")
        print("report", rep)
    except Exception:
        pass
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
