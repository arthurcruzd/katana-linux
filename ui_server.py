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
}


async def get_k() -> KatanaBLE:
    global _katana
    if _katana is None or not _state["connected"]:
        k = KatanaBLE()
        await k.connect()
        _katana = k
        _state["connected"] = True
        _state["error"] = ""
    return _katana


async def ensure_pitch_mod(k: KatanaBLE, semis: int) -> None:
    """Arm MOD pitch shifter params and turn MOD on."""
    semis = max(-24, min(24, int(semis)))
    # MOD on, keep delay/reverb as-is if possible
    try:
        sw = await k.request(ADDR["sw"], 0x06)
        sw[1] = 1  # mod
        # leave fx as-is (usually off for clean pitch)
        await k.write_bytes(ADDR["sw"], sw)
    except Exception:
        await k.write_bytes(ADDR["sw"], [0, 1, 0, 1, 0, 1])

    # Best-effort type = pitch shifter on MOD slots
    for rel in (0x1000, 0x1200, 0x1400):
        try:
            await k.write_bytes(0x20000000 + rel, [11])
        except Exception:
            pass
    await asyncio.sleep(0.15)

    for det in (0x1C00, 0x1E00, 0x2000):
        base = 0x20000000 + det
        await k.write_bytes(base + PS_VOICE, [0])
        await k.write_bytes(base + PS_MODE1, [1])
        await k.write_bytes(base + PS_PITCH1, [(semis + 24) & 0x7F])
        await k.write_bytes(base + PS_FINE1, [50])
        await k.write_bytes(base + PS_PREDELAY1, enc_4x4(0))
        await k.write_bytes(base + PS_LEVEL1, [100])
        await k.write_bytes(base + PS_DIRECT_MIX, [0])

    _state["pitch"] = semis


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


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.get("/api/status")
async def status():
    async with _lock:
        try:
            k = await get_k()
            p = await k.read_patch()
            # read live pitch from DET1
            try:
                d = await k.request(0x20001C48, 20)
                pitch = d[2] - 24
                _state["pitch"] = pitch
            except Exception:
                pitch = _state.get("pitch", 0)
            _state["name"] = p.get("name", "")
            return {
                "connected": True,
                "name": p.get("name"),
                "pitch": pitch,
                "amp": p.get("amp"),
                "sw": p.get("sw"),
                "error": "",
            }
        except Exception as e:
            _state["connected"] = False
            _state["error"] = f"{type(e).__name__}: {e}"
            return {
                "connected": False,
                "name": "",
                "pitch": _state.get("pitch", 0),
                "amp": None,
                "sw": None,
                "error": _state["error"],
            }


@app.post("/api/connect")
async def connect():
    async with _lock:
        global _katana
        try:
            if _katana:
                try:
                    await _katana.disconnect()
                except Exception:
                    pass
                _katana = None
            k = await get_k()
            await ensure_pitch_mod(k, _state.get("pitch", -1))
            return {"ok": True, "connected": True}
        except Exception as e:
            _state["connected"] = False
            raise HTTPException(500, str(e)) from e


@app.post("/api/pitch")
async def set_pitch(body: PitchIn):
    async with _lock:
        try:
            k = await get_k()
            await ensure_pitch_mod(k, body.semitones)
            # readback
            d = await k.request(0x20001C48, 12)
            rb = d[2] - 24
            return {"ok": True, "pitch": rb, "requested": body.semitones}
        except Exception as e:
            _state["connected"] = False
            raise HTTPException(500, str(e)) from e


@app.post("/api/amp")
async def set_amp(body: AmpIn):
    if body.field not in AMP_FIELDS:
        raise HTTPException(400, f"unknown field, use one of {AMP_FIELDS}")
    async with _lock:
        try:
            k = await get_k()
            off = AMP_FIELDS.index(body.field)
            await k.write_bytes(ADDR["amp"] + off, [body.value & 0x7F])
            await asyncio.sleep(0.15)
            amp = await k.request(ADDR["amp"], 0x0A)
            return {"ok": True, "field": body.field, "value": amp[off]}
        except Exception as e:
            _state["connected"] = False
            raise HTTPException(500, str(e)) from e


@app.get("/api/presets")
async def list_presets():
    files = sorted(PRESETS.glob("*.json")) if PRESETS.exists() else []
    out = []
    for f in files:
        try:
            meta = json.loads(f.read_text())
            out.append({"id": f.name, "name": meta.get("name", f.stem), "notes": meta.get("notes", "")})
        except Exception:
            out.append({"id": f.name, "name": f.stem, "notes": ""})
    return out


