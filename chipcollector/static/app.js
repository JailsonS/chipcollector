/* ChipCollector — interface web (JavaScript puro + Leaflet + Leaflet-Geoman) */
"use strict";

const S = { me: null, config: null, project: null, layersCache: {} };
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = () => $("#view");

async function api(path, opts = {}) {
  const o = { credentials: "same-origin", ...opts, headers: { ...(opts.headers || {}) } };
  if (o.body && !(o.body instanceof FormData) && typeof o.body !== "string") {
    o.body = JSON.stringify(o.body);
    o.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, o);
  if (r.status === 401 && !path.startsWith("/api/auth")) { S.me = null; route(); throw new Error("Sessão expirada."); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    let msg = data && data.detail ? data.detail : data;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), err ? 6000 : 2200);
}
const fail = (e) => toast(e.message || String(e), true);

function crumbs(items) {
  $("#crumbs").innerHTML = items.map(([t, h]) => (h ? `<a href="${h}">${esc(t)}</a>` : `<span>${esc(t)}</span>`)).join("<span>/</span>");
}

function renderUserbox() {
  const b = $("#userbox");
  if (!S.me) { b.innerHTML = ""; return; }
  b.innerHTML = `
    ${S.me.is_admin ? '<a href="#/users" class="small">Usuários</a>' : ""}
    <span class="muted">${esc(S.me.full_name || S.me.username)}</span>
    <button class="ghost small" id="pwdBtn" title="Trocar senha">Senha</button>
    <button class="small" id="logoutBtn">Sair</button>`;
  $("#logoutBtn").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); S.me = null; location.hash = "#/"; route(); };
  $("#pwdBtn").onclick = async () => {
    const p = prompt("Nova senha (mín. 6 caracteres):");
    if (!p) return;
    try { await api(`/api/users/${S.me.id}`, { method: "PATCH", body: { password: p } }); toast("Senha alterada."); } catch (e) { fail(e); }
  };
}

