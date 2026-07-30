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

from katana_ble import ADDR, AMP_FIELDS, KatanaBLE

ROOT = Path(__file__).resolve().parent
PRESETS = ROOT / "presets"

# _ops_lock serializes BLE I/O only (short holds)
# _connect_lock prevents parallel connect attempts
_ops_lock = asyncio.Lock()
_connect_lock = asyncio.Lock()
_katana: KatanaBLE | None = None
_state = {
    "connected": False,
    "pitch": -1,
    "name": "",
    "error": "",
    "pitch_armed": False,
    "busy": "",  # connecting | loading | ""
}


def _snap_disconnected(extra_error: str = "") -> dict:
    return {
        "connected": False,
        "name": _state.get("name") or "",
        "pitch": _state.get("pitch", 0),
        "amp": None,
        "sw": None,
        "error": extra_error or _state.get("error") or "",
        "busy": _state.get("busy") or "",
    }


async def _connect_ble(*, force: bool = False, timeout: float = 25.0) -> KatanaBLE:
    """Open BLE outside ops lock. Caller installs into _katana."""
    global _katana
    if force and _katana is not None:
        try:
            await asyncio.wait_for(_katana.disconnect(drop_link=True), timeout=4.0)
        except Exception:
            pass
        _katana = None
        _state["connected"] = False
        _state["pitch_armed"] = False
        await asyncio.sleep(0.3)

    if _katana is not None and _state.get("connected"):
        return _katana

    k = KatanaBLE()
    try:
        await asyncio.wait_for(k.connect(force=force), timeout=timeout)
    except asyncio.TimeoutError as e:
        try:
            await k.disconnect(drop_link=True)
        except Exception:
            pass
        raise RuntimeError(
            "timeout ao conectar no BT-DUAL. "
            "Luz MIDI ok? App do celular fechado?"
        ) from e
    return k


async def with_ops(fn, *, wait_busy: float = 10.0, require_conn: bool = True):
    """Run fn(k) holding ops lock briefly. Does not connect (unless require_conn=False)."""
    deadline = asyncio.get_event_loop().time() + wait_busy
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise HTTPException(
                503, f"ocupado ({_state.get('busy') or 'ble'}) — aguarde"
            )
        try:
            await asyncio.wait_for(_ops_lock.acquire(), timeout=min(1.5, remaining))
            break
        except asyncio.TimeoutError:
            await asyncio.sleep(0.04)
            continue
    try:
        if require_conn:
            if not _state.get("connected") or _katana is None:
                raise HTTPException(400, "não conectado — clique em Conectar")
            k = _katana
        else:
            k = _katana
        return await fn(k)
    except HTTPException:
        raise
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        # transport death
        if "Timeout" in type(e).__name__ or "DT1" in str(e) or "Connect" in str(e):
            _state["connected"] = False
            _state["error"] = err
        raise HTTPException(500, err) from e
    finally:
        _ops_lock.release()


async def ensure_pitch_mod(k: KatanaBLE, semis: int, *, full: bool = False) -> int:
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
    amp: dict[str, int | str | float | None] | None = None
    pitch_semitones: int | None = Field(default=None, ge=-24, le=24)
    sw: dict[str, int] | None = None


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((ROOT / "ui.html").read_text(encoding="utf-8"))


@app.get("/api/status")
async def status():
    if _state.get("busy") == "connecting":
        return _snap_disconnected()
    if not _state.get("connected") or _katana is None:
        return _snap_disconnected()
    # Never report a false disconnect while a preset/control owns BLE.
    if _ops_lock.locked() or _state.get("busy"):
        return {
            "connected": True,
            "name": _state.get("name") or "",
            "pitch": _state.get("pitch", 0),
            "amp": None,
            "sw": None,
            "error": "",
            "busy": _state.get("busy") or "ble",
        }

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
            "busy": _state.get("busy") or "",
        }

    try:
        return await with_ops(_do, wait_busy=2.5)
    except HTTPException as he:
        return JSONResponse(_snap_disconnected(str(he.detail)))


