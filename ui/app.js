// clearance review UI. Plain modules, no build step.

const $ = (sel, el = document) => el.querySelector(sel);
const view = $("#view");
const ROUTE_LABEL = { ready: "Ready", needs_info: "Needs info", human_review: "Human review" };
const ROUTE_ORDER = ["human_review", "needs_info", "ready"];
const OUTCOME_ICON = { pass: "✓", fail: "!", unverifiable: "?", skipped: "–" };
const OUTCOME_WORD = { pass: "Met", fail: "Not met", unverifiable: "Can't verify", skipped: "Not needed" };

let state = { orders: [], current: null, packs: [], pollTimer: null };

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(body?.detail || body || res.statusText);
  return body;
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const ACRONYMS = { f2f: "F2F", npi: "NPI", mbi: "MBI", ahi: "AHI", rdi: "RDI", spo2: "SpO2", po2: "PO2", dob: "DOB", hcpcs: "HCPCS" };
const human = (id) => String(id ?? "").split("_").map((w, i) => ACRONYMS[w] || (i === 0 ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");
const pct = (x, d = 0) => (x == null ? "n/a" : `${(100 * x).toFixed(d)}%`);
const ago = (iso) => {
  if (!iso) return "";
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
};

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.append(t);
  setTimeout(() => t.remove(), 2600);
}

// --- routing ------------------------------------------------------------------------------

function go() {
  const [, tab = "queue", id] = location.hash.split("/");
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.tab === tab) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  clearInterval(state.pollTimer);
  if (tab === "packs") return showPacks(id);
  if (tab === "results") return showResults();
  return showQueue(id ? decodeURIComponent(id) : null);
}
window.addEventListener("hashchange", go);

// --- queue ----------------------------------------------------------------------------------

async function showQueue(selected) {
  if (!$(".desk")) {
    view.innerHTML = `<div class="desk">
      <aside class="queue" aria-label="Order queue"></aside>
      <section class="fax" aria-label="Fax packet"></section>
      <section class="findings" aria-label="Findings"></section></div>`;
  }
  await refreshQueue(selected);
  if (!selected && state.orders.length) {
    const first = state.orders.find((o) => !o.review) || state.orders[0];
    history.replaceState(null, "", `#/queue/${first.order_id}`);
    selected = first.order_id;
    markQueue(selected);
  }
  if (selected) await showOrder(selected);
  else {
    const loading = state.seed?.state === "running";
    $(".fax").innerHTML = `<div class="empty"><div><p>${loading ? "Loading the sample faxes. Orders appear on the left as they're processed." : "The queue is empty. Load the 60 synthetic sample faxes, or use <b>Add a fax</b> to process your own PDF."}</p>
      ${loading ? "" : `<button class="btn primary" id="seed">Load sample faxes</button>`}</div></div>`;
    $("#seed")?.addEventListener("click", async () => { await api("/demo/seed", { method: "POST" }); showQueue(null); });
    $(".findings").innerHTML = "";
  }
}

async function refreshQueue(selected) {
  const data = await api("/orders");
  state.orders = data.orders;
  state.seed = data.seed;
  const q = $(".queue");
  if (!q) return;
  const groups = ROUTE_ORDER.map((r) => ({ r, items: data.orders.filter((o) => o.route === r && !o.review) }));
  const reviewed = data.orders.filter((o) => o.review);
  let html = "";
  if (data.seed?.state === "running") {
    html += `<div class="seeding">Loading sample faxes… ${data.seed.done} of ${data.seed.total}</div>`;
    clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
      const had = state.orders.length;
      await refreshQueue(state.current?.decision?.order_id);
      if (!had && state.orders.length && !state.current) showQueue(null);
    }, 2500);
  } else clearInterval(state.pollTimer);
  for (const g of groups) {
    html += `<section class="queue-group"><h3><span>${ROUTE_LABEL[g.r]}</span><span>${g.items.length}</span></h3>`;
    html += g.items.map(item).join("") || `<div class="seeding">Nothing here.</div>`;
    html += `</section>`;
  }
  if (reviewed.length) {
    html += `<section class="queue-group"><h3><span>Reviewed</span><span>${reviewed.length}</span></h3>${reviewed.map(item).join("")}</section>`;
  }
  q.innerHTML = html;
  markQueue(selected);
  q.querySelectorAll(".queue-item").forEach((b) => b.addEventListener("click", () => { location.hash = `#/queue/${b.dataset.id}`; }));
}