/* =========================================================== roteamento */
let cleanup = null;
async function route() {
  if (cleanup) { try { cleanup(); } catch (_) {} cleanup = null; }
  if (!S.config) S.config = await api("/api/config");
  if (!S.me) {
    try { S.me = await api("/api/auth/me"); } catch (_) { S.me = null; }
  }
  renderUserbox();
  if (!S.me) return renderLogin();
  const hash = location.hash.replace(/^#/, "") || "/";
  if (hash.startsWith("drive=")) { toast("Google Drive conectado."); location.hash = "#/"; return; }
  const parts = hash.split("/").filter(Boolean);
  try {
    if (parts[0] === "users") return await renderUsers();
    if (parts[0] === "p" && parts[1]) {
      S.project = await api(`/api/projects/${parts[1]}`);
      const tab = parts[2] || "annotate";
      renderProjectShell(tab);
      if (tab === "annotate") return await renderAnnotate(parts[3] ? Number(parts[3]) : null);
      if (tab === "map") return await renderMap();
      if (tab === "setup") return await renderSetup();
      if (tab === "export") return await renderExport();
      if (tab === "team") return await renderTeam();
    }
    return await renderProjects();
  } catch (e) { fail(e); }
}
window.addEventListener("hashchange", route);

/* =========================================================== login */
function renderLogin() {
  crumbs([]);
  view().innerHTML = `
  <div class="login card stack">
    <h1>Entrar</h1>
    <p class="muted small">Plataforma de coleta de amostras de referência para treinamento de modelos de IA.</p>
    <div class="field"><label>Usuário</label><input id="lu" autocomplete="username"></div>
    <div class="field"><label>Senha</label><input id="lp" type="password" autocomplete="current-password"></div>
    <div id="signupExtra" class="hidden">
      <div class="field"><label>Nome completo</label><input id="ln"></div>
      <div class="field"><label>E-mail</label><input id="le" type="email"></div>
    </div>
    <div class="row"><button class="primary" id="loginBtn">Entrar</button>
    ${S.config.allow_signup ? '<button class="ghost" id="toSignup">Criar conta</button>' : ""}</div>
  </div>`;
  let signup = false;
  const go = async () => {
    const body = { username: $("#lu").value, password: $("#lp").value, full_name: $("#ln").value, email: $("#le").value };
    try { S.me = await api(signup ? "/api/auth/signup" : "/api/auth/login", { method: "POST", body }); route(); } catch (e) { fail(e); }
  };
  $("#loginBtn").onclick = go;
  $("#lp").onkeydown = (e) => e.key === "Enter" && go();
  const ts = $("#toSignup");
  if (ts) ts.onclick = () => {
    signup = !signup;
    $("#signupExtra").classList.toggle("hidden", !signup);
    $("#loginBtn").textContent = signup ? "Criar conta" : "Entrar";
    ts.textContent = signup ? "Já tenho conta" : "Criar conta";
  };
}

/* =========================================================== projetos */
async function renderProjects() {
  crumbs([["Projetos"]]);
  const list = await api("/api/projects");
  view().innerHTML = `
    <div class="row" style="margin-bottom:1rem"><h1 style="margin:0">Projetos</h1><div class="spacer"></div>
      ${S.me.is_admin ? '<button class="primary" id="newProj">+ Novo projeto</button>' : ""}</div>
    <div id="newProjForm" class="card hidden" style="margin-bottom:1rem"></div>
    <div class="projects">${list.map(projectCard).join("") || '<p class="muted">Nenhum projeto ainda.</p>'}</div>`;
  $$(".project-card").forEach((el) => (el.onclick = () => (location.hash = `#/p/${el.dataset.id}/annotate`)));
  const nb = $("#newProj");
  if (nb) nb.onclick = () => {
    const f = $("#newProjForm");
    f.classList.toggle("hidden");
    f.innerHTML = projectForm({ name: "", description: "", target: "", annotation_type: "classification", multiclass: false,
      chip_size_px: 256, resolution_m: 10, annotators_per_chip: 1, classes: [{ id: 1, name: "alvo", color: "#e63946" }] }, true);
    bindClassEditor(f);
    $("#saveProj", f).onclick = async () => {
      try {
        const p = await api("/api/projects", { method: "POST", body: readProjectForm(f) });
        toast("Projeto criado. Configure as bases de imagem e as camadas de referência.");
        location.hash = `#/p/${p.id}/setup`;
      } catch (e) { fail(e); }
    };
  };
}

function projectCard(p) {
  const st = p.stats || {};
  const pct = st.chips ? Math.round((100 * st.chips_complete) / st.chips) : 0;
  return `<div class="card project-card stack" data-id="${p.id}">
    <div class="row"><h2 style="margin:0">${esc(p.name)}</h2><div class="spacer"></div>
      <span class="pill">${p.annotation_type === "segmentation" ? "segmentação" : "classificação"}</span></div>
    <div class="muted small">${esc(p.target || p.description || "")}</div>
    <div class="row small">${(p.classes || []).map((c) => `<span><span class="swatch" style="background:${esc(c.color)}"></span>${esc(c.name)}</span>`).join("")}</div>
    <div class="progress"><div style="width:${pct}%"></div></div>
    <div class="row small muted"><span>${st.chips_complete || 0}/${st.chips || 0} chips completos</span><div class="spacer"></div>
      <span>${st.annotations || 0} anotações</span><span>${p.chip_size_px}px · ${p.resolution_m} m</span></div>
  </div>`;
}

function projectForm(p, isNew) {
  return `<div class="stack">
    <div class="inline-fields">
      <div class="field"><label>Nome</label><input name="name" value="${esc(p.name)}"></div>
      <div class="field"><label>Alvo de detecção</label><input name="target" value="${esc(p.target)}" placeholder="ex.: extração madeireira"></div>
    </div>
    <div class="field"><label>Descrição</label><textarea name="description" rows="2">${esc(p.description)}</textarea></div>
    <div class="inline-fields">
      <div class="field"><label>Tipo de anotação</label><select name="annotation_type">
        <option value="classification" ${p.annotation_type === "classification" ? "selected" : ""}>Classificação (rótulo por chip)</option>
        <option value="segmentation" ${p.annotation_type === "segmentation" ? "selected" : ""}>Segmentação (polígonos)</option></select></div>
      <div class="field"><label>Classes por chip</label><select name="multiclass">
        <option value="false" ${!p.multiclass ? "selected" : ""}>Uma classe por chip</option>
        <option value="true" ${p.multiclass ? "selected" : ""}>Múltiplas classes por chip</option></select></div>
      <div class="field"><label>Tamanho do chip (pixels)</label><input name="chip_size_px" type="number" min="16" max="2048" value="${p.chip_size_px}" ${isNew ? "" : "data-lock"}></div>
      <div class="field"><label>Resolução (m/pixel)</label><input name="resolution_m" type="number" step="any" min="0.1" value="${p.resolution_m}" ${isNew ? "" : "data-lock"}></div>
      <div class="field"><label>Anotadores por chip</label><input name="annotators_per_chip" type="number" min="1" max="10" value="${p.annotators_per_chip}"></div>
    </div>
    <div><h3>Classes</h3><p class="muted small">Classificação binária: use uma única classe (presença/ausência). O id é o valor gravado nas máscaras.</p>
      <table class="classes"><thead><tr><th style="width:70px">Id</th><th>Nome</th><th style="width:60px">Cor</th><th style="width:40px"></th></tr></thead>
      <tbody>${(p.classes || []).map(classRow).join("")}</tbody></table>
      <button class="small" id="addClass" style="margin-top:.4rem">+ classe</button></div>
    <div class="row"><button class="primary" id="saveProj">${isNew ? "Criar projeto" : "Salvar"}</button></div>
  </div>`;
}
const PALETTE = ["#e63946", "#2a9d8f", "#f4a261", "#457b9d", "#8338ec", "#ffbe0b", "#06d6a0", "#ef476f", "#118ab2"];
const classRow = (c) => `<tr><td><input name="cid" type="number" min="1" max="254" value="${c.id}"></td>
  <td><input name="cname" value="${esc(c.name)}"></td><td><input name="ccolor" type="color" value="${esc(c.color)}"></td>
  <td><button class="ghost danger small rm">✕</button></td></tr>`;
function bindClassEditor(root) {
  const tb = $("table.classes tbody", root);
  const bindRm = () => $$(".rm", tb).forEach((b) => (b.onclick = () => b.closest("tr").remove()));
  bindRm();
  $("#addClass", root).onclick = () => {
    const ids = $$("input[name=cid]", tb).map((i) => Number(i.value));
    const id = (ids.length ? Math.max(...ids) : 0) + 1;
    tb.insertAdjacentHTML("beforeend", classRow({ id, name: `classe_${id}`, color: PALETTE[(id - 1) % PALETTE.length] }));
    bindRm();
  };
}
function readProjectForm(root) {
  const v = (n) => $(`[name=${n}]`, root).value;
  return {
    name: v("name"), target: v("target"), description: v("description"), annotation_type: v("annotation_type"),
    multiclass: v("multiclass") === "true", chip_size_px: Number(v("chip_size_px")), resolution_m: Number(v("resolution_m")),
    annotators_per_chip: Number(v("annotators_per_chip")),
    classes: $$("table.classes tbody tr", root).map((tr) => ({
      id: Number($("[name=cid]", tr).value), name: $("[name=cname]", tr).value, color: $("[name=ccolor]", tr).value })),
  };
}

function renderProjectShell(tab) {
  const p = S.project;
  crumbs([["Projetos", "#/"], [p.name]]);
  const tabs = [["annotate", "Anotar"], ["map", "Mapa"], ["export", "Exportar"], ["team", "Progresso"]];
  if (S.me.is_admin) tabs.splice(2, 0, ["setup", "Configurar"]);
  view().innerHTML = `<div class="tabs">${tabs.map(([k, t]) => `<a href="#/p/${p.id}/${k}" class="${k === tab ? "on" : ""}">${t}</a>`).join("")}</div><div id="tabview"></div>`;
}
const tabview = () => $("#tabview");
const classById = (id) => (S.project.classes || []).find((c) => Number(c.id) === Number(id));

/* =========================================================== anotação */
const A = {};
const toLL = (x, y) => L.latLng(-y, x);
function llToPixelGeom(geom) {
  const conv = (c) => (typeof c[0] === "number" ? [c[0], -c[1]] : c.map(conv));
  return { type: geom.type, coordinates: conv(geom.coordinates) };
}

async function renderAnnotate(chipId) {
  const p = S.project;
  const seg = p.annotation_type === "segmentation";
  if (!p.image_sources.length) {
    tabview().innerHTML = `<div class="card">Este projeto ainda não tem bases de imagem. ${S.me.is_admin ? `<a href="#/p/${p.id}/setup">Configurar</a>` : "Peça a um administrador para configurar."}</div>`;
    return;
  }
  const strata = Object.keys(p.stats.by_stratum || {});
  tabview().innerHTML = `
  <div class="workspace">
    <div class="chipmap-wrap">
      <div id="chipmap" class="chip-crs"></div>
      <div class="toolstrip">
        ${seg ? `<button data-t="poly" title="Desenhar polígono (P)">✎ Polígono</button>
        <button data-t="rect" title="Retângulo">▭ Retângulo</button>
        <button data-t="edit" title="Editar vértices (E)">⬚ Editar</button>
        <button data-t="cut" title="Recortar polígonos">✂ Recortar</button>
        <button data-t="del" title="Apagar polígono (Del)">🗑 Apagar</button>
        <button data-t="useref" title="Copiar polígonos de referência para edição">⇩ Usar referência</button>
        <button data-t="clear" title="Remover todos os polígonos">Limpar</button>` : ""}
        <button data-t="ref" class="active" title="Mostrar/ocultar referência (R)">Referência</button>
        <button data-t="others" class="${S.me.is_admin ? "" : "hidden"}" title="Anotações de outros usuários">Outros</button>
        <button data-t="fit" title="Enquadrar">⤢</button>
      </div>
      <div id="chipmsg" class="overlay-msg">Carregando…</div>
    </div>
    <div class="side">
      <div class="card tight">
        <div class="row"><strong id="chipTitle">Chip</strong><div class="spacer"></div>
          <select id="stratumSel" style="width:auto" title="Filtrar por estrato">
            <option value="">todos os estratos</option>${strata.map((s) => `<option>${esc(s)}</option>`).join("")}</select></div>
        <div id="chipInfo" class="small muted" style="margin-top:.3rem"></div>
      </div>
      <div class="card tight stack">
        <div class="field" style="margin:0"><label>Base de imagem <kbd>B</kbd></label><select id="srcSel">
          ${p.image_sources.map((s) => `<option value="${esc(s.id)}">${esc(s.label || s.id)}</option>`).join("")}</select></div>
        <div class="inline-fields" id="rgbBox"></div>
        <div class="inline-fields">
          <div><label>Mín.</label><input id="stMin" type="number" step="any" placeholder="auto"></div>
          <div><label>Máx.</label><input id="stMax" type="number" step="any" placeholder="auto"></div>
        </div>
        <div class="field" style="margin:0"><label>Comparar com</label><select id="cmpSel"><option value="">—</option>
          ${p.image_sources.map((s) => `<option value="${esc(s.id)}">${esc(s.label || s.id)}</option>`).join("")}</select>
          <input id="cmpOp" type="range" min="0" max="1" step="0.05" value="0.5" class="hidden"></div>
        <div id="imgMeta" class="small muted"></div>
      </div>
      <div class="card tight">
        <h3>${seg ? "Classe para desenhar" : "Rótulo do chip"}</h3>
        <div class="classbtns" id="classBtns"></div>
        <div class="field" style="margin:.6rem 0 0"><label>Comentário</label><input id="comment" placeholder="opcional"></div>
      </div>
      <div class="big-actions">
        <button class="primary" id="saveBtn" title="Salvar e próximo (Enter)">Salvar <kbd>Enter</kbd></button>
        <button id="uncBtn" title="Salvar como incerto (U)">Incerto <kbd>U</kbd></button>
        <button id="skipBtn" title="Pular — imagem ruim/nuvem (S)">Pular <kbd>S</kbd></button>
      </div>
      <div class="card tight"><div id="ctxmap"></div></div>
      <div class="card tight"><h3>Minhas últimas</h3><div id="history" class="small"></div></div>
    </div>
  </div>`;

  // mapa do chip (coordenadas de pixel)
  for (const k of Object.keys(A)) delete A[k];  // estado limpo a cada abertura
  A.map = L.map("chipmap", { crs: L.CRS.Simple, minZoom: -4, maxZoom: 6, zoomSnap: 0.25, attributionControl: false, zoomControl: false });
  L.control.zoom({ position: "bottomright" }).addTo(A.map);
  A.refLayer = L.layerGroup().addTo(A.map);
  A.otherLayer = L.layerGroup();
  A.ctx = L.map("ctxmap", { zoomControl: false, attributionControl: true });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(A.ctx);
  A.ctxRect = null;
  A.classId = p.classes[0].id;
  A.labels = new Set();
  A.labelChosen = false;
  A.chipId = null;

  if (seg) setupGeoman();
  bindAnnotateControls();
  const keyh = (e) => onKey(e);
  document.addEventListener("keydown", keyh);
  cleanup = () => { document.removeEventListener("keydown", keyh); A.map.remove(); A.ctx.remove(); };
  loadHistory();
  if (chipId) await openChip(chipId); else await openNext();
}

function setupGeoman() {
  const m = A.map;
  m.pm.setGlobalOptions({ snappable: true, snapDistance: 12, allowSelfIntersection: false, continueDrawing: true });
  m.pm.setLang("pt_br");
  m.on("pm:create", (e) => {
    tagDrawn(e.layer, A.classId);
  });
  m.on("pm:cut", (e) => {
    const cid = e.originalLayer?.feature?.properties?.class_id ?? A.classId;
    tagDrawn(e.layer, cid);
  });
}
function tagDrawn(layer, classId) {
  layer._ccDraw = true;
  layer.feature = layer.feature || { type: "Feature", properties: {} };
  layer.feature.properties.class_id = Number(classId);
  styleDrawn(layer);
  layer.off("click").on("click", () => {
    if (A.map.pm.globalRemovalModeEnabled() || A.map.pm.globalEditModeEnabled() || A.map.pm.globalCutModeEnabled()) return;
    if (layer.feature.properties.class_id !== Number(A.classId)) {
      layer.feature.properties.class_id = Number(A.classId);
      styleDrawn(layer);
      toast(`Polígono → ${classById(A.classId)?.name}`);
    }
  });
}
function styleDrawn(layer) {
  const c = classById(layer.feature.properties.class_id);
  layer.setStyle({ color: c?.color || "#fff", weight: 2, fillOpacity: 0.25, fillColor: c?.color || "#fff" });
}
const drawnLayers = () => { const out = []; A.map.eachLayer((l) => l._ccDraw && out.push(l)); return out; };

function setTool(t) {
  const m = A.map;
  const btn = (k) => $(`.toolstrip [data-t=${k}]`);
  const off = () => {
    m.pm.disableDraw(); if (m.pm.globalEditModeEnabled()) m.pm.disableGlobalEditMode();
    if (m.pm.globalRemovalModeEnabled()) m.pm.disableGlobalRemovalMode(); if (m.pm.globalCutModeEnabled()) m.pm.disableGlobalCutMode();
    ["poly", "rect", "edit", "cut", "del"].forEach((k) => btn(k)?.classList.remove("active"));
  };
  const was = btn(t)?.classList.contains("active");
  if (["poly", "rect", "edit", "cut", "del", "none"].includes(t)) off();
  if (was) return;
  const style = { pathOptions: { color: classById(A.classId)?.color } , templineStyle: { color: classById(A.classId)?.color }, hintlineStyle: { color: classById(A.classId)?.color, dashArray: "5,5" } };
  if (t === "poly") { m.pm.enableDraw("Polygon", style); btn(t).classList.add("active"); }
  if (t === "rect") { m.pm.enableDraw("Rectangle", style); btn(t).classList.add("active"); }
  if (t === "edit") { m.pm.enableGlobalEditMode(); btn(t).classList.add("active"); }
  if (t === "cut") { m.pm.enableGlobalCutMode(); btn(t).classList.add("active"); }
  if (t === "del") { m.pm.enableGlobalRemovalMode(); btn(t).classList.add("active"); }
}

function bindAnnotateControls() {
  $$(".toolstrip button").forEach((b) => (b.onclick = () => {
    const t = b.dataset.t;
    if (t === "ref") { b.classList.toggle("active"); A.map.hasLayer(A.refLayer) ? A.map.removeLayer(A.refLayer) : A.refLayer.addTo(A.map); return; }
    if (t === "others") { b.classList.toggle("active"); A.map.hasLayer(A.otherLayer) ? A.map.removeLayer(A.otherLayer) : A.otherLayer.addTo(A.map); return; }
    if (t === "fit") { fitChip(); return; }
    if (t === "useref") { useReference(); return; }
    if (t === "clear") { if (confirm("Remover todos os polígonos deste chip?")) drawnLayers().forEach((l) => A.map.removeLayer(l)); return; }
    setTool(t);
  }));
  $("#srcSel").onchange = () => { A.rgb = null; $("#stMin").value = $("#stMax").value = ""; loadImage(); };
  $("#stMin").onchange = $("#stMax").onchange = () => loadImage(false);
  $("#cmpSel").onchange = () => { $("#cmpOp").classList.toggle("hidden", !$("#cmpSel").value); loadCompare(); };
  $("#cmpOp").oninput = () => A.cmpLayer && A.cmpLayer.setOpacity(Number($("#cmpOp").value));
  $("#stratumSel").onchange = () => openNext();
  $("#saveBtn").onclick = () => save("done");
  $("#uncBtn").onclick = () => save("uncertain");
  $("#skipBtn").onclick = () => save("skip");
  renderClassButtons();
}

function renderClassButtons() {
  const p = S.project, seg = p.annotation_type === "segmentation";
  const box = $("#classBtns");
  let html = "";
  if (seg) {
    html = p.classes.map((c, i) => `<button data-c="${c.id}" class="${Number(A.classId) === Number(c.id) ? "active" : ""}">
      <span class="swatch" style="background:${esc(c.color)}"></span>${esc(c.name)}<kbd class="k">${i + 1}</kbd></button>`).join("");
    html += `<div class="small muted" style="margin-top:.3rem">Sem polígonos = ausência do alvo. Clique num polígono para trocar sua classe para a selecionada.</div>`;
  } else if (p.classes.length === 1) {
    const c = p.classes[0];
    html = `<button data-c="${c.id}" class="${A.labelChosen && A.labels.has(Number(c.id)) ? "active" : ""}"><span class="swatch" style="background:${esc(c.color)}"></span>Presente: ${esc(c.name)}<kbd class="k">1</kbd></button>
      <button data-c="0" class="${A.labelChosen && !A.labels.size ? "active" : ""}">Ausente<kbd class="k">0</kbd></button>`;
  } else {
    html = p.classes.map((c, i) => `<button data-c="${c.id}" class="${A.labels.has(Number(c.id)) ? "active" : ""}">
      ${p.multiclass ? (A.labels.has(Number(c.id)) ? "☑" : "☐") : ""} <span class="swatch" style="background:${esc(c.color)}"></span>${esc(c.name)}<kbd class="k">${i + 1}</kbd></button>`).join("");
    html += `<button data-c="0" class="${A.labelChosen && !A.labels.size ? "active" : ""}">Nenhuma (ausência)<kbd class="k">0</kbd></button>`;
  }
  box.innerHTML = html;
  $$("button", box).forEach((b) => (b.onclick = () => pickClass(Number(b.dataset.c))));
}

function pickClass(id) {
  const p = S.project;
  if (p.annotation_type === "segmentation") {
    if (!id) return;
    A.classId = id;
    const active = $(".toolstrip .active[data-t=poly], .toolstrip .active[data-t=rect]");
    if (active) { const t = active.dataset.t; setTool("none"); setTool(t); }
  } else {
    A.labelChosen = true;
    if (!id) A.labels.clear();
    else if (p.multiclass) A.labels.has(id) ? A.labels.delete(id) : A.labels.add(id);
    else { A.labels.clear(); A.labels.add(id); }
  }
  renderClassButtons();
}

function onKey(e) {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
  const p = S.project, k = e.key.toLowerCase();
  if (/^[0-9]$/.test(k)) {
    const n = Number(k);
    if (n === 0) pickClass(0);
    else if (p.classes[n - 1]) pickClass(Number(p.classes[n - 1].id));
    e.preventDefault(); return;
  }
  if (k === "enter") { save("done"); e.preventDefault(); }
  else if (k === "s") save("skip");
  else if (k === "u") save("uncertain");
  else if (k === "r") $(".toolstrip [data-t=ref]").click();
  else if (k === "b") { const s = $("#srcSel"); s.selectedIndex = (s.selectedIndex + 1) % s.options.length; s.onchange(); }
  else if (p.annotation_type === "segmentation") {
    if (k === "p") setTool("poly");
    else if (k === "e") setTool("edit");
    else if (k === "delete" || k === "backspace") setTool("del");
    else if (k === "escape") setTool("none");
  }
}

async function openNext() {
  try {
    const st = $("#stratumSel")?.value || "";
    const r = await api(`/api/projects/${S.project.id}/next?stratum=${encodeURIComponent(st)}&exclude=${A.chipId || 0}`);
    if (!r.chip_id) {
      A.chipId = null;
      $("#chipmsg").classList.remove("hidden");
      $("#chipmsg").textContent = "Não há mais chips disponíveis para você neste projeto/estrato. 🎉";
      $("#chipTitle").textContent = "Fila vazia";
      return;
    }
    history.replaceState(null, "", `#/p/${S.project.id}/annotate/${r.chip_id}`);
    await openChip(r.chip_id);
  } catch (e) { fail(e); }
}

async function openChip(id) {
  const p = S.project, seg = p.annotation_type === "segmentation";
  const c = await api(`/api/chips/${id}`);
  if (c.project_id !== p.id) throw new Error("Chip de outro projeto.");
  A.chip = c; A.chipId = c.id;
  const S0 = c.size_px;
  A.bounds = L.latLngBounds([[-S0, 0], [0, S0]]);
  $("#chipTitle").textContent = `Chip #${c.id}`;
  $("#chipInfo").innerHTML = `estrato <b>${esc(c.stratum)}</b> · ${c.lat.toFixed(5)}, ${c.lon.toFixed(5)} · EPSG:${c.epsg}<br>
    ${c.size_px}×${c.size_px} px · ${c.resolution_m} m · ${c.n_annotations}/${c.annotators_per_chip} anotações
    ${c.my_annotation ? `<br><span class="pill">já anotado por você (${esc(c.my_annotation.status)})</span>` : ""}`;
  // limpa camadas
  drawnLayers().forEach((l) => A.map.removeLayer(l));
  A.refLayer.clearLayers(); A.otherLayer.clearLayers();
  // referência
  L.geoJSON(c.reference, {
    pmIgnore: true, interactive: false, coordsToLatLng: (xy) => toLL(xy[0], xy[1]),
    style: (f) => ({ color: classById(f.properties.class_id)?.color || "#ffd60a", weight: 1.5, dashArray: "4,4", fill: false }),
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 4 }),
  }).addTo(A.refLayer);
  (c.other_annotations || []).forEach((o) => L.geoJSON(o.features, {
    pmIgnore: true, interactive: false, coordsToLatLng: (xy) => toLL(xy[0], xy[1]),
    style: (f) => ({ color: classById(f.properties.class_id)?.color || "#fff", weight: 1, dashArray: "1,4", fill: false }),
  }).addTo(A.otherLayer));
  // anotação existente
  A.labels = new Set((c.my_annotation?.labels || []).map(Number));
  A.labelChosen = !!c.my_annotation && c.my_annotation.status !== "skip";
  $("#comment").value = c.my_annotation?.comment || "";
  if (seg && c.my_annotation) {
    L.geoJSON(c.my_annotation.features, { coordsToLatLng: (xy) => toLL(xy[0], xy[1]) }).eachLayer((l) => {
      const poly = L.polygon(l.getLatLngs()).addTo(A.map);
      tagDrawn(poly, l.feature.properties.class_id);
    });
  }
  if (c.my_annotation?.source_id && p.image_sources.some((s) => s.id === c.my_annotation.source_id)) $("#srcSel").value = c.my_annotation.source_id;
  renderClassButtons();
  fitChip();
  // contexto
  if (A.ctxRect) A.ctx.removeLayer(A.ctxRect);
  A.ctxRect = L.geoJSON(c.footprint, { style: { color: "#e63946", weight: 2, fill: false } }).addTo(A.ctx);
  A.ctx.fitBounds(A.ctxRect.getBounds(), { maxZoom: 13, padding: [40, 40] });
  setTimeout(() => A.ctx.invalidateSize(), 50);
  await loadImage();
  if ($("#cmpSel").value) loadCompare();
}

