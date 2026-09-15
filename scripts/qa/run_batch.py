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