function item(o) {
  const done = o.review ? ` done` : "";
  const tag = o.review ? (o.review === "override" ? "overridden" : "approved") : ago(o.received_at);
  return `<button class="queue-item${done}" data-id="${esc(o.order_id)}">
    <span class="who">${esc(o.patient || "Unknown patient")}<small>${esc(o.order_id)}</small></span>
    <span class="why">${esc(tag)}${o.review ? "" : " · "}${o.review ? "" : esc(o.headline.replace(/^[a-z_]+: /, ""))}</span></button>`;
}

function markQueue(id) {
  document.querySelectorAll(".queue-item").forEach((b) => b.setAttribute("aria-current", b.dataset.id === id ? "true" : "false"));
}

async function showOrder(id, fresh = false) {
  markQueue(id);
  let data;
  try { data = await api(`/orders/${encodeURIComponent(id)}`); }
  catch (e) { $(".fax").innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`; return; }
  state.current = data;
  renderFax(data, fresh);
  renderFindings(data);
}

function renderFax(data, fresh) {
  const d = data.decision;
  const pages = Array.from({ length: data.pages }, (_, i) => i + 1);
  const stamp = `<div class="stamp ${d.route}${fresh ? " fresh" : ""}" aria-hidden="true">${ROUTE_LABEL[d.route]}<small>${esc(d.pack_id)} v${esc(d.pack_version)}</small></div>`;
  $(".fax").innerHTML = `
    <div class="fax-head"><h2>${esc(d.extraction.fields.patient_name?.value || d.order_id)}</h2>
      <span class="muted">${esc(d.order_id)} · ${data.pages} pages · received ${esc(ago(data.meta.received_at))}</span>
      <a href="/orders/${encodeURIComponent(d.order_id)}/packet.pdf" target="_blank" rel="noopener">Open PDF</a></div>
    ${pages.map((n) => `<div class="page-label">Page ${n}${docType(d, n)}</div><figure class="sheet" id="page-${n}">
      <img src="/orders/${encodeURIComponent(d.order_id)}/pages/${n}.png" alt="Page ${n} of the faxed packet" loading="lazy">
      ${n === 1 ? stamp : ""}</figure>`).join("")}`;
}

function docType(d, n) {
  const p = d.extraction.pages.find((x) => x.number === n);
  return p?.document_type ? `: ${esc(human(p.document_type).toLowerCase())}` : ": unrecognized page";
}

function renderFindings(data) {
  const d = data.decision;
  const rv = data.review;
  const el = $(".findings");
  const reasons = d.reasons.map((r) => `<li>${esc(r.replace(/^[a-z_]+: /, (m) => human(m.slice(0, -2)) + ": "))}</li>`).join("");
  const flagged = d.results.filter((r) => r.outcome !== "pass");
  const passed = d.results.filter((r) => r.outcome === "pass");
  el.innerHTML = `
    <div class="verdict">
      <span class="route-chip ${d.route}">${ROUTE_LABEL[d.route]}</span>
      <h2>${headline(d)}</h2>
      <ul>${reasons}</ul>
      <div class="meta">Least certain field read: ${d.min_confidence == null ? "n/a" : d.min_confidence.toFixed(2)} · auto-ready bar ${d.threshold.toFixed(2)} · extractor ${esc(d.models.extractor)} · judge ${esc(d.models.judge)}</div>
      ${rv ? `<div class="reviewed">${rv.action === "override" ? `Overridden to <b>${ROUTE_LABEL[rv.final_route]}</b>` : `Approved as <b>${ROUTE_LABEL[rv.final_route]}</b>`} by ${esc(rv.reviewer)}${rv.note ? `: “${esc(rv.note)}”` : ""}. <button class="btn small" id="reopen">Reopen</button></div>`
        : reviewRow(d.route)}
    </div>
    <div class="section"><h3>Findings <small>${flagged.length} of ${d.results.length} rules flagged</small></h3>
      ${flagged.map((r) => ruleHtml(r, data, true)).join("")}
      ${passed.length ? `<details class="passed"${flagged.length ? "" : " open"}><summary>${passed.length} rule${passed.length === 1 ? "" : "s"} met</summary>${passed.map((r) => ruleHtml(r, data, true)).join("")}</details>` : ""}</div>
    ${d.outreach ? `<div class="section"><h3>Request to the referring office <button class="btn small" id="copy-letter">Copy text</button></h3><div class="letter">${md(d.outreach)}</div></div>` : ""}
    <div class="section"><h3>Extracted fields <small>click a value to correct it</small></h3>${fieldsTable(data)}</div>
    <div class="section"><h3>Audit trail</h3><ul class="audit">${data.audit.slice().reverse().map(auditLine).join("") || "<li>No events.</li>"}</ul></div>`;

  el.querySelectorAll("[data-ev]").forEach((b) => b.addEventListener("click", () => focusEvidence(JSON.parse(b.dataset.ev), b)));
  el.querySelectorAll(".val[data-field]").forEach((v) => v.addEventListener("click", () => editField(v)));
  el.querySelectorAll("[data-decide]").forEach((b) => b.addEventListener("click", () => {
    const route = b.dataset.decide;
    const note = $("#review-note")?.value || "";
    review(route === d.route ? { action: "approve", note } : { action: "override", route, note });
  }));
  $("#reopen", el)?.addEventListener("click", async () => { state.current = await api(`/orders/${d.order_id}/review`, { method: "DELETE" }); renderFindings(state.current); refreshQueue(d.order_id); });
  $("#copy-letter", el)?.addEventListener("click", () => navigator.clipboard.writeText(d.outreach).then(() => toast("Request copied")));
  drawMarks(data);
}

function reviewRow(route) {
  const btn = (r, label, primary) => `<button class="btn${primary ? " primary" : ""}" data-decide="${r}">${label}</button>`;
  const buttons = route === "human_review"
    ? btn("ready", "Mark ready", true) + btn("needs_info", "Request info from office", false)
    : route === "ready"
      ? btn("ready", "Confirm ready", true) + btn("needs_info", "Request info instead", false) + btn("human_review", "Hold for review", false)
      : btn("needs_info", "Confirm request", true) + btn("ready", "Mark ready instead", false) + btn("human_review", "Hold for review", false);
  return `<div class="review-row"><span class="decide">${route === "human_review" ? "Your call." : "Agree with the system?"} A different choice is logged as an override and becomes an eval case.</span>
    ${buttons}<input id="review-note" placeholder="Note (optional)" aria-label="Review note"></div>`;
}

function headline(d) {
  const n = d.results.filter((r) => r.outcome !== "pass").length;
  if (d.route === "ready") return "Complete. Every criterion is documented.";
  if (d.route === "needs_info") return `${d.missing_items.length} item${d.missing_items.length === 1 ? "" : "s"} to request from the office`;
  return n ? `A person needs to look at ${n} finding${n === 1 ? "" : "s"}` : "A person should confirm the low-confidence reads";
}

function ruleHtml(r, data, top) {
  const src = r.source ? data.sources[r.source] : null;
  const evs = r.evidence.filter((e) => e.field && !(r.type === "llm_judgment"));
  const quote = r.type === "llm_judgment" && r.evidence[0]?.quote ? r.evidence[0] : null;
  const kids = r.children?.length ? `<div class="children">${r.children.map((c) => ruleHtml(c, data, false)).join("")}</div>` : "";
  const title = top ? human(r.rule_id) : (r.citation || ({ any_of: "Any one of these", all_of: "All of these", llm_judgment: "Model check", external: "External check" })[r.type] || "");
  const judge = r.type === "llm_judgment" ? `<div class="cite">Asked ${esc(r.detail.model)}: ${esc(r.detail.question)}</div>` : "";
  return `<div class="rule">
    <div class="rule-head"><span class="icon ${r.outcome}" aria-label="${OUTCOME_WORD[r.outcome]}">${OUTCOME_ICON[r.outcome]}</span>
      <div>${title ? `<div class="rule-title">${esc(title)}` : `<div>`}${top && r.outcome !== "pass" && r.on_fail ? `<span class="tag">${r.outcome === "unverifiable" ? "to a reviewer" : r.on_fail === "needs_info" ? "ask the office" : "to a reviewer"}</span>` : ""}</div>
      <div class="rule-msg">${esc(r.message)}</div>
      ${top && (src || r.citation) ? `<div class="cite">${src ? `<a href="${esc(src.url)}" target="_blank" rel="noopener">${esc(src.title)}</a>` : ""}${src && r.citation ? ". " : ""}${esc(r.citation || "")}</div>` : ""}
      ${judge}
      ${evs.length ? `<div class="evidence">${evs.map(evChip).join("")}</div>` : ""}
      ${quote ? `<blockquote class="quote" data-ev='${esc(JSON.stringify({ quote: quote.quote, page: quote.page }))}'>“${esc(quote.quote)}”<br><small>page ${quote.page ?? "?"} · click to find it on the fax</small></blockquote>` : ""}
      ${r.type === "external" && r.detail?.source ? `<div class="cite">Checked via ${esc(r.detail.source)}${r.detail.registry_name ? ` · registry name ${esc(r.detail.registry_name)}` : ""}${r.detail.rejects?.length ? ` · ${r.detail.rejects.map((x) => `AAA ${esc(x.code)}`).join(", ")}` : ""}</div>` : ""}
      ${kids ? (top ? `<details class="more"${r.outcome === "pass" ? "" : " open"}><summary>How this was decided</summary>${kids}</details>` : kids) : ""}
      </div></div></div>`;
}

function evChip(e) {
  const val = e.value === true ? "yes" : e.value === false ? "no" : e.value ?? (e.page ? "unreadable" : "not found");
  const payload = esc(JSON.stringify({ page: e.page, bbox: e.bbox, field: e.field }));
  return `<button class="ev" data-ev='${payload}' title="${e.page ? `Show on page ${e.page}` : "Not found in the packet"}">${esc(human(e.field))}: ${esc(val)}${e.confidence != null && e.page ? `<span class="c">${e.confidence.toFixed(2)} · p${e.page}</span>` : ""}</button>`;
}

function fieldsTable(data) {
  const f = data.decision.extraction.fields;
  const used = new Set(data.rule_fields);
  return `<table class="fields">${data.fields.map((spec) => {
    const x = f[spec.name] || { status: "missing" };
    const v = x.value === true ? "yes" : x.value === false ? "no" : x.value;
    const missing = x.status !== "found" || v == null;
    const conf = x.confidence ?? 0;
    return `<tr><td>${esc(human(spec.name))}${used.has(spec.name) ? "" : ' <span class="muted">(not used)</span>'}</td>
      <td><span class="val${missing ? " missing" : ""}" data-field="${esc(spec.name)}" data-type="${spec.type}" tabindex="0">${missing ? (x.status === "illegible" ? "unreadable" : "not found") : esc(v)}</span>${x.edited_by ? `<span class="edited">edited</span>` : ""}
      ${!missing ? `<br><span class="conf${conf < data.decision.threshold ? " low" : ""}"><i style="width:${Math.round(conf * 100)}%"></i></span><span class="conf-n">${conf.toFixed(2)}${x.page ? ` · p${x.page}` : ""}</span>` : ""}</td></tr>`;
  }).join("")}</table>`;
}

function auditLine(a) {
  const t = new Date(a.at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const what = ({
    decision: () => `<b>Routed ${ROUTE_LABEL[a.route] || a.route}</b> by ${esc(a.pack)}`,
    field_edit: () => `<b>${esc(human(a.field))}</b> changed from ${esc(a.before)} to ${esc(a.after)}${a.route_before !== a.route_after ? `; route ${ROUTE_LABEL[a.route_before]} → ${ROUTE_LABEL[a.route_after]}` : ""}`,
    approve: () => `<b>Approved</b> ${ROUTE_LABEL[a.final_route]}`,
    override: () => `<b>Overridden</b> ${ROUTE_LABEL[a.system_route]} → ${ROUTE_LABEL[a.final_route]}${a.note ? `: ${esc(a.note)}` : ""}`,
    reopen: () => `<b>Reopened</b> for review`,
  }[a.event] || (() => esc(a.event)))();
  return `<li>${t} · ${what}</li>`;
}

async function review(body) {
  const id = state.current.decision.order_id;
  try {
    state.current = await api(`/orders/${id}/review`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    toast(body.action === "approve" ? "Approved" : "Overridden; saved as an eval case");
    renderFindings(state.current);
    refreshQueue(id);
  } catch (e) { toast(e.message); }
}

function editField(span) {
  const name = span.dataset.field;
  const current = span.classList.contains("missing") ? "" : span.textContent;
  const input = document.createElement("input");
  input.value = current;
  input.setAttribute("aria-label", `Correct ${human(name)}`);
  span.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const commit = async (save) => {
    if (done) return;
    done = true;
    if (!save || input.value === current) return renderFindings(state.current);
    try {
      const before = state.current.decision.route;
      state.current = await api(`/orders/${state.current.decision.order_id}/fields/${name}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ value: input.value }) });
      const after = state.current.decision.route;
      renderFax(state.current, after !== before);
      renderFindings(state.current);
      refreshQueue(state.current.decision.order_id);
      toast(after !== before ? `Rules re-run: now ${ROUTE_LABEL[after]}` : "Saved; rules re-run");
    } catch (e) { toast(e.message); renderFindings(state.current); }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(true); if (e.key === "Escape") commit(false); });
  input.addEventListener("blur", () => commit(true));
}

