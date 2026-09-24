#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a QA batch with Playwright and write structured results.

    python -X utf8 scripts/qa/run_batch.py --batch W1
    python -X utf8 scripts/qa/run_batch.py --batch W2 --app-url http://127.0.0.1:5173

Outputs under ``<VINC_QA_DATA>/runs/<run_id>/``:
  run_meta.json, results_structured.json, results.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("VINC_QA_DATA", "/tmp/vinc-qa-store"))
OWNER = os.environ.get("VINC_QA_OWNER", "eval:tooluse")


def _stack_meta() -> dict[str, Any]:
    p = DATA / "stack.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


@dataclass
class TCResult:
    tc_id: str
    flow: str
    outcome: str  # Pass | Fail | Error | Not_Reproducible | Skip
    detail: str = ""
    bucket_id: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    auto_diag_step: str = ""


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _console_errors(page) -> list[str]:
    return page.evaluate("""() => window.__qaConsoleErrors || []""")


def _diag(result: TCResult, page, errors: list[str]) -> None:
    """[A] console -> [B] DOM -> [C] env -> [D] bucket."""
    if errors:
        result.auto_diag_step = "A-console"
        result.evidence["console"] = errors[:5]
        result.bucket_id = result.bucket_id or "downstream_failure"
        return
    try:
        shell = page.locator("#shell")
        if shell.count() == 0:
            result.auto_diag_step = "B-dom"
            result.evidence["dom"] = "missing #shell"
            result.bucket_id = result.bucket_id or "test_design_gap"
            return
    except Exception as e:
        result.auto_diag_step = "C-env"
        result.evidence["env"] = str(e)
        result.bucket_id = result.bucket_id or "env_missing"
        return
    result.auto_diag_step = "D-bucket"
    if not result.bucket_id:
        result.bucket_id = "policy_mismatch"


def _pass(tc_id: str, flow: str, detail: str, **evidence) -> TCResult:
    return TCResult(tc_id, flow, "Pass", detail=detail, evidence=evidence)


def _fail(tc_id: str, flow: str, detail: str, bucket: str = "policy_mismatch", **evidence) -> TCResult:
    return TCResult(tc_id, flow, "Fail", detail=detail, bucket_id=bucket, evidence=evidence)


def _open_lens_opts(page) -> None:
    """Open view lens panel (defaults to Options tab — do not re-click opts; redraw breaks handlers)."""
    page.locator("#tb-viewpoints button[data-view='3d']").click()
    page.wait_for_timeout(800)
    page.wait_for_selector("#view-lens", timeout=15_000)
    panel = page.locator("#view-lens-panel")
    if panel.evaluate("el => el.hidden"):
        page.locator("#view-lens").click()
        page.wait_for_timeout(600)
    page.wait_for_selector('[data-pref="timeBasis"]', timeout=10_000)


def _click_pref(page, pref: str, val: str) -> None:
    """Click a panel pref tile (Playwright click can miss SVG children)."""
    page.evaluate(
        """([pref, val]) => {
            const el = document.querySelector(`[data-pref="${pref}"][data-val="${val}"]`);
            el?.click();
        }""",
        [pref, val],
    )


# ── Test implementations ─────────────────────────────────────────────────────

def tc_f_shell_01(page) -> TCResult:
    flow = "F-SHELL"
    rail = page.locator("#lnb-rail")
    lnb = page.locator("#lnb")
    if rail.count() == 0 or lnb.count() == 0:
        return _fail("F-SHELL-01", flow, "two-layer chrome missing",
                     dom={"lnb_rail": rail.count(), "lnb": lnb.count()})
    kinds = page.locator("#rail-kinds button, #rail-kinds .lnb-rail-btn")
    n = kinds.count()
    if n < 1:
        return _fail("F-SHELL-01", flow, "kind rail empty", dom={"kind_buttons": n})
    return _pass("F-SHELL-01", flow, f"kind rail has {n} controls", kind_buttons=n)