@app.post("/api/connect")
async def connect():
    global _katana
    # Wait if another connect is running
    if _connect_lock.locked():
        for _ in range(50):
            if not _connect_lock.locked():
                break
            await asyncio.sleep(0.15)
        if _connect_lock.locked():
            raise HTTPException(409, "já conectando")

    async with _connect_lock:
        if _state.get("connected") and _katana is not None:
            # already up — just ping
            try:
                async def _ping(k):
                    return await k.read_status_light()

                p = await with_ops(_ping, wait_busy=3.0)
                return {
                    "ok": True,
                    "connected": True,
                    "pitch": _state.get("pitch"),
                    "name": p.get("name"),
                }
            except Exception:
                _state["connected"] = False

        _state["busy"] = "connecting"
        _state["error"] = ""
        try:
            # BLE connect OUTSIDE ops lock so amp/pitch aren't blocked for 30s
            try:
                k = await _connect_ble(force=False, timeout=18.0)
            except Exception as soft_err:
                _state["error"] = f"soft: {soft_err}"
                k = await _connect_ble(force=True, timeout=30.0)

            # install under ops lock briefly
            async with _ops_lock:
                _katana = k
                _state["connected"] = True
                _state["pitch_armed"] = False
                pitch = int(_state.get("pitch", -1) or -1)
                try:
                    await asyncio.wait_for(ensure_pitch_mod(k, pitch, full=True), timeout=6.0)
                except Exception:
                    pass
                name = ""
                try:
                    p = await asyncio.wait_for(k.read_status_light(), timeout=3.5)
                    name = p.get("name") or ""
                    _state["name"] = name
                    if p.get("pitch") is not None:
                        _state["pitch"] = p["pitch"]
                except Exception:
                    pass
                _state["error"] = ""
                return {
                    "ok": True,
                    "connected": True,
                    "pitch": _state["pitch"],
                    "name": name,
                }
        except Exception as e:
            _state["connected"] = False
            _state["pitch_armed"] = False
            _state["error"] = f"{type(e).__name__}: {e}"
            raise HTTPException(500, _state["error"]) from e
        finally:
            _state["busy"] = ""


@app.post("/api/pitch")
async def set_pitch(body: PitchIn):
    async def _do(k: KatanaBLE):
        rb = await ensure_pitch_mod(k, body.semitones, full=False)
        return {"ok": True, "pitch": rb, "requested": body.semitones}

    return await with_ops(_do, wait_busy=6.0)


@app.post("/api/amp")
async def set_amp(body: AmpIn):
    if body.field not in AMP_FIELDS:
        raise HTTPException(400, f"unknown field, use one of {AMP_FIELDS}")

    async def _do(k: KatanaBLE):
        off = AMP_FIELDS.index(body.field)
        await k.write_bytes(ADDR["amp"] + off, [body.value & 0x7F], gap=0.0, pace=0.0)
        return {"ok": True, "field": body.field, "value": body.value}

    return await with_ops(_do, wait_busy=6.0)


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
                    "full": bool(meta.get("raw_blocks")),
                    "chain": (meta.get("chain") or {}).get("label"),
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
                    "full": False,
                }
            )
    return out


@app.post("/api/presets/{preset_id:path}/load")
async def load_preset(preset_id: str):
    path = PRESETS / preset_id
    if not path.exists() or path.suffix != ".json":
        raise HTTPException(404, "preset not found")

    async def _do(k: KatanaBLE):
        _state["busy"] = "loading"
        try:
            preset = json.loads(path.read_text())
            _state["pitch_armed"] = False
            await k.apply_preset(preset, volume_cap=50)
            # light readback
            try:
                p = await k.read_status_light()
            except Exception:
                p = await k.read_patch()
            try:
                d = await k.request(0x20001C48, 12, timeout=1.5)
                _state["pitch"] = d[2] - 24
            except Exception:
                pass
            _state["name"] = p.get("name") or preset.get("name") or ""
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
        finally:
            if _state.get("busy") == "loading":
                _state["busy"] = ""

    return await with_ops(_do, wait_busy=90.0)


@app.patch("/api/presets/{preset_id:path}")
async def patch_preset(preset_id: str, body: PresetPatchIn):
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
        fx.setdefault("type", 11)
        fx.setdefault("type_name", "pitch_shifter")

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
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