// --- highlights on the fax ------------------------------------------------------------------

const normalize = (s) => s.toLowerCase().replace(/\s+/g, " ").trim();

function quoteBoxes(data, quote, page) {
  const p = data.decision.extraction.pages.find((x) => x.number === page);
  if (!p) return [];
  const q = normalize(quote);
  return p.lines.filter((ln) => ln.text.length > 3 && q.includes(normalize(ln.text))).map((ln) => ln.bbox);
}

function drawMarks(data) {
  document.querySelectorAll(".mark").forEach((m) => m.remove());
  const marks = [];
  for (const top of data.decision.results) {
    walk(top, (r) => {
      if (r.outcome === "skipped") return;
      for (const e of r.evidence) {
        if (e.bbox && e.page) marks.push({ page: e.page, bbox: e.bbox, key: `${e.field}` });
        else if (e.quote && e.page) quoteBoxes(data, e.quote, e.page).forEach((b) => marks.push({ page: e.page, bbox: b, key: `q:${e.quote}` }));
      }
    });
  }
  const seen = new Set();
  for (const m of marks) {
    const id = `${m.page}:${m.bbox.join(",")}`;
    if (seen.has(id)) continue;
    seen.add(id);
    const sheet = $(`#page-${m.page}`);
    if (!sheet) continue;
    const div = document.createElement("div");
    div.className = "mark";
    div.dataset.key = m.key;
    const [x0, y0, x1, y1] = m.bbox;
    Object.assign(div.style, { left: `${x0 * 100 - 0.4}%`, top: `${y0 * 100 - 0.2}%`, width: `${(x1 - x0) * 100 + 0.8}%`, height: `${(y1 - y0) * 100 + 0.4}%` });
    sheet.append(div);
  }
}