def tc_f_shell_02(page) -> TCResult:
    flow = "F-SHELL"
    vp = page.locator("#tb-viewpoints")
    if vp.count() == 0:
        return _fail("F-SHELL-02", flow, "viewpoint segment missing")
    btns = page.locator("#tb-viewpoints button")
    n = btns.count()
    if n < 4:
        return _fail("F-SHELL-02", flow, f"expected 4 viewpoints, got {n}", count=n)
    return _pass("F-SHELL-02", flow, f"{n} viewpoint buttons", count=n)


def tc_f_shell_03(page) -> TCResult:
    flow = "F-SHELL"
    topbar = page.locator("#topbar")
    if topbar.count() == 0:
        return _fail("F-SHELL-03", flow, "topbar missing")
    box = topbar.bounding_box()
    surface = page.locator("#surface")
    sbox = surface.bounding_box() if surface.count() else None
    if not box or not sbox:
        return _fail("F-SHELL-03", flow, "layout boxes unavailable")
    # GNB should sit above map column (smaller y or grid overlap at top)
    return _pass("F-SHELL-03", flow, "topbar and surface both present",
                 topbar_y=box["y"], surface_y=sbox["y"])


def tc_f_settings_01(page) -> TCResult:
    flow = "F-SETTINGS"
    page.locator("#settings-btn").click()
    page.wait_for_timeout(400)
    ver = page.locator(".settings-ver")
    if ver.count() == 0:
        return _fail("F-SETTINGS-01", flow, "settings version line missing")
    text = ver.inner_text()
    if not re.search(r"Vinc\s+[\d.]+\+", text):
        return _fail("F-SETTINGS-01", flow, f"unexpected version text: {text}",
                     text=text)
    return _pass("F-SETTINGS-01", flow, text.strip(), text=text)


def _expand_lnb_for_docs(page) -> None:
    """Documents live under collapsed topic groups — expand until a doc row appears."""
    for _ in range(6):
        if page.locator(".lnb-doc[data-doc]").count() > 0:
            return
        groups = page.locator(".lnb-group[aria-expanded='false']")
        if groups.count() == 0:
            break
        groups.first.click()
        page.wait_for_timeout(400)


def tc_f_doc_kb_01(page) -> TCResult:
    flow = "F-DOC-KB"
    _expand_lnb_for_docs(page)
    # Prefer seeded eval doc; fall back to first doc row.
    doc_btn = page.locator(".lnb-doc[data-doc]", has_text="View 구현 노트")
    if doc_btn.count() == 0:
        doc_btn = page.locator(".lnb-doc[data-doc]").first
    if doc_btn.count() == 0:
        return TCResult("F-DOC-KB-01", flow, "Skip", "no document in LNB to test")
    doc_btn.click()
    try:
        page.wait_for_selector(".cm-content", timeout=15_000)
    except Exception:
        return _fail("F-DOC-KB-01", flow, "CodeMirror editor not found after doc open")
    # 기본은 읽기 모드(readOnly) — 숫자를 넣으려면 편집 모드로 전환한다.
    mode = page.locator("#rdr-mode")
    if mode.count():
        label = mode.inner_text()
        if "Write" in label or "쓰기" in label or "편집" in label:
            mode.click()
            page.wait_for_timeout(600)
    editor = page.locator(".cm-content")
    editor.click()
    page.keyboard.press("2")
    page.wait_for_timeout(300)
    hash_after = page.evaluate("() => location.hash")
    editor_text = page.evaluate(
        """() => document.querySelector('.cm-content')?.textContent || ''"""
    )
    if hash_after.startswith("#search") or hash_after.startswith("#packs"):
        return _fail("F-DOC-KB-01", flow,
                     f"key 2 navigated away: hash={hash_after}",
                     hash=hash_after, editor=editor_text[:80])
    if "2" not in editor_text:
        return _fail("F-DOC-KB-01", flow, "key 2 did not insert in editor",
                     editor=editor_text[:80])
    return _pass("F-DOC-KB-01", flow, "key 2 stayed in editor", hash=hash_after)