function fitChip() { if (A.bounds) A.map.fitBounds(A.bounds, { padding: [10, 10] }); }

function imageUrl(srcId, withBands = true) {
  const q = new URLSearchParams({ source: srcId });
  if (withBands && A.rgb && A.rgbSrc === srcId) q.set("rgb", A.rgb.join(","));
  const mn = $("#stMin").value, mx = $("#stMax").value;
  if (withBands && mn !== "" && mx !== "") q.set("stretch", `${mn},${mx}`);
  return `/api/chips/${A.chipId}/image.png?${q}`;
}

function loadOverlay(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(url);
    img.onerror = async () => {
      try { const r = await fetch(url); const j = await r.json(); reject(new Error(j.detail || "Erro ao obter imagem")); }
      catch (_) { reject(new Error("Erro ao obter imagem")); }
    };
    img.src = url;
  });
}

async function loadImage(refreshBands = true) {
  const srcId = $("#srcSel").value, chipId = A.chipId;
  const msg = $("#chipmsg");
  msg.classList.remove("hidden");
  msg.textContent = "Buscando imagem… (a primeira leitura de cada base pode levar alguns segundos)";
  try {
    if (refreshBands || A.rgbSrc !== srcId) {
      const info = await api(`/api/chips/${chipId}/image-info?source=${encodeURIComponent(srcId)}`);
      if (chipId !== A.chipId) return;
      A.bands = info.bands; A.rgbSrc = srcId;
      A.rgb = A.rgb && A.rgb.every((b) => info.bands.includes(b)) ? A.rgb : info.source.rgb.filter((b) => info.bands.includes(b));
      if (!A.rgb.length) A.rgb = info.bands.slice(0, 3);
      const st = info.source.stretch;
      if (st && $("#stMin").value === "" && $("#stMax").value === "") { $("#stMin").placeholder = st[0]; $("#stMax").placeholder = st[1]; }
      const m = info.meta || {};
      $("#imgMeta").innerHTML = [m.dates?.length ? `cenas: ${m.dates.slice(0, 6).join(", ")}${m.dates.length > 6 ? "…" : ""}` : "",
        m.valid_fraction !== undefined ? `pixels válidos: ${(100 * m.valid_fraction).toFixed(0)}%` : "",
        `<a href="/api/chips/${chipId}/image.tif?source=${encodeURIComponent(srcId)}">GeoTIFF</a>`].filter(Boolean).join(" · ");
      $("#rgbBox").innerHTML = ["R", "G", "B"].map((ch, i) => `<div><label>${ch}</label><select data-i="${i}">
        ${A.bands.map((b) => `<option ${A.rgb[i] === b ? "selected" : ""}>${esc(b)}</option>`).join("")}</select></div>`).join("");
      $$("#rgbBox select").forEach((s) => (s.onchange = () => { A.rgb = $$("#rgbBox select").map((x) => x.value); loadImage(false); }));
    }
    const url = imageUrl(srcId);
    await loadOverlay(url);
    if (chipId !== A.chipId) return;
    if (A.imgLayer) A.map.removeLayer(A.imgLayer);
    A.imgLayer = L.imageOverlay(url, A.bounds, { pmIgnore: true, className: "pix", interactive: false }).addTo(A.map);
    A.imgLayer.bringToBack();
    msg.classList.add("hidden");
  } catch (e) {
    msg.textContent = `⚠ ${e.message}`;
  }
}