function walk(r, fn) { fn(r); (r.children || []).forEach((c) => walk(c, fn)); }

function focusEvidence(ev, btn) {
  document.querySelectorAll(".mark.active, .ev.active").forEach((m) => m.classList.remove("active"));
  if (!ev.page) return toast("Not found anywhere in the packet");
  btn.classList.add("active");
  const key = ev.quote ? `q:${ev.quote}` : ev.field;
  const marks = [...document.querySelectorAll(`#page-${ev.page} .mark`)].filter((m) => m.dataset.key === key);
  marks.forEach((m) => { m.classList.remove("active"); void m.offsetWidth; m.classList.add("active"); });
  const target = marks[0] || $(`#page-${ev.page}`);
  target?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
}

// --- tiny markdown for the outreach letter ------------------------------------------------------

function md(src) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/_(.+?)_/g, "<i>$1</i>");
  const out = [];
  let list = null;
  for (const raw of src.split("\n")) {
    const line = raw.trimEnd();
    const li = line.match(/^\d+\.\s+(.*)/);
    if (li) { if (!list) { list = []; } list.push(inline(li[1])); continue; }
    if (list && /^\s{2,}\S/.test(raw)) { list[list.length - 1] += `<br>${inline(line.trim())}`; continue; }
    if (list) { out.push(`<ol>${list.map((x) => `<li>${x}</li>`).join("")}</ol>`); list = null; }
    if (line) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ol>${list.map((x) => `<li>${x}</li>`).join("")}</ol>`);
  return out.join("");
}

// --- packs ----------------------------------------------------------------------------------

async function showPacks(id) {
  const { packs } = await api("/packs");
  state.packs = packs;
  id = id || packs[0]?.pack_id;
  view.innerHTML = `<div class="packs">
    <aside class="pack-list" aria-label="Criteria packs">${packs.map((p) => `<button data-id="${esc(p.pack_id)}" aria-current="${p.pack_id === id}">
      <b>${esc(p.item.name)}</b><small>${esc(p.pack_id)} v${esc(p.version)} · ${p.rules} rules · ${p.fields} fields</small></button>`).join("")}</aside>
    <section class="editor"><div class="row"><h2 id="pack-title"></h2></div>
      <textarea id="yaml" spellcheck="false" aria-label="Pack YAML"></textarea>
      <div class="row"><button class="btn primary" id="check">Check pack</button>
        <button class="btn" id="try">Run on queued orders</button>
        <button class="btn" id="revert">Revert edits</button>
        <span class="muted">Edits here are a sandbox: they're checked and test-run, never saved.</span></div></section>
    <aside class="check" id="check-out" aria-live="polite"></aside></div>`;
  view.querySelectorAll(".pack-list button").forEach((b) => b.addEventListener("click", () => { location.hash = `#/packs/${b.dataset.id}`; }));
  if (!id) return;
  const pack = await api(`/packs/${id}`);
  $("#pack-title").textContent = pack.pack.item.name;
  $("#yaml").value = pack.yaml;
  renderCheck({ ok: true, problems: [], pack: pack.pack });
  $("#check").addEventListener("click", checkPack);
  $("#revert").addEventListener("click", () => { $("#yaml").value = pack.yaml; checkPack(); });
  $("#try").addEventListener("click", tryPack);
  $("#yaml").addEventListener("keydown", (e) => {
    if (e.key === "Tab") { e.preventDefault(); const t = e.target; const s = t.selectionStart; t.setRangeText("  ", s, t.selectionEnd, "end"); }
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") checkPack();
  });
}

