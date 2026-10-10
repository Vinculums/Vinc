// Execute with the connected Playwright browser_run_code tool (filename).
// Isolated API fixtures exercise the actual application and its actual marks.
async (page) => {
  await page.bringToFront();
  const assert = (value, reason) => { if (!value) throw new Error(reason); };
  const errors = [], evidence = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.unrouteAll();
  await page.route("**/*", async route => {
    const path = route.request().url().replace(/^https?:\/\/[^/]+/, "").split("?")[0];
    if (!/^\/(health|graph|domains|nav-tree|docs|changes|view|node-context|edge-context|config|themes)(\/|$)/.test(path)) return route.continue();
    const nodes = Array.from({ length: 24 }, (_, i) => ({ id: `concept:qa-${i}`, label: `QA knowledge ${i}`,
      domain: i < 12 ? "qa/a" : "qa/b", kind: ["decision", "record", "bridge", "concept"][i % 4], degree: 3,
      ts: `2026-09-${String(i + 1).padStart(2, "0")}`, content_ts: i === 5 ? "" : `2020-${String(1 + i % 12).padStart(2, "0")}` }));
    nodes.push({ id: "domain:qa/a", label: "a", domain: "qa", kind: "domain", degree: 12 },
      { id: "domain:qa/b", label: "b", domain: "qa", kind: "domain", degree: 12 },
      { id: "domain:qa", label: "qa", domain: "", kind: "domain", degree: 24 });
    const edges = nodes.slice(0, 23).map((n, i) => ({ id: `qa-edge-${i}`, source: n.id, target: nodes[i + 1].id,
      type: ["DERIVES_FROM", "PREREQUISITE_OF", "RELATED_TO"][i % 3], cross_domain: i === 11,
      confidence: .8, evidence: "QA authored evidence" }));
    edges.push({ id: "qa-cycle", source: nodes[6].id, target: nodes[2].id, type: "PREREQUISITE_OF", cross_domain: false },
      { id: "qa-parallel", source: nodes[0].id, target: nodes[1].id, type: "RELATED_TO", cross_domain: false });
    let data = { executed: true, domains: [], docs: [], nodes: [], edges: [], themes: [], marker: "qa" };
    if (path === "/graph") data = { executed: true, nodes, edges };
    if (path === "/health") data = { ok: true, version: "0.9.7", store_ready: true };
    if (path === "/node-context") data = { executed: true, node: { id: "concept:qa-0", title: "QA knowledge", domain: "qa/a", kind: "concept" }, docs: [], links: [], records: [] };
    if (path === "/edge-context") data = { executed: true, src: { title: "QA source" }, dst: { title: "QA target" }, confidence: .8, evidence: "QA authored evidence" };
    await route.fulfill({ json: data });
  });
  await page.goto("http://127.0.0.1:5207/#view");
  await page.evaluate(() => {
    localStorage.setItem("vinc.view.prefs", JSON.stringify({ pv: 2, view: "2d", edges: "always", timeBasis: "content", arrows: true }));
    localStorage.setItem("vinc.view.coach.seen", "1");
  });
  await page.goto("http://127.0.0.1:5207/#view/as/2d");
  await page.reload();
  await page.waitForSelector('canvas[data-view-layout="elk-temporal-v1"]');
  const initial = await page.evaluate(() => {
    const s = window.__s2, g = s.getGraph();
    window.__qaSigma = s;
    return { nodes: g.order, edges: g.size, original: g.edges().filter(id => g.getEdgeAttribute(id, "relationId")).length,
      positions: JSON.stringify(g.nodes().map(id => [id, g.getNodeAttribute(id, "x"), g.getNodeAttribute(id, "y")])) };
  });
  assert(initial.nodes === 27 && initial.original === 25, "parallel or original relations lost");
  evidence.push({ check: "2d actual graph and parallel relation identity", ...initial, positions: undefined });
  const clickAt = await page.evaluate(() => {
    const s = window.__s2, g = s.getGraph(), rect = document.querySelector(".a2-stage").getBoundingClientRect();
    for (const id of g.edges()) {
      if (!g.getEdgeAttribute(id, "directed")) continue;
      const points = g.getEdgeAttribute(id, "route").points.map(p => s.graphToViewport({ x: p.x, y: -p.y }));
      for (let i = 1; i < points.length; i++) {
        const a = points[i - 1], b = points[i], x = (a.x + b.x) / 2, y = (a.y + b.y) / 2;
        if (Math.hypot(a.x - b.x, a.y - b.y) < 35) continue;
        if (g.nodes().some(id => { const p = s.graphToViewport(g.getNodeAttributes(id)); return Math.hypot(p.x - x, p.y - y) < 18; })) continue;
        return { x: rect.x + x, y: rect.y + y };
      }
    }
  });
  assert(clickAt, "no routed edge click target");
  await page.mouse.click(clickAt.x, clickAt.y);
  await page.getByText("QA authored evidence", { exact: true }).waitFor();
  evidence.push({ check: "2d routed edge picking opens authored evidence", pass: true });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "View and legend, 2D" }).click();
  await page.getByRole("slider", { name: "Time cursor" }).evaluate(el => { el.value = ".25"; el.dispatchEvent(new Event("input", { bubbles: true })); });
  await page.waitForFunction(() => window.__s2.getGraph().nodes().some(id => window.__s2.getNodeDisplayData(id)?.hidden));
  const cut = await page.evaluate(() => { const s = window.__s2, g = s.getGraph(); return {
    hidden: g.nodes().filter(id => s.getNodeDisplayData(id)?.hidden).length,
    unknown: !s.getNodeDisplayData("concept:qa-5")?.hidden,
    same: s === window.__qaSigma, positions: JSON.stringify(g.nodes().map(id => [id, g.getNodeAttribute(id, "x"), g.getNodeAttribute(id, "y")])) }; });
  assert(cut.same && cut.hidden > 0 && cut.unknown && cut.positions === initial.positions, "time filter changes positions or hides unknown dates");
  evidence.push({ check: "2d time cursor keeps camera, renderer and coordinates", hidden: cut.hidden, unknownVisible: cut.unknown });
  await page.evaluate(() => { window.__s2.getCamera().setState({ ratio: .8 }); window.__qaCamera2 = JSON.stringify(window.__s2.getCamera().getState()); });
  await page.getByRole("radio", { name: "when recorded", exact: true }).click();
  await page.waitForFunction(() => window.__s2 !== window.__qaSigma);
  assert(await page.evaluate(() => JSON.stringify(window.__s2.getCamera().getState()) === window.__qaCamera2), "date basis resets 2d camera");
  evidence.push({ check: "date basis recomputes temporal layout", pass: true });
  await page.getByRole("slider", { name: "Time cursor" }).evaluate(el => { el.value = "1"; el.dispatchEvent(new Event("input", { bubbles: true })); });
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("vinc.view.prefs")).when === 1);
  await page.getByRole("button", { name: "3D", exact: true }).click();
  await page.waitForFunction(() => window.__g3?.graphData().links.some(l => l.__lineObj?.children[0]?.geometry.attributes.position?.count > 2));
  if (!await page.getByRole("slider", { name: "Time cursor" }).isVisible())
    await page.getByRole("button", { name: "View and legend, 3D" }).click();
  const geometry = await page.evaluate(() => {
    const g = window.__g3, links = g.graphData().links;
    const orthogonal = links.every(l => { const a = l.__lineObj.children[0].geometry.attributes.position;
      for (let i = 1; i < a.count; i++) if ([a.getX(i)-a.getX(i-1),a.getY(i)-a.getY(i-1),a.getZ(i)-a.getZ(i-1)].filter(v=>Math.abs(v)>.0001).length>1) return false;
      return true; });
    return { orthogonal, original: links.filter(l => l.id).length, metadata: links.find(l => l.id === "qa-edge-0").evidence,
      builtInArrowsDisabled: links.every(l => g.linkDirectionalArrowLength()(l) === 0) };
  });
  assert(geometry.orthogonal && geometry.original === 25 && geometry.metadata === "QA authored evidence" && geometry.builtInArrowsDisabled, "3d route or metadata mismatch");
  evidence.push({ check: "3d actual sections and original evidence", ...geometry });
  await page.getByRole("slider", { name: "Time cursor" }).evaluate(el => { el.value = ".25"; el.dispatchEvent(new Event("input", { bubbles: true })); });
  await page.waitForFunction(() => { let found = false; window.__g3.scene().traverse(obj => {
    if (obj.isInstancedMesh) for (let i = 0; i < obj.count; i++) {
      const a = obj.instanceMatrix.array; if (a[i*16] === 0 && a[i*16+5] === 0 && a[i*16+10] === 0) found = true;
    }
  }); return found; });
  const hidden3 = await page.evaluate(() => { let hidden = 0; window.__g3.scene().traverse(obj => {
    if (!obj.isInstancedMesh) return;
    for (let i = 0; i < obj.count; i++) { const a = obj.instanceMatrix.array; if (a[i*16] === 0 && a[i*16+5] === 0 && a[i*16+10] === 0) hidden++; }
  }); return hidden; });
  assert(hidden3 > 0, "time visibility does not reach instanced node bodies");
  evidence.push({ check: "3d time filter reaches actual instance matrices", hidden: hidden3 });
  await page.getByRole("slider", { name: "Time cursor" }).evaluate(el => { el.value = "1"; el.dispatchEvent(new Event("input", { bubbles: true })); });
  const cdp = await page.context().newCDPSession(page);
  const targets = await cdp.send("Target.getTargets");
  assert(!targets.targetInfos.some(t => t.type === "worker" && /5207/.test(t.url)), "settled layout left a worker running");
  await cdp.detach();
  evidence.push({ check: "completed ELK and WASM jobs leave no live workers", pass: true });
  // Wait for the renderer's documented 400 ms initial camera fit, otherwise
  // its ongoing movement is incorrectly attributed to the theme repaint.
  await page.waitForTimeout(600);
  await page.evaluate(async () => { const camera = window.__g3.cameraPosition(); document.documentElement.dataset.theme = "light";
    localStorage.setItem("vinc.theme", "light"); const app = await import("/src/main.ts"); app.applyTheme();
    window.__qaThemeCamera = JSON.stringify(camera); });
  const theme = await page.evaluate(() => ({ light: document.documentElement.dataset.theme === "light",
    sameCamera: JSON.stringify(window.__g3.cameraPosition()) === window.__qaThemeCamera }));
  assert(theme.light && theme.sameCamera, "theme switch resets camera");
  evidence.push({ check: "app theme entry repaints 3d in place", ...theme });
  if (!await page.getByRole("radio", { name: "particles", exact: true }).isVisible())
    await page.getByRole("button", { name: "View and legend, 3D" }).click();
  await page.getByRole("radio", { name: "particles", exact: true }).click();
  await page.waitForFunction(() => window.__g3.graphData().links.find(l => l.id === "qa-edge-0")?.__lineObj?.children[2].visible);
  const photon = await page.evaluate(() => window.__g3.graphData().links.find(l => l.id === "qa-edge-0").__lineObj.children[2].position.toArray());
  await page.waitForTimeout(150);
  const particles = await page.evaluate(before => {
    const links = window.__g3.graphData().links, l = links.find(l => l.id === "qa-edge-0");
    return { moving: JSON.stringify(l.__lineObj.children[2].position.toArray()) !== JSON.stringify(before),
      lineHidden: !l.__lineObj.children[0].visible,
      undirectedHidden: links.filter(l => l.type === "RELATED_TO").every(l => !l.__lineObj.children[2].visible) };
  }, photon);
  assert(particles.moving && particles.lineHidden && particles.undirectedHidden, "particles disagree with routed relation semantics");
  evidence.push({ check: "3d routed photons move only along directed relations", ...particles });
  await page.getByRole("radio", { name: "always", exact: true }).click();
  await page.waitForFunction(() => window.__g3.graphData().links.find(l => l.id === "qa-edge-0")?.__lineObj?.children[0].visible);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  const dragAt = await page.evaluate(() => {
    const g = window.__g3, n = g.graphData().nodes.find(n => n.id === "concept:qa-0"),
      p = g.graph2ScreenCoords(n.x, n.y, n.z), r = document.querySelector("[data-view-layout]").getBoundingClientRect();
    window.__qaDragBefore = [n.x, n.y, n.z];
    return { x: r.x + p.x, y: r.y + p.y };
  });
  await page.mouse.move(dragAt.x, dragAt.y); await page.mouse.down();
  await page.mouse.move(dragAt.x + 35, dragAt.y + 25, { steps: 8 }); await page.mouse.up();
  await page.waitForFunction(() => {
    const g = window.__g3, n = g.graphData().nodes.find(n => n.id === "concept:qa-0"),
      p = g.graphData().links.find(l => l.id === "qa-edge-0").__lineObj.children[0].geometry.attributes.position;
    return JSON.stringify([n.x, n.y, n.z]) !== JSON.stringify(window.__qaDragBefore) && Math.hypot(p.getX(0)-n.x,p.getY(0)-n.y,p.getZ(0)-n.z) < .001;
  });
  evidence.push({ check: "3d physical node drag moves the route endpoint with its body", pass: true });
  assert(errors.length === 0, `page errors: ${errors.join("; ")}`);
  return { evidence, pageErrors: errors };
}