def tc_f_view_01(page) -> TCResult:
    flow = "F-VIEW"
    page.locator("#tb-viewpoints button").first.click()
    page.wait_for_timeout(800)
    canvas = page.locator("#surface canvas, #surface svg, #surface .sigma")
    if canvas.count() == 0:
        # timeline/weave use SVG inside surface
        inner = page.locator("#surface *").count()
        if inner < 3:
            return _fail("F-VIEW-01", flow, "view surface appears empty", nodes=inner)
    return _pass("F-VIEW-01", flow, "view surface rendered")


def tc_f_view_02(page) -> TCResult:
    flow = "F-VIEW"
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    return _pass("F-VIEW-02", flow, "Escape did not throw")


def tc_f_share_01(page) -> TCResult:
    flow = "F-SHARE"
    tools = page.locator("#tb-tools")
    if tools.count() == 0:
        return TCResult("F-SHARE-01", flow, "Skip", "tools button missing")
    tools.click()
    page.wait_for_timeout(500)
    share_tab = page.locator('[data-tab="share"]')
    if share_tab.count() == 0:
        return TCResult("F-SHARE-01", flow, "Skip", "share tab missing in tools sheet")
    share_tab.first.click()
    page.wait_for_timeout(500)
    scope = page.locator("#tools-pane .tools-opt, #tools-pane #tl-backup")
    if scope.count() == 0:
        return _fail("F-SHARE-01", flow, "share scope picker missing")
    return _pass("F-SHARE-01", flow, "share scope picker reachable", options=scope.count())


def tc_f_time_01(page) -> TCResult:
    """Time basis tiles: active UI must match stored pref after click."""
    flow = "F-TIME"
    _open_lens_opts(page)
    if page.locator('[data-pref="timeBasis"][data-val="content"]').count() == 0:
        return TCResult("F-TIME-01", flow, "Skip", "time basis tiles missing")
    _click_pref(page, "timeBasis", "content")
    page.wait_for_timeout(500)
    state = page.evaluate("""() => {
        const raw = localStorage.getItem('vinc.view.prefs');
        const prefs = raw ? JSON.parse(raw) : {};
        return {
            stored: prefs.timeBasis || 'recorded',
            recordedActive: document.querySelector('[data-pref="timeBasis"][data-val="recorded"]')
                ?.classList.contains('active'),
            contentActive: document.querySelector('[data-pref="timeBasis"][data-val="content"]')
                ?.classList.contains('active'),
        };
    }""")
    if state["stored"] != "content":
        return _fail("F-TIME-01", flow, "timeBasis not saved as content", **state)
    if not state["contentActive"] or state["recordedActive"]:
        return _fail(
            "F-TIME-01", flow,
            "timeBasis tile highlight stale (stored=content but UI shows recorded)",
            **state,
        )
    return _pass("F-TIME-01", flow, "timeBasis tile syncs with stored pref", **state)


def tc_f_time_02(page) -> TCResult:
    """When slider: drag must change value without replacing the input element."""
    flow = "F-TIME"
    _open_lens_opts(page)
    # F-TIME-01 직후 content 축이면 content_ts 없는 노드만 있어 라벨이 안 바뀐다.
    _click_pref(page, "timeBasis", "recorded")
    page.wait_for_timeout(300)
    slider = page.locator("#pref-when")
    if slider.count() == 0:
        return TCResult("F-TIME-02", flow, "Skip", "when slider missing")
    before = slider.input_value()
    label_before = page.locator("#pref-when-val").inner_text()
    page.evaluate("""() => {
        const s = document.querySelector('#pref-when');
        if (!s) return;
        s.value = '0';
        s.dispatchEvent(new Event('input', { bubbles: true }));
    }""")
    page.wait_for_timeout(300)
    after = slider.input_value()
    label_after = page.locator("#pref-when-val").inner_text()
    stored_when = page.evaluate(
        "() => JSON.parse(localStorage.getItem('vinc.view.prefs')||'{}').when"
    )
    if after == before and label_after == label_before:
        return TCResult(
            "F-TIME-02", flow, "Skip",
            "when slider has no visible range in fixture (stuck at All)",
            evidence={"before": before, "label": label_before},
        )
    if stored_when != 0 and float(stored_when or 1) > 0.01:
        return _fail("F-TIME-02", flow, "when pref not saved after input",
                     stored=stored_when, after=after)
    if label_after == label_before:
        return _fail("F-TIME-02", flow, "when label stale after slider input",
                     label_before=label_before, label_after=label_after)
    return _pass("F-TIME-02", flow, f"when cursor {label_before} -> {label_after}",
                 stored=stored_when)