async function checkPack() {
  const res = await api("/packs/validate", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ yaml: $("#yaml").value }) });
  renderCheck(res);
}

function renderCheck(res) {
  const out = $("#check-out");
  if (!res.ok) {
    out.innerHTML = `<h3>This pack won't load</h3><ul class="problems">${res.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul><p class="muted">Fix these and check again. Nothing in the engine changes when a pack changes.</p>`;
    return;
  }
  const p = res.pack;
  out.innerHTML = `<p class="ok-line">Valid pack: ${p.rules.length} rules over ${p.extraction.fields.length} fields, ${p.sources.length} cited sources.</p>
    <h3>Rules, in evaluation order</h3><ul class="tree">${p.rules.map((r) => treeNode(r, p)).join("")}</ul>
    <h3 style="margin-top:14px">Routing</h3><p class="muted" style="font-size:.86rem">Auto-ready only when every field read clears <b>${p.routing.auto_ready_min_confidence}</b>; fields below <b>${p.routing.unverifiable_below}</b> make a rule unverifiable.${p.routing.never_auto.length ? " A model judging something not documented goes to a reviewer before the office is asked." : ""}</p>
    <div id="try-out"></div>`;
}

function treeNode(r, p) {
  const kids = r.options || r.rules;
  const label = {
    present: () => `<code>${r.field}</code> is present`,
    present_true: () => `<code>${r.field}</code> is yes`,
    pattern: () => `<code>${r.field}</code> matches format`,
    in: () => `<code>${r.field}</code> is one of ${r.values.join(", ")}`,
    date_order: () => `<code>${r.a}</code> ${r.relation.replace(/_/g, " ")} <code>${r.b}</code>`,
    date_window: () => `<code>${r.b}</code> within ${r.min_days ?? "…"}–${r.max_days ?? "…"} days of <code>${r.a}</code>`,
    numeric: () => `<code>${r.field}</code> ${r.op} ${r.value}`,
    numeric_range: () => `<code>${r.field}</code> between ${r.min} and ${r.max}`,
    any_of: () => `<span class="op">any of</span>`,
    all_of: () => `<span class="op">all of</span>`,
    llm_judgment: () => `model check, quote required: “${esc(r.question)}”`,
    external: () => `${r.check === "npi_registry" ? "NPI Registry" : "eligibility"} check${r.field ? ` on <code>${r.field}</code>` : ""}`,
  }[r.type]();
  const head = r.id ? `<b>${esc(human(r.id))}</b>${r.on_fail ? ` <span class="op">→ ${r.on_fail === "needs_info" ? "ask office" : "reviewer"}</span>` : ""}: ` : "";
  return `<li>${head}${label}${kids ? `<ul>${kids.map((k) => treeNode(k, p)).join("")}</ul>` : ""}</li>`;
}