@app.post("/api/presets/{preset_id:path}/load")
async def load_preset(preset_id: str):
    path = PRESETS / preset_id
    if not path.exists() or path.suffix != ".json":
        raise HTTPException(404, "preset not found")
    async with _lock:
        try:
            k = await get_k()
            preset = json.loads(path.read_text())
            await k.apply_preset(preset)
            p = await k.read_patch()
            try:
                d = await k.request(0x20001C48, 12)
                _state["pitch"] = d[2] - 24
            except Exception:
                pass
            return {"ok": True, "name": p.get("name"), "pitch": _state["pitch"], "amp": p.get("amp")}
        except Exception as e:
            _state["connected"] = False
            raise HTTPException(500, str(e)) from e


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Katana Control</title>
<style>
  :root {
    --bg: #0f1115;
    --panel: #1a1d24;
    --line: #2a2f3a;
    --text: #e8eaed;
    --muted: #9aa3b2;
    --accent: #3dd6ff;
    --accent2: #ff4d6d;
    --ok: #3dd68c;
    --warn: #f5a524;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh;
    font-family: "Segoe UI", system-ui, sans-serif;
    background: radial-gradient(1200px 600px at 20% -10%, #1b2436 0%, var(--bg) 55%);
    color: var(--text);
  }
  .wrap { max-width: 720px; margin: 0 auto; padding: 28px 20px 60px; }
  h1 { font-weight: 600; letter-spacing: 0.04em; font-size: 1.35rem; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 0.9rem; margin-bottom: 22px; }
  .card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 20px 22px 22px;
    margin-bottom: 16px;
    box-shadow: 0 10px 40px rgba(0,0,0,.25);
  }
  .row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .badge {
    display: inline-flex; align-items: center; gap: 8px;
    font-size: 0.82rem; padding: 6px 10px; border-radius: 999px;
    border: 1px solid var(--line); color: var(--muted);
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--warn); }
  .dot.on { background: var(--ok); box-shadow: 0 0 10px var(--ok); }
  .dot.off { background: var(--accent2); }
  label.block { display: block; color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 10px; }
  .pitch-val {
    font-size: 3.2rem; font-weight: 700; font-variant-numeric: tabular-nums;
    text-align: center; margin: 8px 0 4px; color: var(--accent);
  }
  .pitch-note { text-align: center; color: var(--muted); font-size: 0.95rem; margin-bottom: 18px; }
  input[type=range] {
    -webkit-appearance: none; appearance: none;
    width: 100%; height: 8px; border-radius: 999px;
    background: linear-gradient(90deg, #5b6cff, var(--accent));
    outline: none;
  }
  input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none;
    width: 28px; height: 28px; border-radius: 50%;
    background: #fff; border: 3px solid var(--accent);
    box-shadow: 0 2px 12px rgba(61,214,255,.45);
    cursor: pointer;
  }
  .ticks { display: flex; justify-content: space-between; color: var(--muted); font-size: 0.75rem; margin-top: 8px; }
  .btns { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
  button {
    background: #232833; color: var(--text); border: 1px solid var(--line);
    border-radius: 10px; padding: 10px 14px; font-size: 0.9rem; cursor: pointer;
  }
  button:hover { border-color: var(--accent); color: #fff; }
  button.primary { background: linear-gradient(135deg, #1e6b88, #178a9e); border-color: transparent; }
  button.ghost { background: transparent; }
  .presets { display: grid; grid-template-columns: 1fr; gap: 8px; }
  .preset {
    text-align: left; padding: 12px 14px;
    display: flex; flex-direction: column; gap: 2px;
  }
  .preset strong { font-size: 0.95rem; }
  .preset span { color: var(--muted); font-size: 0.78rem; }
  .sliders { display: grid; gap: 14px; }
  .slabel { display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 4px; }
  .err { color: var(--accent2); font-size: 0.85rem; margin-top: 10px; white-space: pre-wrap; }
  .okmsg { color: var(--ok); font-size: 0.85rem; margin-top: 8px; min-height: 1.2em; }
</style>
</head>
<body>
<div class="wrap">
  <div class="row" style="margin-bottom:18px">
    <div>
      <h1>KATANA CONTROL</h1>
      <div class="sub">Pitch shifter + presets · BT-DUAL</div>
    </div>
    <div class="badge"><span class="dot" id="dot"></span><span id="conn">…</span></div>
  </div>

  <div class="card">
    <div class="row">
      <div>
        <div style="color:var(--muted);font-size:.8rem;text-transform:uppercase;letter-spacing:.08em">Patch</div>
        <div id="pname" style="font-size:1.15rem;margin-top:4px">—</div>
      </div>
      <div class="btns" style="margin:0">
        <button class="primary" onclick="connect()">Conectar</button>
        <button class="ghost" onclick="refresh()">Atualizar</button>
      </div>
    </div>
    <div class="err" id="err"></div>
  </div>

  <div class="card">
    <label class="block">Tom (semitons)</label>
    <div class="pitch-val" id="pval">0</div>
    <div class="pitch-note" id="pnote">E → E</div>
    <input id="pitch" type="range" min="-12" max="12" step="1" value="0"
           oninput="onSlide(this.value)" onchange="commitPitch(this.value)"/>
    <div class="ticks"><span>-12</span><span>-4</span><span>0</span><span>+4</span><span>+12</span></div>
    <div class="btns">
      <button onclick="setPitch(-4)">−4</button>
      <button onclick="setPitch(-1)">−1 (Eb)</button>
      <button onclick="setPitch(0)">0</button>
      <button onclick="setPitch(1)">+1</button>
      <button onclick="setPitch(-12)">−12</button>
    </div>
    <div class="okmsg" id="msg"></div>
  </div>

  <div class="card">
    <label class="block">Amp rápido</label>
    <div class="sliders" id="ampSliders"></div>
  </div>

  <div class="card">
    <label class="block">Presets</label>
    <div class="presets" id="presets">carregando…</div>
  </div>
</div>
<script>
const NOTE = ['C','C#','D','Eb','E','F','F#','G','Ab','A','Bb','B'];
function openTo(semi) {
  // open E string sounding
  const idx = (4 + semi + 120) % 12; // E=4
  return NOTE[idx];
}
function fmtPitch(v) {
  v = +v;
  return (v > 0 ? '+' : '') + v;
}
function onSlide(v) {
  document.getElementById('pval').textContent = fmtPitch(v);
  document.getElementById('pnote').textContent = `forma em E → soa ${openTo(+v)}  ·  Mi aberto = ${openTo(+v)}`;
}
let pitchTimer = null;
function commitPitch(v) {
  clearTimeout(pitchTimer);
  pitchTimer = setTimeout(() => setPitch(+v), 80);
}
async function setPitch(v) {
  v = +v;
  document.getElementById('pitch').value = v;
  onSlide(v);
  document.getElementById('msg').textContent = 'enviando…';
  try {
    const r = await fetch('/api/pitch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({semitones: v})
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || JSON.stringify(j));
    document.getElementById('msg').textContent = `tom ${fmtPitch(j.pitch)} ok`;
    setConn(true);
  } catch (e) {
    document.getElementById('msg').textContent = '';
    document.getElementById('err').textContent = String(e.message || e);
    setConn(false);
  }
}
function setConn(on, err) {
  const dot = document.getElementById('dot');
  const conn = document.getElementById('conn');
  dot.className = 'dot ' + (on ? 'on' : 'off');
  conn.textContent = on ? 'conectado' : 'desconectado';
  if (err) document.getElementById('err').textContent = err;
  else if (on) document.getElementById('err').textContent = '';
}
async function refresh() {
  try {
    const r = await fetch('/api/status');
    const j = await r.json();
    setConn(!!j.connected, j.error || '');
    document.getElementById('pname').textContent = j.name || '—';
    if (j.pitch !== undefined && j.pitch !== null) {
      document.getElementById('pitch').value = j.pitch;
      onSlide(j.pitch);
    }
    if (j.amp) renderAmp(j.amp);
  } catch (e) {
    setConn(false, String(e));
  }
}
async function connect() {
  document.getElementById('msg').textContent = 'conectando…';
  try {
    const r = await fetch('/api/connect', {method:'POST'});
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || JSON.stringify(j));
    document.getElementById('msg').textContent = 'conectado';
    await refresh();
  } catch (e) {
    setConn(false, String(e.message || e));
  }
}
const AMP_UI = [
  ['gain','Gain'],['volume','Volume'],['bass','Bass'],
  ['middle','Middle'],['treble','Treble'],['presence','Presence']
];
function renderAmp(amp) {
  const root = document.getElementById('ampSliders');
  root.innerHTML = '';
  for (const [key, label] of AMP_UI) {
    const val = amp[key] ?? 50;
    const div = document.createElement('div');
    div.innerHTML = `<div class="slabel"><span>${label}</span><span id="av_${key}">${val}</span></div>
      <input type="range" min="0" max="100" value="${val}" data-field="${key}"
        oninput="document.getElementById('av_${key}').textContent=this.value"
        onchange="setAmp('${key}', this.value)"/>`;
    root.appendChild(div);
  }
}
async function setAmp(field, value) {
  try {
    const r = await fetch('/api/amp', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({field, value: +value})
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || JSON.stringify(j));
    document.getElementById('av_'+field).textContent = j.value;
    setConn(true);
  } catch (e) {
    document.getElementById('err').textContent = String(e.message || e);
  }
}
async function loadPresets() {
  const r = await fetch('/api/presets');
  const list = await r.json();
  const root = document.getElementById('presets');
  root.innerHTML = '';
  for (const p of list) {
    const b = document.createElement('button');
    b.className = 'preset';
    b.innerHTML = `<strong>${p.name}</strong><span>${p.id}</span>`;
    b.onclick = () => loadPreset(p.id);
    root.appendChild(b);
  }
}
async function loadPreset(id) {
  document.getElementById('msg').textContent = 'carregando preset…';
  try {
    const r = await fetch('/api/presets/' + encodeURIComponent(id) + '/load', {method:'POST'});
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || JSON.stringify(j));
    document.getElementById('msg').textContent = 'preset: ' + (j.name || id);
    await refresh();
  } catch (e) {
    document.getElementById('err').textContent = String(e.message || e);
  }
}
onSlide(0);
loadPresets();
refresh();
setInterval(refresh, 12000);
</script>
</body>
</html>
"""


def main():
    import uvicorn

    print("Katana UI → http://127.0.0.1:8765")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