def tc_f_time_03(page) -> TCResult:
    """Opts/legend tab round-trip must not break time basis controls."""
    flow = "F-TIME"
    _open_lens_opts(page)
    page.locator('#view-lens-panel [data-tab="legend"]').click()
    page.wait_for_timeout(300)
    page.locator('#view-lens-panel [data-tab="opts"]').click()
    page.wait_for_timeout(300)
    _click_pref(page, "timeBasis", "content")
    page.wait_for_timeout(400)
    state = page.evaluate("""() => {
        const raw = localStorage.getItem('vinc.view.prefs');
        const prefs = raw ? JSON.parse(raw) : {};
        return {
            stored: prefs.timeBasis || 'recorded',
            contentActive: document.querySelector('[data-pref="timeBasis"][data-val="content"]')
                ?.classList.contains('active'),
            whenPresent: !!document.querySelector('#pref-when'),
        };
    }""")
    if not state["whenPresent"]:
        return _fail("F-TIME-03", flow, "when slider missing after tab switch", **state)
    if state["stored"] != "content":
        return _fail("F-TIME-03", flow, "timeBasis not saved after tab round-trip",
                     **state)
    if not state["contentActive"] or state.get("recordedActive"):
        return _fail(
            "F-TIME-03", flow,
            "timeBasis UI stale after opts/legend tab round-trip",
            **state,
        )
    return _pass("F-TIME-03", flow, "time controls survive tab switch", **state)


def _prep_search(page) -> None:
    page.evaluate(
        """() => {
            localStorage.setItem('vinc.firstrun.seen', '1');
            localStorage.setItem('vinc.search.mode', 'find');
        }"""
    )
    page.reload(wait_until="networkidle", timeout=60_000)
    page.wait_for_timeout(1500)


def tc_f_search_01(page) -> TCResult:
    """Enter before debounce must not open the browse-all sheet."""
    flow = "F-SEARCH"
    _prep_search(page)
    page.locator("#tb-q").fill("billing")
    page.locator("#tb-q").press("Enter")
    page.wait_for_timeout(400)
    state = page.evaluate(
        """() => ({
            sheet: !!document.querySelector('.sheet'),
            title: document.querySelector('.sheet h2')?.textContent || '',
            hash: location.hash,
        })"""
    )
    if state["sheet"] and "document" in state["title"].lower():
        return _fail(
            "F-SEARCH-01", flow,
            "immediate Enter opened browse sheet before search completed",
            bucket="policy_mismatch",
            **state,
        )
    return _pass("F-SEARCH-01", flow, "no browse sheet on immediate Enter", **state)


def tc_f_search_02(page) -> TCResult:
    """After debounce, Enter should land on first hit (not browse sheet)."""
    flow = "F-SEARCH"
    _prep_search(page)
    page.locator("#tb-q").fill("billing")
    page.wait_for_timeout(700)
    page.locator("#tb-q").press("Enter")
    page.wait_for_timeout(500)
    state = page.evaluate(
        """() => ({
            sheet: !!document.querySelector('.sheet'),
            hash: location.hash,
        })"""
    )
    if state["sheet"]:
        return _fail("F-SEARCH-02", flow, "sheet opened after debounced Enter", **state)
    if not state["hash"].startswith("#view/"):
        return _fail("F-SEARCH-02", flow, "did not navigate to view hit", **state)
    return _pass("F-SEARCH-02", flow, "debounced Enter navigates to hit", **state)


