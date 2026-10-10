// Execute with the connected Playwright browser_run_code tool (filename).
// Isolated API fixtures exercise the actual application and its actual marks.
async (page) => {
  await page.bringToFront();
  const assert = (value, reason) => { if (!value) throw new Error(reason); };
  const errors = [], evidence = [];
  let graphRequests = 0;
  let failNextGraph = false;
  let mapFixture = false;
  let holdNextScope = false, releaseScope, signalScopeStarted;
  let navEnabled = false;
  const scopeStarted = new Promise(resolve => { signalScopeStarted = resolve; });
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
    if (path === "/graph") {
      graphRequests++;
      const scoped = decodeURIComponent(route.request().url().match(/[?&]domains=([^&]+)/)?.[1] || "") === "qa/a";
      if (scoped && holdNextScope) {
        holdNextScope = false; signalScopeStarted();
        await new Promise(resolve => { releaseScope = resolve; });
      }
      const shown = scoped ? nodes.filter(n => n.domain === "qa/a" || n.id === "domain:qa/a") : nodes;
      const ids = new Set(shown.map(n => n.id));
      data = failNextGraph ? { executed: false, error: "store_busy" } : {
        executed: true, nodes: shown, edges: edges.filter(e => ids.has(e.source) && ids.has(e.target)) };
      failNextGraph = false;
      if (mapFixture) data = { executed: true, edges: [], nodes: Array.from({ length: 24 }, (_, i) => [
        { id: `concept:map${i}`, label: `Map ${i}`, kind: "concept", domain: `map/${i}`, degree: 0, ts: "2020", content_ts: "2020" },
        { id: `domain:map/${i}`, label: `${i}`, kind: "domain", domain: "map", degree: 1 },
      ]).flat() };
    }
    if (path === "/health") data = { ok: true, version: "0.9.7", store_ready: true };
    if (path === "/nav-tree" && navEnabled) data = { executed: true, domains: ["qa/a", "qa/b"].map(domain =>
      ({ domain, concepts: 12, docs: [], nodes: [] })), counts: { domains: 3, docs: 0, unfiled: 0, kinds: { concept: 24 } } };
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
  const grid2d = await page.evaluate(() => {
    const g = window.__s2.getGraph(), t = g.getNodeAttributes("domain:qa/a");
    const ns = g.nodes().filter(id => /^concept:qa-(?:[0-9]|1[01])$/.test(id)).map(id => g.getNodeAttributes(id));
    return { east: ns.some(n => n.x > t.x), west: ns.some(n => n.x < t.x),
      north: ns.some(n => n.y > t.y), south: ns.some(n => n.y < t.y),
      grid: ns.every(n => [n.x - t.x, n.y - t.y].every(v => Math.abs(v / .33 - Math.round(v / .33)) < 1e-7)) };
  });
  assert(Object.values(grid2d).every(Boolean), "actual 2d Topic does not spread across the regular grid in four directions");
  evidence.push({ check: "actual 2d Topic roots spread in four directions on a regular grid", ...grid2d });
  await page.screenshot({ path: "D:/Projects/AIProjects/vinc-project/_wt/vin427-view-elk/output/view-elk-qa/grid-app-2d.jpg" });
  const clickAt = await page.evaluate(() => {
    const s = window.__s2, g = s.getGraph(), rect = document.querySelector(".a2-stage").getBoundingClientRect();
    for (const id of g.edges()) {
      if (!g.getEdgeAttribute(id, "directed")) continue;
      const r = g.getEdgeAttribute(id, "route");
      const points = (r.points2d ?? r.points).map(p => s.graphToViewport({ x: p.x, y: -p.y }));
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
  const requestsBeforeRank = graphRequests;
  const recordedPositions = await page.evaluate(() => {
    window.__qaSigma = window.__s2;
    const g = window.__s2.getGraph();
    return JSON.stringify(g.nodes().map(id => [id, g.getNodeAttribute(id, "x"), g.getNodeAttribute(id, "y")]));
  });
  await page.getByRole("radio", { name: "ranked", exact: true }).click();
  await page.waitForFunction(() => window.__s2 !== window.__qaSigma);
  const ranked2d = await page.evaluate(() => { const s = window.__s2, g = s.getGraph(); return {
    camera: JSON.stringify(s.getCamera().getState()),
    positions: JSON.stringify(g.nodes().map(id => [id, g.getNodeAttribute(id, "x"), g.getNodeAttribute(id, "y")])),
    original: g.edges().filter(id => g.getEdgeAttribute(id, "relationId")).length }; });
  assert(ranked2d.camera === await page.evaluate(() => window.__qaCamera2), "ranked resets camera");
  assert(ranked2d.positions !== recordedPositions && ranked2d.original === 25 && graphRequests === requestsBeforeRank,
    "ranked uses stale temporal cache, loses relations or refetches the graph");
  evidence.push({ check: "2d ranked changes coordinates without refetch or camera reset", pass: true });
  await page.evaluate(() => { window.__qaSigma = window.__s2; });
  await page.getByRole("radio", { name: "by time", exact: true }).click();
  await page.waitForFunction(() => window.__s2 !== window.__qaSigma);
  assert(await page.evaluate(() => { const g = window.__s2.getGraph(); return JSON.stringify(g.nodes().map(id =>
    [id, g.getNodeAttribute(id, "x"), g.getNodeAttribute(id, "y")])); }) === recordedPositions,
    "switching back does not restore deterministic temporal coordinates");
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
  const grid3d = await page.evaluate(() => {
    const ns = window.__g3.graphData().nodes, t = ns.find(n => n.id === "domain:qa/a");
    const members = ns.filter(n => /^concept:qa-(?:[0-9]|1[01])$/.test(n.id));
    return { east: members.some(n => n.x > t.x), west: members.some(n => n.x < t.x),
      north: members.some(n => n.y < t.y), south: members.some(n => n.y > t.y),
      front: members.some(n => n.z > t.z), back: members.some(n => n.z < t.z),
      grid: members.every(n => [n.__x0 - t.__x0, n.__y0 - t.__y0, n.__z0 - t.__z0]
        .every(v => Math.abs(v / .33 - Math.round(v / .33)) < 1e-7)) };
  });
  assert(Object.values(grid3d).every(Boolean), "actual 3d Topic does not spread across the spatial grid in six directions");
  evidence.push({ check: "actual 3d Topic roots spread in six directions on a spatial grid", ...grid3d });
  await page.waitForTimeout(600);
  const framing = await page.evaluate(() => {
    const g = window.__g3, camera = g.camera();
    const matrix = camera.matrixWorldInverse.elements;
    const visible = g.graphData().nodes.filter(n => {
      const depth = -(matrix[2]*n.x + matrix[6]*n.y + matrix[10]*n.z + matrix[14]);
      return depth > camera.near && depth < camera.far;
    }).length;
    return { depthVisible: visible, total: g.graphData().nodes.length, far: camera.far };
  });
  assert(framing.depthVisible === framing.total, "fitted3d marks fall outside camera depth range");
  evidence.push({ check: "fitted3d camera includes every actual mark depth", ...framing });
  await page.screenshot({ path: "D:/Projects/AIProjects/vinc-project/_wt/vin427-view-elk/output/view-elk-qa/grid-app-3d.jpg" });
  const requestsBeforeRank3 = graphRequests;
  await page.evaluate(() => {
    window.__qaGraph3 = window.__g3;
    window.__qaCamera3 = JSON.stringify(window.__g3.cameraPosition());
  });
  await page.getByRole("radio", { name: "ranked", exact: true }).click();
  await page.waitForFunction(() => window.__g3 !== window.__qaGraph3 && window.__g3.graphData().links.length > 0);
  assert(await page.evaluate(() => JSON.stringify(window.__g3.cameraPosition()) === window.__qaCamera3), "ranked resets 3d camera");
  assert(graphRequests === requestsBeforeRank3, "ranked refetches the 3d graph");
  await page.evaluate(() => { window.__qaGraph3 = window.__g3; });
  await page.getByRole("radio", { name: "by time", exact: true }).click();
  await page.waitForFunction(() => window.__g3 !== window.__qaGraph3 && window.__g3.graphData().links.length > 0);
  evidence.push({ check: "3d ranked and temporal selection keeps camera without refetch", pass: true });
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
  failNextGraph = true;
  await page.getByRole("button", { name: "View and legend, 3D" }).click();
  await page.getByRole("button", { name: "2D", exact: true }).click();
  await page.getByText("View couldn't load. Retry anytime. Your documents are untouched.", { exact: true }).waitFor();
  const requestsAfterFailure = graphRequests;
  await page.evaluate(() => { window.__qaSigma = window.__s2; });
  await page.getByRole("radio", { name: "ranked", exact: true }).click();
  await page.waitForFunction(() => window.__s2 !== window.__qaSigma && window.__s2.getGraph().order === 27);
  assert(graphRequests === requestsAfterFailure + 1, "failed API response was reused instead of retried");
  evidence.push({ check: "layout selection retries a failed graph response", pass: true });
  navEnabled = true;
  await page.evaluate(async () => { const lnb = await import("/src/lnb.ts"); await lnb.refreshLnb(); });
  holdNextScope = true;
  const requestsBeforeScope = graphRequests;
  await page.getByRole("button", { name: "Show only a in View", exact: true }).click();
  await scopeStarted;
  await page.getByRole("radio", { name: "by time", exact: true }).click();
  releaseScope();
  await page.waitForFunction(() => window.__s2.getGraph().order === 13);
  assert(graphRequests === requestsBeforeScope + 2, "pending topic switch reused the previous scope's API graph");
  assert(await page.evaluate(() => window.__s2.getGraph().nodes().every(id =>
    id === "domain:qa/a" || /^concept:qa-(?:[0-9]|1[01])$/.test(id))), "old topic leaked into new scope");
  evidence.push({ check: "layout change during a topic fetch cannot reuse the previous scope", pass: true });
  mapFixture = true;
  await page.goto("http://127.0.0.1:5207/#view/as/2d");
  await page.waitForFunction(() => window.__s2?.getGraph().order === 48);
  const composition = await page.evaluate(() => {
    const s = window.__s2, g = s.getGraph(), topics = g.nodes().filter(id => id.startsWith("domain:"));
    const points = topics.map(id => s.graphToViewport(g.getNodeAttributes(id)));
    const rect = document.querySelector(".a2-stage").getBoundingClientRect();
    return { columns: new Set(topics.map(id => g.getNodeAttribute(id, "x"))).size,
      rows: new Set(topics.map(id => g.getNodeAttribute(id, "y"))).size,
      width: (Math.max(...points.map(p => p.x)) - Math.min(...points.map(p => p.x))) / rect.width,
      height: (Math.max(...points.map(p => p.y)) - Math.min(...points.map(p => p.y))) / rect.height };
  });
  assert(composition.columns > 1 && composition.rows > 1 && composition.width > .3 && composition.height > .3,
    "Topic map leaves an axis unused");
  evidence.push({ check: "actual 2d renderer distributes Topics across the canvas", ...composition });
  assert(errors.length === 0, `page errors: ${errors.join("; ")}`);
  return { evidence, pageErrors: errors };
}
