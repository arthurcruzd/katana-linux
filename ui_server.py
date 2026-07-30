#!/usr/bin/env python3
"""Katana control UI — local web app with pitch slider.

  .venv/bin/python ui_server.py
  → http://127.0.0.1:8765
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from katana_ble import (
    ADDR,
    AMP_FIELDS,
    KatanaBLE,
    PS_DIRECT_MIX,
    PS_FINE1,
    PS_LEVEL1,
    PS_MODE1,
    PS_PITCH1,
    PS_PREDELAY1,
    PS_VOICE,
    enc_4x4,
)

ROOT = Path(__file__).resolve().parent
PRESETS = ROOT / "presets"

# Shared connection (one BLE link)
_lock = asyncio.Lock()
_katana: KatanaBLE | None = None
_state = {
    "connected": False,
    "pitch": -1,
    "name": "",
    "error": "",
    "pitch_armed": False,
}


async def get_k(*, force: bool = False) -> KatanaBLE:
    global _katana
    if force and _katana is not None:
        try:
            await _katana.disconnect(drop_link=True)
        except Exception:
            pass
        _katana = None
        _state["connected"] = False
        _state["pitch_armed"] = False
        await asyncio.sleep(0.5)

    if _katana is None or not _state["connected"]:
        k = KatanaBLE()
        await k.connect(force=force)
        _katana = k
        _state["connected"] = True
        _state["error"] = ""
    return _katana


async def with_k(fn, *, retry: bool = True):
    """Run fn(k); on failure optionally hard-reconnect once and retry."""
    async with _lock:
        try:
            k = await get_k()
            return await fn(k)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            _state["error"] = err
            _state["connected"] = False
            if not retry:
                raise HTTPException(500, err) from e
            try:
                k = await get_k(force=True)
                return await fn(k)
            except Exception as e2:
                _state["connected"] = False
                _state["error"] = f"{type(e2).__name__}: {e2}"
                raise HTTPException(500, _state["error"]) from e2


async def ensure_pitch_mod(k: KatanaBLE, semis: int, *, full: bool = False) -> int:
    """Fast path: 1-byte pitch write. Full arm only once per session."""
    semis = max(-24, min(24, int(semis)))
    if full or not _state.get("pitch_armed"):
        await k.arm_pitch_shifter(semis)
        _state["pitch_armed"] = True
    else:
        await k.set_pitch(semis, slots=1)
    _state["pitch"] = semis
    return semis


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    global _katana
    if _katana:
        try:
            await _katana.disconnect()
        except Exception:
            pass
        _katana = None


app = FastAPI(title="Katana UI", lifespan=lifespan)


class PitchIn(BaseModel):
    semitones: int = Field(..., ge=-24, le=24)


class AmpIn(BaseModel):
    field: str
    value: int = Field(..., ge=0, le=120)


class PresetPatchIn(BaseModel):
    """Partial update written back to the preset JSON on disk."""
    amp: dict[str, int | str | float | None] | None = None
    pitch_semitones: int | None = Field(default=None, ge=-24, le=24)
    sw: dict[str, int] | None = None


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = ROOT / "ui.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/status")
async def status():
    async def _do(k: KatanaBLE):
        p = await k.read_status_light()
        _state["pitch"] = p.get("pitch", _state.get("pitch", 0))
        _state["name"] = p.get("name", "")
        _state["connected"] = True
        _state["error"] = ""
        return {
            "connected": True,
            "name": p.get("name"),
            "pitch": p.get("pitch"),
            "amp": p.get("amp"),
            "sw": p.get("sw"),
            "error": "",
        }

    try:
        # status: don't thrash BLE with force-reconnect loops
        return await with_k(_do, retry=False)
    except HTTPException as he:
        return JSONResponse(
            {
                "connected": False,
                "name": "",
                "pitch": _state.get("pitch", 0),
                "amp": None,
                "sw": None,
                "error": he.detail,
            }
        )


@app.post("/api/connect")
async def connect():
    async with _lock:
        try:
            _state["pitch_armed"] = False
            k = await get_k(force=True)
            pitch = int(_state.get("pitch", -1) or -1)
            await ensure_pitch_mod(k, pitch, full=True)
            _state["connected"] = True
            _state["error"] = ""
            return {"ok": True, "connected": True, "pitch": _state["pitch"]}
        except Exception as e:
            _state["connected"] = False
            _state["pitch_armed"] = False
            _state["error"] = f"{type(e).__name__}: {e}"
            raise HTTPException(500, _state["error"]) from e


@app.post("/api/pitch")
async def set_pitch(body: PitchIn):
    async def _do(k: KatanaBLE):
        # no readback — fire-and-forget for lowest latency
        rb = await ensure_pitch_mod(k, body.semitones, full=False)
        _state["connected"] = True
        return {"ok": True, "pitch": rb, "requested": body.semitones}

    return await with_k(_do)


@app.post("/api/amp")
async def set_amp(body: AmpIn):
    if body.field not in AMP_FIELDS:
        raise HTTPException(400, f"unknown field, use one of {AMP_FIELDS}")

    async def _do(k: KatanaBLE):
        off = AMP_FIELDS.index(body.field)
        # fire-and-forget write, no readback
        await k.write_bytes(ADDR["amp"] + off, [body.value & 0x7F], gap=0.0, pace=0.0)
        _state["connected"] = True
        return {"ok": True, "field": body.field, "value": body.value}

    return await with_k(_do)


@app.get("/api/presets")
async def list_presets():
    files = sorted(PRESETS.glob("*.json")) if PRESETS.exists() else []
    out = []
    for f in files:
        try:
            meta = json.loads(f.read_text())
            amp = meta.get("amp") or {}
            out.append(
                {
                    "id": f.name,
                    "name": meta.get("name", f.stem),
                    "notes": meta.get("notes", ""),
                    "song": meta.get("song", ""),
                    "tuning": meta.get("tuning", ""),
                    "amp": {
                        "type": amp.get("type"),
                        "type_name": amp.get("type_name"),
                        "gain": amp.get("gain"),
                        "volume": amp.get("volume"),
                    },
                }
            )
        except Exception:
            out.append(
                {
                    "id": f.name,
                    "name": f.stem,
                    "notes": "",
                    "song": "",
                    "tuning": "",
                    "amp": {},
                }
            )
    return out


@app.post("/api/presets/{preset_id:path}/load")
async def load_preset(preset_id: str):
    path = PRESETS / preset_id
    if not path.exists() or path.suffix != ".json":
        raise HTTPException(404, "preset not found")

    async def _do(k: KatanaBLE):
        preset = json.loads(path.read_text())
        # Full .tsl dumps can be loud — soft cap unless preset volume already lower
        await k.apply_preset(preset, volume_cap=50)
        p = await k.read_status_light() if hasattr(k, "read_status_light") else await k.read_patch()
        # prefer light status; fall back
        if "amp" not in p:
            p = await k.read_patch()
        try:
            d = await k.request(0x20001C48, 12, timeout=1.5)
            _state["pitch"] = d[2] - 24
        except Exception:
            pass
        _state["connected"] = True
        _state["active_preset"] = preset_id
        return {
            "ok": True,
            "id": preset_id,
            "name": p.get("name"),
            "pitch": _state.get("pitch"),
            "amp": p.get("amp"),
            "full": bool(preset.get("raw_blocks")),
            "chain": (preset.get("chain") or {}).get("label"),
        }

    return await with_k(_do)


@app.patch("/api/presets/{preset_id:path}")
async def patch_preset(preset_id: str, body: PresetPatchIn):
    """Persist slider tweaks into the preset file (volume, EQ, pitch…)."""
    # prevent path traversal
    path = (PRESETS / preset_id).resolve()
    if not str(path).startswith(str(PRESETS.resolve())) or path.suffix != ".json":
        raise HTTPException(400, "invalid preset id")
    if not path.exists():
        raise HTTPException(404, "preset not found")

    try:
        data = json.loads(path.read_text())
    except Exception as e:
        raise HTTPException(500, f"cannot read preset: {e}") from e

    if body.amp:
        amp = data.setdefault("amp", {})
        for k, v in body.amp.items():
            if v is None:
                continue
            if k in ("type_name",):
                amp[k] = str(v)
            else:
                try:
                    amp[k] = int(v)
                except (TypeError, ValueError):
                    continue

    if body.sw:
        sw = data.setdefault("sw", {})
        for k, v in body.sw.items():
            sw[k] = int(v)

    if body.pitch_semitones is not None:
        fx = data.setdefault("fx", {})
        fx["pitch_semitones"] = int(body.pitch_semitones)
        # keep type as pitch shifter if present
        fx.setdefault("type", 11)
        fx.setdefault("type_name", "pitch_shifter")

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    _state["active_preset"] = preset_id
    return {
        "ok": True,
        "id": preset_id,
        "amp": data.get("amp"),
        "pitch_semitones": (data.get("fx") or {}).get("pitch_semitones"),
    }


def main():
    import uvicorn

    print("Katana UI → http://127.0.0.1:8765")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