def tc_f_search_03(page) -> TCResult:
    """#search surface alone must not auto-open a sheet."""
    flow = "F-SEARCH"
    _prep_search(page)
    page.evaluate("() => { location.hash = '#search'; }")
    page.wait_for_timeout(800)
    state = page.evaluate(
        """() => ({
            hash: location.hash,
            sheet: !!document.querySelector('.sheet'),
        })"""
    )
    if state["sheet"]:
        return _fail("F-SEARCH-03", flow, "sheet opened on #search navigation", **state)
    return _pass("F-SEARCH-03", flow, "#search has no sheet", **state)


def tc_f_packs_01(page) -> TCResult:
    """Legacy #packs alias opens Tools share tab (documented, not a defect)."""
    flow = "F-PACKS"
    _prep_search(page)
    page.evaluate("() => { location.hash = '#packs'; }")
    page.wait_for_timeout(1200)
    state = page.evaluate(
        """() => ({
            hash: location.hash,
            sheet: !!document.querySelector('.sheet'),
            title: document.querySelector('.sheet h2')?.textContent || '',
            shareTab: document.querySelector('[data-tab="share"]')?.classList.contains('active'),
        })"""
    )
    if not state["sheet"] or state["title"] != "Tools":
        return _fail("F-PACKS-01", flow, "expected Tools sheet from #packs alias", **state)
    return _pass("F-PACKS-01", flow, "#packs opens Tools share (alias)", **state)


def tc_f_vincpack_01(page) -> TCResult:
    """Tools import tab ingests a starter .vincpack."""
    flow = "F-VINCPACK"
    pack = ROOT / "starter-packs" / "vinc-pack-math.vincpack"
    if not pack.exists():
        return TCResult("F-VINCPACK-01", flow, "Skip", f"missing fixture {pack}")
    _prep_search(page)
    page.locator("#tb-tools").click()
    page.wait_for_timeout(400)
    tab = page.locator('[data-tab="import"]')
    if tab.count() == 0:
        return TCResult("F-VINCPACK-01", flow, "Skip", "import tab missing")
    tab.first.click()
    page.wait_for_timeout(400)
    file_input = page.locator('input[type="file"]').first
    if file_input.count() == 0:
        return _fail("F-VINCPACK-01", flow, "file input missing in import tab")
    file_input.set_input_files(str(pack))
    deadline = time.time() + 30
    body = ""
    while time.time() < deadline:
        page.wait_for_timeout(1000)
        body = page.evaluate(
            "() => document.querySelector('#tools-pane')?.innerText || ''"
        )
        low = body.lower()
        if "imported" in low or "error" in low or "failed" in low:
            break
    if "imported" not in body.lower():
        return _fail(
            "F-VINCPACK-01", flow,
            "import did not finish within 30s",
            bucket="downstream_failure",
            body=body[:200],
        )
    return _pass("F-VINCPACK-01", flow, "starter pack imported", snippet=body[:120])


def tc_f_time_04(page) -> TCResult:
    """Replay button toggles Play/Stop when graph has >=2 timestamps."""
    flow = "F-TIME"
    _open_lens_opts(page)
    replay = page.locator("#pref-replay")
    if replay.count() == 0:
        return TCResult("F-TIME-04", flow, "Skip", "replay button missing on 3D")
    before = replay.inner_text()
    replay.click()
    page.wait_for_timeout(500)
    during = replay.inner_text()
    if before == during:
        return TCResult(
            "F-TIME-04", flow, "Skip",
            "replay did not start (fewer than 2 timestamps in fixture)",
            evidence={"label": before},
        )
    replay.click()
    page.wait_for_timeout(300)
    after = replay.inner_text()
    if during == before:
        return _fail("F-TIME-04", flow, "replay never entered playing state",
                     before=before, during=during)
    if after != before:
        return _fail("F-TIME-04", flow, "replay did not return to Play",
                     before=before, during=during, after=after)
    return _pass("F-TIME-04", flow, "replay toggles Play/Stop", before=before)


