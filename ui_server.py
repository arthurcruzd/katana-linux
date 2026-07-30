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
_connect_task: asyncio.Task | None = None
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
    """Run one BLE operation; commands arriving during connect wait for it."""
    if require_conn and (not _state.get("connected") or _katana is None):
        task = _connect_task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=70.0)
            except asyncio.TimeoutError as e:
                raise HTTPException(504, "conexão ainda não terminou") from e
            except Exception as e:
                raise HTTPException(500, f"conexão falhou: {e}") from e

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
        # A failed readback does not mean writes/link are dead. Ask BlueZ.
        link_up = False
        if _katana is not None:
            try:
                link_up = await _katana._is_connected()
            except Exception:
                pass
        _state["connected"] = bool(link_up)
        _state["error"] = "" if link_up else err
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
        # Readback can time out while the BlueZ link and writes remain healthy.
        if _state.get("connected") and _katana is not None:
            return JSONResponse(
                {
                    "connected": True,
                    "name": _state.get("name") or "",
                    "pitch": _state.get("pitch", 0),
                    "amp": None,
                    "sw": None,
                    "error": "",
                    "warning": str(he.detail),
                    "busy": _state.get("busy") or "",
                }
            )
        return JSONResponse(_snap_disconnected(str(he.detail)))


async def _run_connect() -> dict:
    """One durable connect attempt shared by every HTTP caller."""
    global _katana
    async with _connect_lock:
        # An initialized link is enough. A readback timeout is only a warning.
        if _state.get("connected") and _katana is not None:
            try:
                if await _katana._is_connected():
                    return {
                        "ok": True,
                        "connected": True,
                        "pitch": _state.get("pitch"),
                        "name": _state.get("name") or "",
                    }
            except Exception:
                pass
            _state["connected"] = False

        _state["busy"] = "connecting"
        _state["error"] = ""
        try:
            try:
                k = await _connect_ble(force=False, timeout=20.0)
            except Exception as soft_err:
                _state["error"] = f"soft: {soft_err}"
                k = await _connect_ble(force=True, timeout=35.0)

            async with _ops_lock:
                _katana = k
                _state["connected"] = True
                _state["pitch_armed"] = False
                pitch = int(_state.get("pitch", -1) or -1)
                try:
                    await asyncio.wait_for(
                        ensure_pitch_mod(k, pitch, full=True), timeout=6.0
                    )
                except Exception:
                    pass
                name = _state.get("name") or ""
                try:
                    p = await asyncio.wait_for(k.read_status_light(), timeout=3.5)
                    name = p.get("name") or name
                    _state["name"] = name
                    if p.get("pitch") is not None:
                        _state["pitch"] = p["pitch"]
                except Exception:
                    # Readback is optional; the link/notify setup succeeded.
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
            raise RuntimeError(_state["error"]) from e
        finally:
            _state["busy"] = ""


@app.post("/api/connect")
async def connect():
    global _connect_task
    # Concurrent clicks/tabs await the SAME attempt; no 409 and no orphan connect.
    if _connect_task is None or _connect_task.done():
        _connect_task = asyncio.create_task(_run_connect())
    task = _connect_task
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=70.0)
    except asyncio.TimeoutError as e:
        raise HTTPException(504, "conexão ainda em andamento — aguarde") from e
    except Exception as e:
        raise HTTPException(500, str(e)) from e
    finally:
        if task.done() and _connect_task is task:
            _connect_task = None


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
            # Writes completed: readback is diagnostic, never grounds for failure.
            p = {
                "name": preset.get("name") or path.stem,
                "amp": preset.get("amp") or {},
            }
            readback_ok = False
            warning = ""
            try:
                live = await k.read_status_light()
                if live:
                    p = live
                    readback_ok = True
            except Exception as e:
                warning = f"preset aplicado; leitura de confirmação indisponível: {e}"
            try:
                d = await k.request(0x20001C48, 12, timeout=1.5)
                _state["pitch"] = d[2] - 24
            except Exception:
                fx = preset.get("fx") or {}
                if fx.get("pitch_semitones") is not None:
                    _state["pitch"] = int(fx["pitch_semitones"])
            _state["name"] = p.get("name") or preset.get("name") or ""
            _state["active_preset"] = preset_id
            _state["connected"] = True
            return {
                "ok": True,
                "id": preset_id,
                "name": p.get("name") or preset.get("name"),
                "pitch": _state.get("pitch"),
                "amp": p.get("amp") or preset.get("amp") or {},
                "full": bool(preset.get("raw_blocks")),
                "chain": (preset.get("chain") or {}).get("label"),
                "readback_ok": readback_ok,
                "warning": warning,
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