async function tryPack() {
  const btn = $("#try");
  btn.disabled = true;
  btn.textContent = "Running…";
  try {
    const res = await api("/packs/try", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ yaml: $("#yaml").value }) });
    const total = Object.values(res.counts).reduce((a, b) => a + b, 0);
    $("#try-out").innerHTML = `<h3 style="margin-top:14px">Against ${total} queued orders</h3>
      <table class="grid"><tr><th>Route</th><th>Orders</th></tr>${ROUTE_ORDER.map((r) => `<tr><td>${ROUTE_LABEL[r]}</td><td>${res.counts[r]}</td></tr>`).join("")}</table>
      ${res.changed.length ? `<p class="muted" style="font-size:.86rem;margin-top:10px">${res.changed.length} order${res.changed.length === 1 ? "" : "s"} would change route:</p><ul class="diff-list">${res.changed.map((c) => `<li><a href="#/queue/${c.order_id}">${c.order_id}</a>: ${ROUTE_LABEL[c.before]} → ${ROUTE_LABEL[c.after]}</li>`).join("")}</ul>` : `<p class="muted" style="font-size:.86rem">No order changes route.</p>`}`;
  } catch (e) { $("#try-out").innerHTML = `<ul class="problems"><li>${esc(e.message)}</li></ul>`; }
  btn.disabled = false;
  btn.textContent = "Run on queued orders";
}