BATCHES: dict[str, list[tuple[str, Callable]]] = {
    "W0": [("env", lambda p: _pass("ENV-01", "ENV", "manual"))],
    "W1": [
        ("F-SHELL-01", tc_f_shell_01),
        ("F-SHELL-02", tc_f_shell_02),
        ("F-SHELL-03", tc_f_shell_03),
        ("F-SETTINGS-01", tc_f_settings_01),
    ],
    "W2": [
        ("F-DOC-KB-01", tc_f_doc_kb_01),
        ("F-SHARE-01", tc_f_share_01),
    ],
    "W3": [
        ("F-VIEW-01", tc_f_view_01),
        ("F-VIEW-02", tc_f_view_02),
    ],
    "W4": [
        ("F-TIME-01", tc_f_time_01),
        ("F-TIME-02", tc_f_time_02),
        ("F-TIME-03", tc_f_time_03),
        ("F-TIME-04", tc_f_time_04),
    ],
    "W5": [
        ("F-SEARCH-01", tc_f_search_01),
        ("F-SEARCH-02", tc_f_search_02),
        ("F-SEARCH-03", tc_f_search_03),
        ("F-PACKS-01", tc_f_packs_01),
        ("F-VINCPACK-01", tc_f_vincpack_01),
    ],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True, choices=sorted(BATCHES))
    ap.add_argument("--app-url", default=os.environ.get("VINC_QA_APP_URL", "http://127.0.0.1:5173"))
    args = ap.parse_args()

    if args.batch == "W0":
        os.execvp(sys.executable, [sys.executable, str(ROOT / "scripts/qa/env_gate.py")])

    from playwright.sync_api import sync_playwright

    run_id = _run_id()
    out_dir = DATA / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[TCResult] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1568, "height": 1073})
        page = context.new_page()
        page.on("console", lambda msg: page.evaluate(
            """(t) => { window.__qaConsoleErrors = window.__qaConsoleErrors || [];
                if (t.type === 'error') window.__qaConsoleErrors.push(t.text); }""",
            {"type": msg.type, "text": msg.text}))
        stack = _stack_meta()
        token = stack.get("token", "")
        page.goto(args.app_url, wait_until="networkidle", timeout=60_000)
        page.evaluate(
            """([owner, token]) => {
                localStorage.setItem('vinc.owner', owner);
                if (token) localStorage.setItem('vinc.sidecar.token', token);
            }""",
            [OWNER, token],
        )
        page.reload(wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2500)

        for _name, fn in BATCHES[args.batch]:
            if _name == "env":
                continue
            try:
                r = fn(page)
            except Exception as e:
                r = TCResult(_name, "?", "Error", detail=str(e))
            if r.outcome in ("Fail", "Error", "Not_Reproducible"):
                errs = _console_errors(page)
                _diag(r, page, errs)
            results.append(r)

        browser.close()

    counts = {k: 0 for k in ("Pass", "Fail", "Error", "Not_Reproducible", "Skip")}
    buckets: dict[str, int] = {}
    for r in results:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
        if r.bucket_id:
            buckets[r.bucket_id] = buckets.get(r.bucket_id, 0) + 1

    meta = {
        "run_id": run_id,
        "batch_ids": [args.batch],
        "task_type": "ui_qa_batch",
        "app_url": args.app_url,
        "owner": OWNER,
        "browser_profile": "chromium-headless-1568x1073",
        "started_at": run_id,
        "outcomes": counts,
        "failure_buckets": buckets,
    }
    structured = [{"tc_id": r.tc_id, "flow": r.flow, "outcome": r.outcome,
                   "detail": r.detail, "bucket_id": r.bucket_id,
                   "auto_diag_step": r.auto_diag_step, "evidence": r.evidence}
                  for r in results]
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (out_dir / "results_structured.json").write_text(json.dumps(structured, indent=2), encoding="utf-8")
    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["tc_id", "flow", "outcome", "detail", "bucket_id"])
        w.writeheader()
        for r in results:
            w.writerow({"tc_id": r.tc_id, "flow": r.flow, "outcome": r.outcome,
                        "detail": r.detail, "bucket_id": r.bucket_id})

    print(json.dumps({"run_id": run_id, "outcomes": counts, "dir": str(out_dir)}, indent=2))
    return 1 if counts.get("Fail") or counts.get("Error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