async function loadCompare() {
  if (A.cmpLayer) { A.map.removeLayer(A.cmpLayer); A.cmpLayer = null; }
  const sid = $("#cmpSel").value;
  if (!sid) return;
  try {
    const url = imageUrl(sid, false);
    await loadOverlay(url);
    A.cmpLayer = L.imageOverlay(url, A.bounds, { pmIgnore: true, opacity: Number($("#cmpOp").value), interactive: false }).addTo(A.map);
    if (A.imgLayer) A.cmpLayer.bringToFront();
  } catch (e) { fail(e); }
}

function useReference() {
  const feats = (A.chip.reference?.features || []).filter((f) => /Polygon/.test(f.geometry.type));
  if (!feats.length) return toast("Sem polígonos de referência neste chip.");
  let n = 0;
  L.geoJSON({ type: "FeatureCollection", features: feats }, { coordsToLatLng: (xy) => toLL(xy[0], xy[1]) }).eachLayer((l) => {
    const cid = l.feature.properties.class_id ?? A.classId;
    const lls = l.getLatLngs();
    const polys = l instanceof L.Polygon && Array.isArray(lls[0]) && Array.isArray(lls[0][0]) ? lls : [lls];
    polys.forEach((ll) => { tagDrawn(L.polygon(ll).addTo(A.map), cid); n++; });
  });
  toast(`${n} polígono(s) copiados — ajuste os vértices com "Editar".`);
}