// --- results ---------------------------------------------------------------------------------

async function showResults() {
  let data;
  try { data = await api("/eval"); }
  catch { view.innerHTML = `<div class="results"><h2>No results yet</h2><p>Run <code>make eval</code> to score the pipeline against the synthetic ground truth.</p></div>`; return; }
  view.innerHTML = `<div class="results"><h1 style="font-size:1.6rem">How well it works</h1>
    <p>Scored against ${Object.values(data.suites).reduce((a, s) => a + s.orders, 0)} synthetic faxed orders with planted defects (${esc(data.date)}). Expected outcomes come from the generator, not the engine.</p>
    ${Object.entries(data.suites).map(([k, s]) => suiteHtml(k, s)).join("")}</div>`;
  for (const [k, s] of Object.entries(data.suites)) wireSweep(k, s);
}

function suiteHtml(k, s) {
  const conf = s.confusion;
  const routes = ["ready", "needs_info", "human_review"];
  return `<section class="suite" id="suite-${k}">
    <h2>${esc(s.pack_id)} <span class="muted" style="font-weight:400">v${esc(s.pack_version)} · ${s.orders} orders</span></h2>
    <p class="muted" style="margin:0">Extractor ${esc(s.models.extractor)}, judge ${esc(s.models.judge)}</p>
    <div class="kpis">
      <div><b>${pct(s.defect_recall)}</b><span>planted defects caught</span></div>
      <div><b>${s.unsafe_ready.length}</b><span>defective orders marked Ready</span></div>
      <div><b>${pct(s.field_accuracy, 1)}</b><span>fields read correctly</span></div>
      <div><b>${pct(s.routing_accuracy)}</b><span>routed as expected</span></div>
      <div><b>${s.latency_p50_s.toFixed(1)}s</b><span>median time per order</span></div>
    </div>
    <h3>Automation vs. error as the confidence bar moves</h3>
    <div class="sweep"><div><svg viewBox="0 0 560 230" role="img" aria-label="Auto-routed rate and error rate by threshold"></svg>
      <div class="legend"><span><i style="background:#2a78d6"></i>Auto-routed Ready, % of orders</span><span><i style="background:#eb6834"></i>Wrong among auto-routed</span></div></div>
      <div class="readout"><label for="thr-${k}">Auto-ready bar: <b id="thr-v-${k}"></b></label>
        <input type="range" id="thr-${k}" min="0" max="${s.sweep.length - 1}" step="1">
        <p id="thr-text-${k}"></p></div></div>
    <div class="two">
      <div><h3>Routing (rows: expected)</h3><table class="grid"><tr><th></th>${routes.map((r) => `<th>${ROUTE_LABEL[r]}</th>`).join("")}</tr>
        ${routes.map((w) => `<tr><td>${ROUTE_LABEL[w]}</td>${routes.map((g) => `<td class="${w === g ? "hit" : ""}">${conf[w][g]}</td>`).join("")}</tr>`).join("")}</table>
        <p class="muted" style="font-size:.82rem">Ready orders sent to a reviewer cost minutes; defective orders sent out as Ready cost denials. The bar is set for the second to be zero.</p></div>
      <div><h3>Per rule</h3><table class="grid"><tr><th>Rule</th><th>Precision</th><th>Recall</th></tr>
        ${Object.entries(s.rules).map(([id, r]) => `<tr><td>${esc(human(id))}</td><td>${pct(r.precision)}</td><td>${pct(r.recall)}</td></tr>`).join("")}</table></div>
    </div></section>`;
}

