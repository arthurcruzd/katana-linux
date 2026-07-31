#!/usr/bin/env python3
"""Headless Chrome stress test for the real Katana UI."""
from __future__ import annotations

import atexit
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765"
BAD_WORDS = ("BLE ocupado", "ocupado (ble)", "já conectando", "timeout")


def main() -> int:
    # The real UI autosaves. Snapshot/restore so stress tests never mutate the library.
    preset_dir = Path(__file__).resolve().parents[1] / "presets"
    snapshot = {p: p.read_bytes() for p in preset_dir.glob("*.json")}

    def restore_presets():
        for path, data in snapshot.items():
            path.write_bytes(data)

    atexit.register(restore_presets)

    responses: list[dict] = []
    console_errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path="/usr/bin/google-chrome",
            args=[
                "--no-sandbox", "--disable-dev-shm-usage",
                "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
            ],
        )
        page = browser.new_page(viewport={"width": 1600, "height": 900})

        def on_response(r):
            if "/api/" in r.url:
                responses.append({"status": r.status, "url": r.url})

        page.on("response", on_response)
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" and "404" not in m.text else None)
        page.goto(BASE, wait_until="networkidle", timeout=15_000)
        page.wait_for_selector(".card", timeout=10_000)
        page.wait_for_selector("#btnLive", timeout=2_000)
        page.wait_for_selector("#btnTuner", timeout=2_000)
        assert page.evaluate("typeof prepareLive === 'function' && liveSlots instanceof Map")
        detected = page.evaluate("""
          () => {
            const sr = 48000, hz = 110, n = 8192;
            const samples = new Float32Array(n);
            for (let i = 0; i < n; i++) samples[i] = 0.8 * Math.sin(2 * Math.PI * hz * i / sr);
            const frequency = detectPitchYin(samples, sr);
            return { frequency, info: pitchInfo(frequency) };
          }
        """)
        assert abs(detected["frequency"] - 110) < 0.5, detected
        assert detected["info"]["note"] == "A2", detected
        assert abs(detected["info"]["cents"]) <= 2, detected
        guitar_notes = page.evaluate("""
          () => [73.42, 82.41, 329.63].map(hz => {
            const sr = 48000, samples = new Float32Array(8192);
            for (let i = 0; i < samples.length; i++) samples[i] = 0.7 * Math.sin(2 * Math.PI * hz * i / sr);
            const frequency = detectPitchYin(samples, sr);
            return pitchInfo(frequency).note;
          })
        """)
        assert guitar_notes == ["D2", "E2", "E4"], guitar_notes
        assert page.evaluate("detectPitchYin(new Float32Array(4096), 48000)") is None

        page.locator("#btnTuner").click()
        page.wait_for_function("document.querySelector('#tunerStatus').textContent.includes('ouvindo')", timeout=10_000)
        assert page.locator("#tunerPanel").evaluate("el => el.open")
        page.locator("#btnTunerStop").click()
        assert page.locator("#tunerStatus").inner_text() == "microfone desligado"
        page.locator(".tuner-close").click()
        assert not page.locator("#tunerPanel").evaluate("el => el.open")

        # Connect if needed.
        if page.locator("#conn").inner_text() != "conectado":
            page.locator("#btnConnect").click()
            page.wait_for_function(
                "document.querySelector('#conn').textContent === 'conectado' || document.querySelector('#err').textContent.length > 0",
                timeout=50_000,
            )
        if page.locator("#conn").inner_text() != "conectado":
            print("FAIL connect:", page.locator("#err").inner_text())
            browser.close()
            return 2

        cards = page.locator(".card")
        n = cards.count()
        print("cards", n, "connected")

        # Normal use, intentionally faster than a human: click 5 presets.
        for i in range(min(5, n)):
            cards.nth(i).click()
            page.wait_for_timeout(70)

        # Keyboard preset switch bursts; repeat-filter is tested by multiple presses.
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(60)
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(60)
        page.keyboard.press("ArrowLeft")

        # While load is queued, adjust volume/pitch/EQ. The latest values should win.
        for key in ("ArrowUp", "ArrowUp", "ArrowDown", "Shift+ArrowUp", "ArrowDown"):
            page.keyboard.press(key)
            page.wait_for_timeout(25)
        page.evaluate("setPitch(-2); setPitch(-1); setPitch(0); setPitch(-1)")
        page.evaluate("setAmp('gain', 41); setAmp('gain', 43); setAmp('middle', 52); setAmp('presence', 48)")

        # Last preset request and final intended values.
        last = min(2, n - 1)
        cards.nth(last).click()
        page.wait_for_timeout(100)
        page.evaluate("setVol(34); setVol(36); setVol(38); setPitch(0); setPitch(-1)")

        # Drain all UI queues.
        page.wait_for_function(
            "!loadInflight && !volInflight && !pitchInflight && Object.values(ampInflight).every(v => !v)",
            timeout=180_000,
        )
        page.wait_for_timeout(1_000)

        err = page.locator("#err").inner_text().strip()
        conn = page.locator("#conn").inner_text().strip()
        msg = page.locator("#msg").inner_text().strip()
        selected = page.locator(".card.selected .name").inner_text() if page.locator(".card.selected .name").count() else ""
        final = page.evaluate("({presetId: current.presetId, volume: current.volume, pitch: current.pitch, gen: bleGen})")
        bad_http = [r for r in responses if r["status"] >= 400]
        bad_text = [w for w in BAD_WORDS if w.lower() in err.lower()]

        report = {
            "connected": conn,
            "message": msg,
            "error": err,
            "selected": selected,
            "final": final,
            "http_errors": bad_http,
            "console_errors": console_errors,
            "api_responses": len(responses),
        }
        out = Path(__file__).with_name("browser_stress_last.json")
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        browser.close()

    ok = conn == "conectado" and not bad_http and not bad_text and not console_errors and final["volume"] == 38 and final["pitch"] == -1
    print("PASS" if ok else "FAIL", out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