async function save(status) {
  if (!A.chipId) return;
  const p = S.project, seg = p.annotation_type === "segmentation";
  if (!seg && status === "done" && !A.labelChosen) return toast("Escolha um rótulo (presença/ausência ou classe) antes de salvar.", true);
  const features = seg ? {
    type: "FeatureCollection",
    features: drawnLayers().map((l) => ({ type: "Feature", properties: { class_id: l.feature.properties.class_id }, geometry: llToPixelGeom(l.toGeoJSON().geometry) })),
  } : { type: "FeatureCollection", features: [] };
  try {
    await api(`/api/chips/${A.chipId}/annotation`, { method: "PUT", body: {
      source_id: $("#srcSel").value, status, labels: [...A.labels], features, comment: $("#comment").value } });
    toast(status === "skip" ? "Chip pulado." : "Salvo ✓");
    loadHistory();
    await openNext();
  } catch (e) { fail(e); }
}

async function loadHistory() {
  try {
    const rows = await api(`/api/projects/${S.project.id}/annotations?mine=true&limit=12`);
    $("#history").innerHTML = rows.length ? rows.map((r) => `<div class="row"><a href="#/p/${S.project.id}/annotate/${r.chip_id}">#${r.chip_id}</a>
      <span class="muted">${esc(r.status)}</span><span>${esc((r.labels || []).map((i) => classById(i)?.name || i).join(", ") || "ausente")}</span></div>`).join("")
      : '<span class="muted">nenhuma ainda</span>';
  } catch (_) {}
}

/* =========================================================== mapa geral */
async function renderMap() {
  const p = S.project;
  const [chips, layers] = await Promise.all([api(`/api/projects/${p.id}/chips`), api(`/api/projects/${p.id}/layers`)]);
  tabview().innerHTML = `
    <div class="row" style="margin-bottom:.6rem">
      <div class="legend"><span><span class="swatch" style="background:#2d6a4f"></span>completo</span>
      <span><span class="swatch" style="background:#f4a261"></span>parcial</span><span><span class="swatch" style="background:#8d99ae"></span>pendente</span>
      <span><span class="swatch" style="border:2px solid #e63946"></span>anotado por mim</span></div>
      <div class="spacer"></div>
      <label class="small" style="margin:0">Camada p/ chip manual</label>
      <select id="manLayer" style="width:auto"><option value="">(nenhuma)</option>${layers.map((l) => `<option value="${l.id}">${esc(l.name)}</option>`).join("")}</select>
      <button id="manBtn">+ Chip por clique</button>
    </div>
    <div id="overviewmap"></div>`;
  const map = L.map("overviewmap");
  const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
  const eox = L.tileLayer("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2023_3857/default/GoogleMapsCompatible/{z}/{y}/{x}.jpg",
    { maxZoom: 17, attribution: "Sentinel-2 cloudless 2023 © EOX (CC BY-NC-SA 4.0)" });
  const overlays = {};
  const all = L.featureGroup();
  for (const l of layers) {
    try {
      const gj = await api(`/api/layers/${l.id}/geojson`);
      const strata = [...new Set(gj.features.map((f) => f.properties.stratum))];
      const lay = L.geoJSON(gj, { style: (f) => ({ color: PALETTE[strata.indexOf(f.properties.stratum) % PALETTE.length], weight: 1, fillOpacity: 0.15 }),
        onEachFeature: (f, ly) => ly.bindTooltip(String(f.properties.stratum ?? l.name)) });
      overlays[`${l.name} (${l.kind})`] = lay.addTo(map); all.addLayer(lay);
    } catch (_) {}
  }
  const pts = L.featureGroup(chips.map((c) => L.circleMarker([c.lat, c.lon], {
    radius: 5, weight: c.mine ? 2.5 : 1, color: c.mine ? "#e63946" : "#222",
    fillColor: c.complete ? "#2d6a4f" : c.n_annotations ? "#f4a261" : "#8d99ae", fillOpacity: 0.9 })
    .bindTooltip(`#${c.id} · ${c.stratum} · ${c.n_annotations} anot.`)
    .on("click", () => (location.hash = `#/p/${p.id}/annotate/${c.id}`)))).addTo(map);
  overlays["Chips"] = pts; all.addLayer(pts);
  L.control.layers({ OpenStreetMap: osm, "Sentinel-2 cloudless (EOX)": eox }, overlays).addTo(map);
  if (all.getLayers().length && all.getBounds().isValid()) map.fitBounds(all.getBounds(), { padding: [20, 20] }); else map.setView([-3, -55], 4);
  let manual = false;
  $("#manBtn").onclick = () => { manual = !manual; $("#manBtn").classList.toggle("active", manual); map.getContainer().style.cursor = manual ? "crosshair" : ""; };
  map.on("click", async (e) => {
    if (!manual) return;
    try {
      const r = await api(`/api/projects/${p.id}/chips/manual`, { method: "POST", body: { lon: e.latlng.lng, lat: e.latlng.lat, layer_id: Number($("#manLayer").value) || null } });
      toast(`Chip #${r.id} criado.`);
      location.hash = `#/p/${p.id}/annotate/${r.id}`;
    } catch (err) { fail(err); }
  });
  cleanup = () => map.remove();
}