function wireSweep(k, s) {
  const svg = $(`#suite-${k} svg`);
  const W = 560, H = 230, L = 34, R = 10, T = 10, B = 28;
  const xs = s.sweep.map((p) => p.threshold);
  const x = (t) => L + ((t - xs[0]) / (xs[xs.length - 1] - xs[0])) * (W - L - R);
  const y = (v) => T + (1 - v) * (H - T - B);
  const path = (key) => s.sweep.map((p, i) => `${i ? "L" : "M"}${x(p.threshold).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
  const grid = [0, 0.25, 0.5, 0.75, 1].map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--rule)"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="10" fill="var(--muted)">${v * 100}%</text>`).join("");
  const ticks = xs.filter((_, i) => i % 3 === 0).map((t) => `<text x="${x(t)}" y="${H - 8}" text-anchor="middle" font-size="10" fill="var(--muted)">${t.toFixed(2)}</text>`).join("");
  svg.innerHTML = `${grid}${ticks}
    <path d="${path("auto_rate")}" fill="none" stroke="#2a78d6" stroke-width="2"/>
    <path d="${path("error_rate")}" fill="none" stroke="#eb6834" stroke-width="2"/>
    <line id="cursor-${k}" y1="${T}" y2="${H - B}" stroke="var(--ink)" stroke-dasharray="3 3"/>
    <circle id="dot-a-${k}" r="4.5" fill="#2a78d6" stroke="var(--surface)" stroke-width="2"/>
    <circle id="dot-e-${k}" r="4.5" fill="#eb6834" stroke="var(--surface)" stroke-width="2"/>`;
  const input = $(`#thr-${k}`);
  const def = s.sweep.findIndex((p) => Math.abs(p.threshold - s.threshold) < 1e-6);
  input.value = def >= 0 ? def : s.sweep.length - 1;
  const update = () => {
    const p = s.sweep[+input.value];
    $(`#thr-v-${k}`).textContent = p.threshold.toFixed(2);
    $(`#cursor-${k}`).setAttribute("x1", x(p.threshold));
    $(`#cursor-${k}`).setAttribute("x2", x(p.threshold));
    $(`#dot-a-${k}`).setAttribute("cx", x(p.threshold)); $(`#dot-a-${k}`).setAttribute("cy", y(p.auto_rate));
    $(`#dot-e-${k}`).setAttribute("cx", x(p.threshold)); $(`#dot-e-${k}`).setAttribute("cy", y(p.error_rate));
    $(`#thr-text-${k}`).innerHTML = `${p.auto} of ${s.orders} orders (${pct(p.auto_rate)}) go straight to Ready; <b>${p.wrong}</b> of those had a planted defect. ${pct(p.ready_recall)} of truly complete orders skip the reviewer.${Math.abs(p.threshold - s.threshold) < 1e-6 ? " This is the pack's setting." : ""}`;
  };
  input.addEventListener("input", update);
  update();
}

// --- upload ------------------------------------------------------------------------------------

$("#add-fax").addEventListener("click", async () => {
  const { packs } = await api("/packs");
  $("#upload-pack").innerHTML = packs.map((p) => `<option value="${esc(p.pack_id)}">${esc(p.item.name)} (${esc(p.payer)})</option>`).join("");
  $("#upload-error").hidden = true;
  $("#upload").showModal();
});

$("#upload-form").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "go") return;
  e.preventDefault();
  const form = new FormData(e.target);
  const btn = $("#upload-go");
  btn.disabled = true;
  btn.textContent = "Reading the fax…";
  try {
    const res = await api("/orders", { method: "POST", body: form });
    $("#upload").close();
    e.target.reset();
    location.hash = `#/queue/${res.order_id}`;
    if ($(".desk")) { await refreshQueue(res.order_id); await showOrder(res.order_id, true); }
  } catch (err) {
    $("#upload-error").textContent = err.message;
    $("#upload-error").hidden = false;
  }
  btn.disabled = false;
  btn.textContent = "Process fax";
});

go();