/* =========================================================== configuração */
async function renderSetup() {
  const p = S.project;
  const [layers, rasters] = await Promise.all([api(`/api/projects/${p.id}/layers`), api(`/api/projects/${p.id}/rasters`)]);
  S.rasters = rasters;
  tabview().innerHTML = `
  <div class="stack">
    <div class="card"><h2>1. Parâmetros do projeto</h2><div id="pform"></div></div>
    <div class="card"><h2>2. Bases de imagem (sensores e períodos)</h2>
      <p class="muted small">As imagens de cada chip são buscadas sob demanda e reamostradas para a grade do chip
      (${p.chip_size_px}px × ${p.resolution_m} m). Planetary Computer e Earth Search são gratuitos e não exigem conta;
      o Google Earth Engine é gratuito para ONGs/pesquisa (exige projeto Cloud registrado — ${S.config.gee_project ? "configurado ✓" : "<b>não configurado no servidor</b>"}).</p>
      <div id="sources"></div>
      <div class="row"><button id="addSrc">+ base de imagem</button><button class="primary" id="saveSrc">Salvar bases</button></div>
    </div>
    <div class="grid2">
      <div class="card"><h2>3. Camadas de referência</h2>
        <p class="muted small">Vetor (GPKG, GeoJSON, SHP zipado, KML, FGB, Parquet) com um campo de classe para estratificar,
        ou raster categórico (GeoTIFF, ex.: MapBiomas, PRODES).</p>
        <div class="field"><label>Arquivo(s)</label><input id="lyFiles" type="file" multiple></div>
        <div class="inline-fields"><div class="field"><label>Nome</label><input id="lyName"></div>
          <div class="field"><label>Tema</label><input id="lyTheme" placeholder="desmatamento, LULC…"></div>
          <div class="field"><label>Campo de classe (vetor)</label><input id="lyField" placeholder="ex.: classe"></div></div>
        <button class="primary" id="lyUp">Enviar camada</button>
        <table style="margin-top:.8rem"><thead><tr><th>Camada</th><th>Estratos</th><th></th></tr></thead><tbody>
        ${layers.map((l) => `<tr><td><b>${esc(l.name)}</b><br><span class="muted small">${esc(l.theme)} · ${l.kind} · ${l.n_features} feições ${l.class_field ? "· campo " + esc(l.class_field) : ""}</span></td>
          <td class="small">${Object.entries(l.strata).slice(0, 12).map(([k, v]) => `${esc(k)}: ${v}`).join("<br>")}</td>
          <td><button class="ghost danger small" data-dl="${l.id}">✕</button></td></tr>`).join("")}</tbody></table>
      </div>
      <div class="card"><h2>Rasters locais (opcional)</h2>
        <p class="muted small">Envie mosaicos próprios (GeoTIFF/COG — ex.: Planet NICFI baixado, CBERS, drone) para usar como base de imagem.</p>
        <div class="field"><label>GeoTIFF</label><input id="rsFile" type="file" accept=".tif,.tiff"></div>
        <div class="field"><label>Nome</label><input id="rsName"></div>
        <button class="primary" id="rsUp">Enviar raster</button>
        <table style="margin-top:.8rem"><tbody>${rasters.map((r) => `<tr><td>#${r.id} <b>${esc(r.name)}</b><br>
          <span class="muted small">${r.count} bandas (${esc(r.band_names.join(", "))}) · ${esc(r.crs)}</span></td>
          <td><button class="ghost danger small" data-dr="${r.id}">✕</button></td></tr>`).join("")}</tbody></table>
      </div>
    </div>
    <div class="card"><h2>4. Gerar chips (amostragem estratificada)</h2>
      <div class="inline-fields">
        <div class="field"><label>Camada</label><select id="gLayer">${layers.map((l) => `<option value="${l.id}">${esc(l.name)}</option>`).join("")}</select></div>
        <div class="field"><label>Chips por estrato</label><input id="gN" type="number" value="50" min="1"></div>
        <div class="field"><label>Chips de fundo (fora das feições)</label><input id="gBg" type="number" value="0" min="0"></div>
        <div class="field"><label>Espaçamento mín. (px)</label><input id="gSp" type="number" placeholder="${p.chip_size_px} (sem sobreposição)"></div>
        <div class="field"><label>Posição</label><select id="gPl"><option value="random">aleatória dentro da feição</option><option value="centroid">centro da feição</option></select></div>
        <div class="field"><label>Semente</label><input id="gSeed" type="number" value="42"></div>
      </div>
      <div id="gStrata"></div>
      <div class="row"><button class="primary" id="gGo">Gerar chips</button>
        <span class="muted small">Chips atuais: ${p.stats.chips}</span><div class="spacer"></div>
        <button class="danger" id="gDel">Apagar chips sem anotação</button></div>
    </div>
    <div class="card"><h2 class="danger">Zona de perigo</h2><button class="danger" id="delProj">Excluir projeto</button></div>
  </div>`;

  // formulário do projeto
  const pf = $("#pform");
  pf.innerHTML = projectForm(p, false);
  bindClassEditor(pf);
  $("#saveProj", pf).onclick = async () => {
    try { await api(`/api/projects/${p.id}`, { method: "PATCH", body: readProjectForm(pf) }); toast("Projeto salvo."); route(); } catch (e) { fail(e); }
  };
  // fontes
  const srcBox = $("#sources");
  const draw = (list) => { srcBox.innerHTML = list.map(sourceRow).join("") || '<p class="muted">Nenhuma base configurada.</p>'; bindSourceRows(); };
  const bindSourceRows = () => {
    $$(".source-row", srcBox).forEach((row) => {
      $("[name=provider]", row).onchange = () => { const cur = readSources(); draw(cur); };
      $(".rmsrc", row).onclick = () => { row.remove(); };
    });
  };
  draw(p.image_sources);
  $("#addSrc").onclick = () => {
    const cur = readSources();
    const y = new Date().getFullYear() - 1;
    cur.push({ id: `s2-${y}`, label: `Sentinel-2 ${y}`, provider: "planetary_computer", sensor: "sentinel-2", start: `${y}-06-01`, end: `${y}-09-30`, max_cloud: 20, composite: "median" });
    draw(cur);
  };
  $("#saveSrc").onclick = async () => {
    try { await api(`/api/projects/${p.id}`, { method: "PATCH", body: { image_sources: readSources() } }); toast("Bases salvas."); route(); } catch (e) { fail(e); }
  };
  // camadas
  $("#lyUp").onclick = async () => {
    const files = $("#lyFiles").files;
    if (!files.length) return toast("Selecione o(s) arquivo(s).", true);
    const fd = new FormData();
    [...files].forEach((f) => fd.append("files", f));
    fd.append("name", $("#lyName").value); fd.append("theme", $("#lyTheme").value); fd.append("class_field", $("#lyField").value);
    $("#lyUp").disabled = true;
    try { const r = await api(`/api/projects/${p.id}/layers`, { method: "POST", body: fd }); toast(`Camada enviada: ${r.n_features} feições. Campos: ${(r.fields || []).join(", ")}`); route(); }
    catch (e) { fail(e); } finally { $("#lyUp").disabled = false; }
  };
  $$("[data-dl]").forEach((b) => (b.onclick = async () => { if (confirm("Remover camada?")) { await api(`/api/layers/${b.dataset.dl}`, { method: "DELETE" }).catch(fail); route(); } }));
  $("#rsUp").onclick = async () => {
    const f = $("#rsFile").files[0];
    if (!f) return toast("Selecione um GeoTIFF.", true);
    const fd = new FormData(); fd.append("file", f); fd.append("name", $("#rsName").value);
    $("#rsUp").disabled = true;
    try { await api(`/api/projects/${p.id}/rasters`, { method: "POST", body: fd }); toast("Raster enviado."); route(); }
    catch (e) { fail(e); } finally { $("#rsUp").disabled = false; }
  };
  $$("[data-dr]").forEach((b) => (b.onclick = async () => { if (confirm("Remover raster?")) { await api(`/api/rasters/${b.dataset.dr}`, { method: "DELETE" }).catch(fail); route(); } }));
  // geração
  const showStrata = () => {
    const l = layers.find((x) => x.id === Number($("#gLayer").value));
    if (!l) { $("#gStrata").innerHTML = '<p class="muted">Envie uma camada de referência primeiro.</p>'; return; }
    $("#gStrata").innerHTML = `<table><thead><tr><th>Usar</th><th>Estrato</th><th>Feições/pixels</th><th>Classe do projeto (pré-rotulagem)</th></tr></thead><tbody>
      ${Object.entries(l.strata).map(([k, v]) => `<tr><td><input type="checkbox" data-s="${esc(k)}" checked></td><td>${esc(k)}</td><td>${v}</td>
      <td><select data-m="${esc(k)}"><option value="">—</option>${p.classes.map((c) => `<option value="${c.id}" ${c.name === k ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></td></tr>`).join("")}
      </tbody></table>`;
  };
  $("#gLayer").onchange = showStrata; showStrata();
  $("#gGo").onclick = async () => {
    const strata = $$("[data-s]").filter((c) => c.checked).map((c) => c.dataset.s);
    const class_map = {}; $$("[data-m]").forEach((s) => s.value && (class_map[s.dataset.m] = Number(s.value)));
    $("#gGo").disabled = true; $("#gGo").textContent = "Gerando…";
    try {
      const r = await api(`/api/projects/${p.id}/chips/generate`, { method: "POST", body: {
        layer_id: Number($("#gLayer").value), n_per_stratum: Number($("#gN").value), n_background: Number($("#gBg").value),
        min_spacing_px: $("#gSp").value ? Number($("#gSp").value) : null, placement: $("#gPl").value, seed: Number($("#gSeed").value), strata, class_map } });
      toast(`${r.created} chips criados.`); route();
    } catch (e) { fail(e); } finally { $("#gGo").disabled = false; $("#gGo").textContent = "Gerar chips"; }
  };
  $("#gDel").onclick = async () => { if (!confirm("Apagar todos os chips ainda não anotados?")) return; const r = await api(`/api/projects/${p.id}/chips`, { method: "DELETE" }).catch(fail); r && toast(`${r.deleted} chips removidos.`); route(); };
  $("#delProj").onclick = async () => { if (prompt(`Digite o nome do projeto para excluir: ${p.name}`) !== p.name) return; await api(`/api/projects/${p.id}`, { method: "DELETE" }).catch(fail); location.hash = "#/"; };
}

function sourceRow(s) {
  const cat = S.config.catalog;
  const prov = s.provider || "planetary_computer";
  const sensors = cat.providers[prov].sensors;
  const sensor = sensors.includes(s.sensor) ? s.sensor : sensors[0];
  const isLocal = prov === "local";
  const list = (a) => (Array.isArray(a) ? a.join(",") : a || "");
  return `<div class="source-row"><div class="inline-fields">
    <div><label>Id</label><input name="id" value="${esc(s.id)}"></div>
    <div><label>Rótulo</label><input name="label" value="${esc(s.label)}"></div>
    <div><label>Provedor</label><select name="provider">${Object.entries(cat.providers).map(([k, v]) => `<option value="${k}" ${k === prov ? "selected" : ""} title="${esc(v.label)}">${esc(v.label.split(" (")[0])}</option>`).join("")}</select></div>
    ${isLocal ? `<div><label>Raster</label><select name="raster_id">${(S.rasters || []).map((r) => `<option value="${r.id}" ${Number(s.raster_id) === r.id ? "selected" : ""}>#${r.id} ${esc(r.name)}</option>`).join("")}</select></div>`
      : `<div><label>Sensor</label><select name="sensor">${sensors.map((k) => `<option value="${k}" ${k === sensor ? "selected" : ""}>${esc(cat.sensors[k].label)}</option>`).join("")}</select></div>
    <div><label>Início</label><input name="start" type="date" value="${esc(s.start)}"></div>
    <div><label>Fim</label><input name="end" type="date" value="${esc(s.end)}"></div>
    <div><label>Nuvens máx. (%)</label><input name="max_cloud" type="number" value="${s.max_cloud ?? 30}"></div>
    <div><label>Composição</label><select name="composite"><option value="median" ${s.composite !== "least_cloudy" ? "selected" : ""}>mediana</option><option value="least_cloudy" ${s.composite === "least_cloudy" ? "selected" : ""}>cena menos nublada</option></select></div>`}
    <div><label>Bandas exportadas</label><input name="bands" value="${esc(list(s.bands))}" placeholder="padrão do sensor"></div>
    <div><label>RGB</label><input name="rgb" value="${esc(list(s.rgb))}" placeholder="padrão"></div>
    <div><label>Contraste (mín,máx)</label><input name="stretch" value="${esc(list(s.stretch))}" placeholder="auto 2–98%"></div>
    ${prov === "gee" ? `<div><label>Coleção GEE (custom/NICFI)</label><input name="collection" value="${esc(s.collection)}" placeholder="opcional"></div>` : ""}
  </div><div class="row" style="margin-top:.4rem"><div class="spacer"></div><button class="ghost danger small rmsrc">remover</button></div></div>`;
}
function readSources() {
  return $$("#sources .source-row").map((row) => {
    const g = (n) => { const el = $(`[name=${n}]`, row); return el ? el.value.trim() : undefined; };
    const arr = (v) => (v ? v.split(",").map((x) => x.trim()).filter(Boolean) : undefined);
    const o = { id: g("id"), label: g("label"), provider: g("provider") };
    if (o.provider === "local") o.raster_id = Number(g("raster_id"));
    else Object.assign(o, { sensor: g("sensor"), start: g("start"), end: g("end"), max_cloud: Number(g("max_cloud")), composite: g("composite"), collection: g("collection") || undefined });
    o.bands = arr(g("bands")); o.rgb = arr(g("rgb"));
    const st = arr(g("stretch")); o.stretch = st && st.length === 2 ? st.map(Number) : null;
    return o;
  });
}

/* =========================================================== exportação */
async function renderExport() {
  const p = S.project;
  const c = S.config;
  tabview().innerHTML = `
  <div class="grid2">
    <div class="card stack"><h2>Exportar amostras</h2>
      <div><label>Bases de imagem a incluir</label>${p.image_sources.map((s) => `<label class="row" style="color:var(--ink)"><input type="checkbox" data-src="${esc(s.id)}" checked> ${esc(s.label || s.id)}</label>`).join("")}</div>
      <div><label>Status</label>
        <label class="row" style="color:var(--ink)"><input type="checkbox" data-st="done" checked> concluídas</label>
        <label class="row" style="color:var(--ink)"><input type="checkbox" data-st="uncertain"> incertas</label></div>
      <label class="row" style="color:var(--ink)"><input type="checkbox" id="exImg" checked> incluir imagens (GeoTIFF por chip)</label>
      <div class="inline-fields"><div><label>Treino</label><input id="spT" type="number" step="0.05" value="0.7"></div>
        <div><label>Validação</label><input id="spV" type="number" step="0.05" value="0.15"></div>
        <div><label>Teste</label><input id="spE" type="number" step="0.05" value="0.15"></div></div>
      <div class="card tight"><h3>Destino</h3>
        <p class="small muted">O arquivo .zip fica sempre disponível para download (salvar no computador).</p>
        ${c.drive_oauth ? (S.me.drive_connected
          ? `<div class="row"><span class="pill">Google Drive conectado</span><button class="ghost small" id="drvOff">desconectar</button></div>`
          : `<button id="drvOn">Conectar meu Google Drive</button>`) : (c.drive_service_account ? '<span class="pill">Drive via conta de serviço</span>' : '<span class="muted small">Google Drive não configurado no servidor (veja README).</span>')}
        <label class="row" style="color:var(--ink);margin-top:.5rem"><input type="checkbox" id="exDrive" ${(S.me.drive_connected || c.drive_service_account) ? "" : "disabled"}> enviar também para o Google Drive (pasta "ChipCollector")</label>
      </div>
      <button class="primary" id="exGo">Gerar pacote</button>
      <p class="small muted">Conteúdo: GeoTIFFs multibanda, máscaras de segmentação (GeoTIFF uint8), COCO JSON, labels.csv,
      GeoJSON das anotações e dos chips, divisão treino/val/teste e metadados com autoria.</p>
    </div>
    <div class="card"><h2>Exportações</h2><div id="jobs"></div></div>
  </div>`;
  const drvOn = $("#drvOn");
  if (drvOn) drvOn.onclick = async () => { try { const r = await api("/api/drive/authorize"); location.href = r.url; } catch (e) { fail(e); } };
  const drvOff = $("#drvOff");
  if (drvOff) drvOff.onclick = async () => { await api("/api/drive/disconnect", { method: "POST" }); S.me.drive_connected = false; route(); };
  $("#exGo").onclick = async () => {
    try {
      await api(`/api/projects/${p.id}/exports`, { method: "POST", body: {
        source_ids: $$("[data-src]").filter((x) => x.checked).map((x) => x.dataset.src),
        statuses: $$("[data-st]").filter((x) => x.checked).map((x) => x.dataset.st),
        include_images: $("#exImg").checked, split: [Number($("#spT").value), Number($("#spV").value), Number($("#spE").value)],
        drive: $("#exDrive").checked } });
      toast("Exportação iniciada."); loadJobs();
    } catch (e) { fail(e); }
  };
  const loadJobs = async () => {
    const jobs = await api(`/api/projects/${p.id}/exports`);
    $("#jobs").innerHTML = jobs.length ? `<table><tbody>${jobs.map((j) => `<tr><td>#${j.id}<br><span class="small muted">${esc(j.created_at.slice(0, 16).replace("T", " "))}</span></td>
      <td>${j.status === "done" ? `<a href="/api/exports/${j.id}/download">⬇ ${esc(j.filename)}</a>${j.drive_file_id ? `<br><a class="small" target="_blank" href="https://drive.google.com/file/d/${esc(j.drive_file_id)}/view">abrir no Drive</a>` : ""}`
        : j.status === "error" ? `<span style="color:var(--danger)">${esc(j.message)}</span>`
        : `<div class="progress"><div style="width:${Math.round(100 * j.progress)}%"></div></div><span class="small muted">${esc(j.message || j.status)}</span>`}</td></tr>`).join("")}</tbody></table>`
      : '<p class="muted">Nenhuma exportação.</p>';
    return jobs.some((j) => j.status === "pending" || j.status === "running");
  };
  let timer = null;
  const poll = async () => { const running = await loadJobs().catch(() => false); timer = setTimeout(poll, running ? 1500 : 6000); };
  poll();
  cleanup = () => clearTimeout(timer);
}

/* =========================================================== progresso / equipe */
async function renderTeam() {
  const p = await api(`/api/projects/${S.project.id}`);
  const st = p.stats;
  const rows = Object.entries(st.by_user).sort((a, b) => sum(b[1]) - sum(a[1]));
  tabview().innerHTML = `
  <div class="grid2" style="margin-bottom:1rem">
    <div class="card"><div class="muted small">Chips</div><div class="stat">${st.chips}</div></div>
    <div class="card"><div class="muted small">Chips completos</div><div class="stat">${st.chips_complete}</div></div>
    <div class="card"><div class="muted small">Anotações</div><div class="stat">${st.annotations}</div></div>
    <div class="card"><div class="muted small">Anotadores</div><div class="stat">${rows.length}</div></div>
  </div>
  <div class="grid2">
    <div class="card"><h2>Por anotador</h2><table><thead><tr><th>Usuário</th><th>Concluídas</th><th>Incertas</th><th>Puladas</th></tr></thead><tbody>
      ${rows.map(([u, s]) => `<tr><td>${esc(u)}</td><td>${s.done || 0}</td><td>${s.uncertain || 0}</td><td>${s.skip || 0}</td></tr>`).join("")}</tbody></table></div>
    <div class="card"><h2>Por rótulo</h2><table><tbody>${Object.entries(st.by_label).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join("")}</tbody></table></div>
    <div class="card"><h2>Chips por estrato</h2><table><tbody>${Object.entries(st.by_stratum).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join("")}</tbody></table></div>
  </div>`;
}
const sum = (o) => Object.values(o).reduce((a, b) => a + b, 0);

async function renderUsers() {
  crumbs([["Projetos", "#/"], ["Usuários"]]);
  const users = await api("/api/users");
  view().innerHTML = `<div class="grid2">
    <div class="card"><h2>Usuários</h2><table><thead><tr><th>Usuário</th><th>Admin</th><th>Ativo</th></tr></thead><tbody>
    ${users.map((u) => `<tr><td><b>${esc(u.username)}</b><br><span class="small muted">${esc(u.full_name)} ${esc(u.email)}</span></td>
      <td><input type="checkbox" data-adm="${u.id}" ${u.is_admin ? "checked" : ""} ${u.id === S.me.id ? "disabled" : ""}></td>
      <td><input type="checkbox" data-act="${u.id}" ${u.is_active ? "checked" : ""} ${u.id === S.me.id ? "disabled" : ""}></td></tr>`).join("")}</tbody></table></div>
    <div class="card stack"><h2>Novo usuário</h2>
      <div class="field"><label>Usuário</label><input id="nu"></div><div class="field"><label>Senha inicial</label><input id="np" type="text"></div>
      <div class="field"><label>Nome</label><input id="nn"></div><div class="field"><label>E-mail</label><input id="ne"></div>
      <label class="row" style="color:var(--ink)"><input type="checkbox" id="na"> administrador</label>
      <button class="primary" id="nGo">Criar</button></div></div>`;
  $$("[data-adm]").forEach((c) => (c.onchange = () => api(`/api/users/${c.dataset.adm}`, { method: "PATCH", body: { is_admin: c.checked } }).catch(fail)));
  $$("[data-act]").forEach((c) => (c.onchange = () => api(`/api/users/${c.dataset.act}`, { method: "PATCH", body: { is_active: c.checked } }).catch(fail)));
  $("#nGo").onclick = async () => {
    try { await api("/api/users", { method: "POST", body: { username: $("#nu").value, password: $("#np").value, full_name: $("#nn").value, email: $("#ne").value, is_admin: $("#na").checked } }); toast("Usuário criado."); route(); }
    catch (e) { fail(e); }
  };
}

route();
