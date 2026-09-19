# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build a single self-contained HTML dashboard from data/*.json and open it in the browser.

No server, no build step: the JSON is embedded in the page, so the file works offline and can be moved anywhere.
Reads data/d2l.db. Opens on upcoming deadlines and a card per course; tabs for what's new, announcements (click to
expand the full text), grades by category, assignments and course files. Status is
icon + word + colour, never colour alone. Every scraped string is escaped before it touches the DOM.
"""
import html
import json
import os
import webbrowser

from . import query, semestra, store
from .session import ROOT

SOURCE_URL = "https://github.com/boss2236/d2l-brightspace"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Brightspace</title>
<style>
:root {
  color-scheme: light;
  --surface: #f7f7f5; --card: #ffffff; --line: #e6e5e0; --soft: #f1f0ec;
  --text: #111110; --muted: #62615d;
  --good: #1a7f37; --warn: #9a6700; --crit: #c93c37; --accent: #2a6fd6; --accent-soft: #e8f0fc;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  color-scheme: dark;
  --surface: #161615; --card: #1f1f1e; --line: #333331; --soft: #2a2a28;
  --text: #f3f3f1; --muted: #a9a8a1;
  --good: #4ac26b; --warn: #d4a72c; --crit: #f47067; --accent: #5b9cf0; --accent-soft: #1d2b40;
} }
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface: #161615; --card: #1f1f1e; --line: #333331; --soft: #2a2a28;
  --text: #f3f3f1; --muted: #a9a8a1;
  --good: #4ac26b; --warn: #d4a72c; --crit: #f47067; --accent: #5b9cf0; --accent-soft: #1d2b40;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--text); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 28px 16px 64px; }
header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
h1 { font-size: 24px; margin: 0; letter-spacing: -0.015em; }
.sub { color: var(--muted); font-size: 13.5px; }
button.theme { margin-left: auto; background: var(--card); border: 1px solid var(--line); color: var(--muted);
  border-radius: 8px; padding: 5px 11px; cursor: pointer; font-size: 13px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 22px 0 6px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
.tile .n { font-size: 28px; font-weight: 650; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.tile .l { color: var(--muted); font-size: 13px; }
nav { display: flex; gap: 4px; margin: 22px 0 12px; flex-wrap: wrap; border-bottom: 1px solid var(--line); }
nav button { background: none; border: none; border-bottom: 2px solid transparent; padding: 8px 12px; margin-bottom: -1px;
  color: var(--muted); cursor: pointer; font-size: 14px; }
nav button[aria-selected="true"] { border-bottom-color: var(--accent); color: var(--text); font-weight: 600; }
nav .count { opacity: .6; font-weight: 400; margin-left: 3px; }
.controls { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
input, select { background: var(--card); color: var(--text); border: 1px solid var(--line);
  border-radius: 8px; padding: 7px 10px; font-size: 14px; min-width: 0; }
input { flex: 1 1 240px; } select { flex: 1 1 220px; max-width: 360px; }
.chip { display: inline-block; font-size: 12px; font-weight: 600; letter-spacing: .02em; padding: 1px 7px;
  border-radius: 6px; background: var(--accent-soft); color: var(--accent); white-space: nowrap; }
.muted { color: var(--muted); }
.small { font-size: 13px; }

/* overview */
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.card { min-width: 0; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.card h3 { margin: 0; font-size: 16px; line-height: 1.3; }
.card .top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card .latest { background: var(--soft); border-radius: 8px; padding: 8px 10px; font-size: 13.5px; }
.card .latest b { display: block; font-weight: 550; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card .stats { display: flex; gap: 4px 12px; flex-wrap: wrap; font-size: 13px; color: var(--muted); }
.card .stats b { color: var(--text); font-variant-numeric: tabular-nums; }
.card a.open { margin-top: auto; font-size: 13px; }

/* lists */
.list { background: var(--card); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.row { display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; padding: 11px 14px; border-bottom: 1px solid var(--line); align-items: baseline; }
.row:last-child { border-bottom: none; }
.row .title { font-weight: 500; overflow-wrap: anywhere; }
.row .meta { grid-column: 1 / -1; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; color: var(--muted); }
.row .right { font-variant-numeric: tabular-nums; white-space: nowrap; text-align: end; }
.group { margin-bottom: 16px; }
.group h4 { margin: 0 0 8px; font-size: 14px; display: flex; gap: 8px; align-items: center; }
.bar { height: 6px; width: 80px; background: var(--soft); border-radius: 3px; overflow: hidden; display: inline-block; vertical-align: middle; margin-left: 8px; }
.bar i { display: block; height: 100%; background: var(--accent); }
.badge { display: inline-flex; align-items: center; gap: 4px; font-size: 12.5px; font-weight: 550; white-space: nowrap; }
.badge.ok { color: var(--good); } .badge.no { color: var(--crit); } .badge.due { color: var(--warn); } .badge.new { color: var(--accent); }
.empty { color: var(--muted); padding: 28px 16px; text-align: center; background: var(--card); border: 1px dashed var(--line); border-radius: 12px; }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
footer { color: var(--muted); font-size: 12.5px; margin-top: 32px; }
code { font-size: 12px; background: var(--soft); padding: 1px 5px; border-radius: 4px; }
.strip { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 4px; margin: 0 0 16px; }
.dl { flex: 0 0 240px; background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--warn); border-radius: 10px; padding: 10px 12px; font-size: 13.5px; }
.dl.urgent { border-left-color: var(--crit); }
.dl b { display: block; font-weight: 550; margin: 4px 0 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
h2.section { font-size: 14px; color: var(--muted); font-weight: 600; margin: 0 0 8px; text-transform: uppercase; letter-spacing: .04em; }
details.row { display: block; }
details.row summary { list-style: none; cursor: pointer; display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; align-items: baseline; }
details.row summary::-webkit-details-marker { display: none; }
details.row .body { white-space: pre-wrap; font-size: 14px; margin-top: 10px; padding: 10px 12px; background: var(--soft); border-radius: 8px; overflow-wrap: anywhere; }
.type { font-size: 11px; font-weight: 650; text-transform: uppercase; letter-spacing: .04em; padding: 1px 6px; border-radius: 5px; background: var(--soft); color: var(--muted); }
.cat { font-size: 12px; color: var(--muted); font-weight: 600; margin: 10px 0 4px 2px; text-transform: uppercase; letter-spacing: .04em; }
.panel { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 18px; margin-bottom: 14px; }
.panel h3 { margin: 0 0 4px; font-size: 16px; }
.panel h4 { margin: 0 0 6px; font-size: 14px; }
.panel p { margin: 8px 0; }
.panel .list { margin-top: 10px; }
.phead { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.pill { font-size: 12.5px; font-weight: 600; padding: 3px 10px; border-radius: 999px; background: var(--soft); color: var(--muted); white-space: nowrap; }
.pill.on { color: var(--good); } .pill.starting { color: var(--warn); } .pill.error { color: var(--crit); }
.btn { background: var(--card); color: var(--text); border: 1px solid var(--line); border-radius: 8px; padding: 7px 14px;
  font-size: 14px; cursor: pointer; text-decoration: none; display: inline-block; }
.btn:hover { border-color: var(--accent); text-decoration: none; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.small { padding: 2px 8px; font-size: 12.5px; }
.row .right .btn.small { margin-left: 8px; }
.btn:disabled { opacity: .6; cursor: default; }
.urlbox { display: flex; gap: 8px; margin: 6px 0 4px; }
.urlbox input { flex: 1; font: 13px ui-monospace, Menlo, monospace; min-width: 0; }
.steps { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; margin: 12px 0 4px; }
.steps > div { background: var(--soft); border-radius: 10px; padding: 12px 14px; }
.steps ol { margin: 0; padding-left: 18px; font-size: 14px; } .steps li { margin-bottom: 5px; }
.log { background: var(--soft); border-radius: 8px; padding: 10px; font-size: 12px; max-height: 180px; overflow: auto; white-space: pre-wrap; }
table.kv { border-collapse: collapse; width: 100%; font-size: 14px; margin-top: 8px; }
table.kv td { padding: 7px 10px 7px 0; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }
table.kv td:first-child { color: var(--muted); white-space: nowrap; width: 1%; }
.modes { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 8px; margin: 12px 0; }
.mode { text-align: start; background: var(--card); color: var(--text); border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 12px; cursor: pointer; display: flex; flex-direction: column; gap: 3px; font: inherit; }
.mode b { font-size: 14px; } .mode span { font-size: 12.5px; color: var(--muted); }
.mode.sel { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
.modeform { background: var(--soft); border-radius: 10px; padding: 4px 14px; margin-bottom: 10px; }
.form { display: grid; gap: 10px; margin: 8px 0; }
.form label { display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
.form label.check { display: flex; gap: 8px; align-items: center; color: var(--text); }
.form label.check input { flex: 0 0 auto; width: auto; margin: 0; }
.form input:not([type=checkbox]) { width: 100%; font: 13.5px ui-monospace, Menlo, monospace; }
ol.setup { padding-left: 18px; } ol.setup li { margin-bottom: 4px; }
table.rubric th { text-align: start; font-size: 12px; color: var(--muted); padding: 4px 10px 4px 0; }
.health.ok { color: var(--good); } .health.bad { color: var(--crit); }
.note { border-left: 3px solid var(--warn); background: var(--card); padding: 10px 14px; border-radius: 0 8px 8px 0; font-size: 14px; }
@media (max-width: 640px) { h1 { font-size: 20px; } .cards { grid-template-columns: minmax(0, 1fr); } select { max-width: none; } }
</style></head><body><div class="wrap">
<header>
  <h1 id="term">Brightspace</h1><span class="sub" id="stamp"></span>
  <button class="theme" onclick="toggleTheme()" aria-label="Toggle light/dark">◐ theme</button>
</header>
<div class="tiles" id="tiles"></div>
<nav id="tabs" role="tablist"></nav>
<div class="controls" id="controls">
  <input id="q" placeholder="Search…" oninput="render()">
  <select id="course" onchange="render()"></select>
</div>
<div id="view"></div>
<footer>Read-only snapshot of your own Brightspace account, this term only. Refreshed by <code>uv run d2l sync</code>
(or the timer); rebuild this page with <code>uv run d2l ui</code>. Commands: <code>docs/commands.html</code> ·
AI setup: <code>docs/connect-ai.html</code>.<br>
<a href="__SOURCE__" target="_blank" rel="noopener">Source code</a> · licensed under the
<a href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noopener">GNU AGPL v3.0 or later</a>.</footer>
</div>
<script>
const DATA = __DATA__;
const LIVE = __LIVE__;
const NOW = new Date();
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const byId = Object.fromEntries(DATA.courses.map(c => [c.id, c]));
const chip = id => { const c = byId[id]; return c ? `<span class="chip" title="${esc(c.name)}">${esc(c.code)}${c.section ? " · " + esc(c.section) : ""}</span>` : ""; };
const d = iso => iso ? new Date(iso) : null;
const days = x => (x - NOW) / 864e5;
function ago(x) {
  const n = Math.floor(-days(x));
  return n <= 0 ? "today" : n === 1 ? "yesterday" : n < 7 ? `${n} days ago` : x.toLocaleDateString(undefined, {month: "short", day: "numeric"});
}
function until(h) { return h < 0 ? "passed" : h < 1 ? "within the hour" : h < 48 ? `in ${Math.round(h)} h` : `in ${Math.round(h / 24)} days`; }
const isNew = x => x && days(x) >= -7;

function termName() {
  const s = DATA.courses.map(c => d(c.start)).filter(Boolean)[0];
  if (!s) return "Brightspace";
  const m = s.getMonth();
  return (m >= 7 ? "Fall" : m >= 4 ? "Summer" : "Spring") + " " + s.getFullYear();
}

const TABS = {overview: "Overview", new: "What's new", announcements: "Announcements", grades: "Grades",
              assignments: "Assignments", files: "Files", connect: "Connect AI"};
const counts = {overview: DATA.courses.length, new: DATA.events.length, announcements: DATA.announcements.length,
                grades: DATA.grades.length, assignments: DATA.assignments.length,
                files: DATA.files.filter(f => f.kind === "File").length};
let tab = "overview";
try { const t = localStorage.getItem("d2l.tab"); if (t in TABS) tab = t; } catch {}
if (location.hash.slice(1) in TABS) tab = location.hash.slice(1);

function filtered(rows) {
  const q = document.getElementById("q").value.toLowerCase();
  const ou = document.getElementById("course").value;
  return rows.filter(r => (ou === "all" || String(r.course_id ?? r.id) === ou)
    && (!q || JSON.stringify(r).toLowerCase().includes(q)));
}

function tiles() {
  const soon = DATA.deadlines.filter(x => x.in_hours >= 0 && x.in_hours <= 14 * 24).length;
  const fresh = DATA.events.filter(e => isNew(d(e.ts))).length;
  const graded = DATA.grades.filter(g => g.graded).length;
  const subjects = new Set(DATA.courses.map(c => c.code)).size;
  return [[subjects, `subjects · ${DATA.courses.length} course pages`], [soon, "dated in the next 14 days"],
          [fresh, "changes this week"], [graded, `graded of ${DATA.grades.length} grade items`]];
}

function strip() {
  const up = filtered(DATA.deadlines).filter(x => x.in_hours >= 0).slice(0, 8);
  if (!up.length) return "";
  return `<h2 class="section">Coming up</h2><div class="strip">${up.map(x => `<div class="dl ${x.in_hours < 48 ? "urgent" : ""}">
      ${chip(x.course_id)} <span class="muted small">${esc(x.kind)}</span><b title="${esc(x.title)}">${esc(x.title)}</b>
      <span class="small">${esc(x.when)} · <span class="muted">${until(x.in_hours)}</span></span></div>`).join("")}</div>`;
}

function overview() {
  return strip() + `<div class="cards">${filtered(DATA.courses).map(c => {
    const a = DATA.announcements.filter(x => x.course_id === c.id);
    const latest = a[0];
    const k = c.counts;
    return `<div class="card">
      <div class="top">${chip(c.id)}${a.some(x => isNew(d(x.date))) ? '<span class="badge new">● new</span>' : ""}</div>
      <h3>${esc(c.name)}</h3>
      ${latest ? `<div class="latest"><b>${esc(latest.title)}</b><span class="muted">${ago(d(latest.date))}</span></div>`
               : '<div class="latest muted">No announcements yet</div>'}
      <div class="stats"><span><b>${k.announcements}</b> announcements</span><span><b>${k.graded}</b>/${k.grade_items} graded</span>
        <span><b>${k.files}</b> files</span><span><b>${k.quizzes}</b> quizzes</span></div>
      <a class="open" href="${esc(c.url)}" target="_blank" rel="noopener">Open in Brightspace ↗</a>
    </div>`;
  }).join("")}</div>`;
}

const ICON = {new_announcement: "📢", new_grade: "🎯", grade_changed: "🎯", new_assignment: "📝", due_changed: "📝",
              new_quiz: "⏱", new_files: "📄", due_soon: "⏰", session_expired: "🔑", new_feedback: "💬", new_course: "🎓", course_archived: "🗄"};
function whatsNew() {
  const rs = filtered(DATA.events);
  if (!rs.length) return '<p class="empty">Nothing new yet. Changes show up here after the next sync notices them.</p>';
  return `<div class="list">${rs.map(e => `<div class="row">
      <span class="title">${ICON[e.kind] || "•"} ${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.summary)}</a>` : esc(e.summary)}</span>
      <span class="right small muted">${ago(d(e.ts))}</span>
      <span class="meta">${chip(e.course_id)}<span>${esc(e.when)}</span>${e.detail ? `<span>${esc(e.detail.slice(0, 140))}${e.detail.length > 140 ? "…" : ""}</span>` : ""}</span>
    </div>`).join("")}</div>`;
}

function announcements() {
  const rs = filtered(DATA.announcements);
  if (!rs.length) return '<p class="empty">No announcements match.</p>';
  return `<div class="list">${rs.map(a => `<details class="row"><summary>
      <span class="title">${esc(a.title)}</span><span class="right small muted">${ago(d(a.date))}</span>
      <span class="meta">${chip(a.course_id)}${isNew(d(a.date)) ? '<span class="badge new">● new</span>' : ""}<span>${esc(a.posted)}</span></span>
    </summary><div class="body">${esc(a.body || "(no text)")}</div></details>`).join("")}</div>`;
}

function grades() {
  const rs = filtered(DATA.grades);
  if (!rs.length) return '<p class="empty">No grade items yet.</p>';
  const groups = {};
  rs.forEach(g => (groups[g.course_id] ||= []).push(g));
  return Object.entries(groups).map(([id, gs]) => {
    const cats = {};
    gs.forEach(g => (cats[g.category || "Other"] ||= []).push(g));
    if (cats.Other) { const o = cats.Other; delete cats.Other; cats.Other = o; }     // named categories first
    const sem = (DATA.semestra || {})[id] || {};
    const semBtn = sem.payload && sem.payload.categories.length ? `<button class="btn small" style="margin-left:auto"
        title="${esc((sem.warnings || []).join("\\n") || "Paste into Semestra → Import")}"
        onclick="copyText(JSON.stringify(DATA.semestra['${id}'].payload, null, 2), this)">Copy for Semestra${sem.warnings.length ? " ⓘ" : ""}</button>` : "";
    return `<div class="group"><h4>${chip(+id)} ${esc((byId[id] || {}).name)}
        <span class="muted small">${gs.filter(g => g.graded).length} of ${gs.length} graded</span>${semBtn}</h4>
      <div class="list">${Object.entries(cats).map(([cat, items]) => `<div class="cat" style="padding:0 14px">${esc(cat)}</div>` + items.map(g => {
        const pct = g.points && g.out_of ? parseFloat(g.points) / g.out_of * 100 : null;
        return `<div class="row"><span class="title">${esc(g.item)}${g.weight ? ` <span class="muted small">· weight ${+g.weight.toFixed(2)}</span>` : ""}</span>
          <span class="right">${g.graded ? `${esc(g.grade)}${pct != null ? `<span class="bar"><i style="width:${Math.min(100, pct)}%"></i></span>` : ""}`
                                    : '<span class="muted small">not graded yet</span>'}</span>
          ${g.comments ? `<span class="meta">${esc(g.comments)}</span>` : ""}</div>`;
      }).join("")).join("")}</div></div>`;
  }).join("");
}

function assignments() {
  const rs = filtered(DATA.assignments);
  if (!rs.length) return '<p class="empty">No assignment folders posted yet in this term’s courses.</p>';
  return `<div class="list">${rs.map(a => {
    const h = a.due_utc ? days(d(a.due_utc)) * 24 : null;
    const status = a.submitted ? `<span class="badge ok">✓ ${esc(a.status)}</span>`
      : h != null && h < 0 ? `<span class="badge no">✕ overdue</span>`
      : h != null ? `<span class="badge due">◔ due ${until(h)}</span>`
      : `<span class="badge no">✕ ${esc(a.status)}</span>`;
    const fb = feedbackHtml(a);
    const head = `<span class="title">${esc(a.name)}</span>
      <span class="right">${a.score ? esc(a.score) : '<span class="muted">—</span>'}</span>
      <span class="meta">${chip(a.course_id)}${status}${a.due ? `<span>due ${esc(a.due)}</span>` : ""}
        ${fb ? '<span class="badge new">💬 feedback</span>' : ""}</span>`;
    return fb ? `<details class="row"><summary>${head}</summary>${fb}</details>` : `<div class="row">${head}</div>`;
  }).join("")}</div>`;
}
function feedbackHtml(a) {
  // instructor feedback, rubric breakdown and feedback files; empty when nothing has been released
  const rub = a.rubric || [], files = a.feedback_files || [];
  if (!a.feedback && !rub.length && !files.length) return "";
  const total = rub.reduce((t, r) => t + (r.score || 0), 0), max = rub.reduce((t, r) => t + (r.out_of || 0), 0);
  return `<div class="body">
    ${a.submitted_at ? `<div class="muted small">Submitted ${esc(a.submitted_at)}</div>` : ""}
    ${a.feedback ? `<p style="margin:6px 0 10px">${esc(a.feedback)}</p>` : ""}
    ${rub.length ? `<table class="kv rubric"><tr><th>Criterion</th><th>Level</th><th>Score</th></tr>
      ${rub.map(r => `<tr><td>${esc(r.criterion)}${r.feedback ? `<div class="muted small">${esc(r.feedback)}</div>` : ""}</td>
        <td>${esc(r.level || "—")}</td><td class="num">${r.score ?? "—"}${r.out_of ? " / " + r.out_of : ""}</td></tr>`).join("")}
      ${max ? `<tr><td><b>Total</b></td><td></td><td class="num"><b>${total} / ${max}</b></td></tr>` : ""}</table>` : ""}
    ${files.length ? `<div class="small" style="margin-top:8px">Feedback files: ${files.map(esc).join(", ")}
      <span class="muted">(open them in Brightspace)</span></div>` : ""}
  </div>`;
}

function fileLinks(f) {
  // live app: the app serves the stored copy (fetching it first if needed); saved dashboard: the copy next to it
  if (LIVE) return {open: `/files/${f.id}`, save: `/files/${f.id}?download=1`};
  if (f.status === "ok" && f.file_path) { const u = encodeURI(f.file_path); return {open: u, save: u}; }
  return {open: f.url, save: null};
}
function fileRow(f) {
  if (f.kind !== "File") {
    const tool = /type=lti/.test(f.url || "");
    return `<div class="row"><span class="title"><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.title)}</a> <span class="muted small">↗</span></span>
      <span class="right"><span class="type">${tool ? "tool" : esc(f.kind === "Link" ? "link" : f.kind)}</span></span>
      ${tool ? '<span class="meta">opens through your Brightspace login, straight into the tool</span>' : ""}</div>`;
  }
  const L = fileLinks(f), stored = f.status === "ok";
  const note = f.status === "missing" ? "missing on Brightspace itself — the same file may be in another section"
    : f.status === "too_big" ? "too big to store — opens from Brightspace"
    : !stored ? (LIVE ? "not stored yet — opening it downloads it first" : "not stored yet — open the live app to download it") : "";
  return `<div class="row"><span class="title"><a href="${esc(L.open)}" target="_blank" rel="noopener">${esc(f.title)}</a></span>
      <span class="right"><span class="type">${esc(f.type || "file")}</span>
        ${L.save && f.status !== "missing" ? `<a class="btn small" href="${esc(L.save)}" download title="Download">↓ Download</a>` : ""}</span>
      ${note ? `<span class="meta">${esc(note)}</span>` : ""}</div>`;
}
function files() {
  const rs = filtered(DATA.files);
  if (!rs.length) return '<p class="empty">No course content matches.</p>';
  const groups = {};
  rs.forEach(f => (groups[f.course_id + "|" + f.module] ||= []).push(f));
  return `<div class="list">${Object.entries(groups).map(([k, fs]) => `<div class="cat" style="padding:4px 14px 0">${chip(+k.split("|")[0])} ${esc(fs[0].module)}</div>` +
    fs.map(fileRow).join("")).join("")}</div>`;
}

// --- Connect AI -------------------------------------------------------------------------------------------------
// Live only when served by `d2l app` (http://127.0.0.1:8766); the saved dashboard.html shows how to get there.
let ST = null;
async function api(path, body) {
  const init = body === undefined ? {} : {method: "POST", body: JSON.stringify(body),
                                         headers: {"X-D2L": "1", "Content-Type": "application/json"}};
  const r = await fetch(path, init);
  return r.json();
}
let editing = false, draft = null;           // draft: the public-link mode being looked at before saving
async function refresh(force) {
  try { ST = await api("/ui/status"); } catch { ST = null; }
  if (editing && !force) return;
  const el = document.getElementById("connect");
  if (el) el.innerHTML = connectHtml();
}
if (LIVE) setInterval(() => { if (tab === "connect") refresh(); }, 3000);

function copyText(text, btn) {
  navigator.clipboard.writeText(text).then(() => { btn.textContent = "Copied ✓"; setTimeout(() => btn.textContent = "Copy", 1500); });
}
function pickMode(m) { draft = m; editing = m === "cloudflare" || m === "custom"; refresh(true); }
async function savePublic(mode, btn) {
  const v = id => { const el = document.getElementById(id); return el ? (el.type === "checkbox" ? el.checked : el.value) : undefined; };
  const body = {mode, cf_host: v("cf_host"), cf_token: v("cf_token"), custom_url: v("custom_url"),
                direct: v("direct"), direct_port: v("direct_port")};
  if (btn) { btn.disabled = true; btn.textContent = "Connecting…"; }
  const r = await api("/ui/public", body);
  if (!r.ok) { alertBox(r.message); if (btn) { btn.disabled = false; btn.textContent = "Save and connect"; } return; }
  hideAlert(); editing = false; draft = null; refresh(true);
}
async function saveSemestra(btn) {
  btn.disabled = true;
  const r = await api("/ui/semestra", {url: document.getElementById("sem_url").value, key: document.getElementById("sem_key").value});
  editing = false; await refresh(true);
  if (!r.ok) alertBox(r.message); else hideAlert();
}
async function pushSemestra(btn) {
  btn.disabled = true; btn.textContent = "Sending…";
  await api("/ui/semestra/push", {}); refresh(true);
}
async function checkNow(btn) { btn.disabled = true; btn.textContent = "Checking…"; await api("/ui/public/check", {}); refresh(true); }
async function runJob(name) { await api("/ui/" + name, {}); refresh(); }
async function addClient(key, btn) {
  btn.disabled = true; btn.textContent = "Adding…";
  const r = await api("/ui/clients/" + key, {});
  if (!r.ok) alertBox(r.message);
  refresh();
}
function alertBox(msg) { const el = document.getElementById("cmsg"); if (el) { el.textContent = msg; el.hidden = false; } }
function hideAlert() { const el = document.getElementById("cmsg"); if (el) el.hidden = true; }
let showToken = false, rotateArmed = 0;
async function rotateToken(btn) {
  // two clicks within 5 s: a new token breaks existing cloud connectors, n8n credentials and ?token= links
  if (Date.now() - rotateArmed > 5000) { rotateArmed = Date.now(); btn.textContent = "Click again to confirm"; return; }
  rotateArmed = 0; btn.disabled = true;
  await api("/ui/token/rotate", {});
  await refresh(true);                      // redraw first, then show the message so the redraw doesn't hide it
  alertBox("New token created. Update it in n8n and your own apps, and copy the new connector link into claude.ai / ChatGPT.");
}

function connect() {
  if (LIVE && !ST) refresh();
  return `<div id="connect">${connectHtml()}</div>`;
}

function connectHtml() {
  if (!LIVE) return `<section class="panel"><h3>Connect your AI</h3>
    <p>This is the saved copy of the dashboard. Connections are managed in the live app:</p>
    <p><a class="btn primary" href="http://127.0.0.1:8766/#connect">Open the Brightspace app</a></p>
    <p class="muted small">Not opening? Start it with <code>uv run d2l app</code>, or install it to always run with
    <code>uv run d2l schedule install --serve</code>. Written guide: <code>docs/connect-ai.html</code>.</p></section>`;
  if (!ST) return '<p class="empty">Loading…</p>';
  const P = ST.public, mode = draft || P.mode, C = P.config;
  const label = {on: "Link is on", starting: "Starting…", off: "Off", error: "Problem"}[P.state];
  const job = j => j.state === "idle" ? "" : `<pre class="log">${esc(j.log.join("\\n") || "…")}</pre>`;
  const card = (m, title, desc) => `<button class="mode ${mode === m ? "sel" : ""}" onclick="pickMode('${m}')">
      <b>${title}${P.mode === m && m !== "off" ? ' <span class="badge ok">● active</span>' : ""}</b><span>${desc}</span></button>`;
  const typing = 'oninput="editing = true" onfocus="editing = true"';
  const H = P.health || {};
  const checked = H.checked ? new Date(H.checked * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";
  const forms = {
    off: P.mode === "off" ? '<p class="muted small">No public link. Cloud AIs can’t reach your data; apps on this computer still can.</p>'
       : `<p><button class="btn" onclick="savePublic('off', this)">Turn off public link</button></p>`,
    quick: `<p class="small">Cloudflare’s free quick tunnel: nothing to set up and no account. It’s slower, and gets a
       <b>new address every time it restarts</b>, so you re-copy the link after a reboot.</p>
       ${P.cloudflared ? "" : '<p class="small" style="color:var(--crit)">Needs cloudflared: <code>sudo pacman -S cloudflared</code></p>'}
       ${P.mode === "quick" ? "" : `<p><button class="btn primary" onclick="savePublic('quick', this)">Use quick link</button></p>`}`,
    cloudflare: `<ol class="small setup">
         <li>In <a href="https://one.dash.cloudflare.com/" target="_blank" rel="noopener">Cloudflare Zero Trust</a> → Networks → Tunnels → <b>Create a tunnel</b> → Cloudflared, give it a name.</li>
         <li>Copy the token from the install command shown (the long text after <code>--token</code>). You don’t need to run that command; this app runs the tunnel.</li>
         <li>Under <b>Public hostname</b>, add e.g. <code>brightspace</code> . <i>your domain</i>, Service <b>HTTP</b> → <code>localhost:8765</code>.</li>
         <li>Paste both here.</li></ol>
       <div class="form">
         <label>Public hostname<input id="cf_host" ${typing} placeholder="brightspace.example.com" value="${esc(C.cf_host)}"></label>
         <label>Tunnel token<input id="cf_token" ${typing} type="password" autocomplete="off"
           placeholder="${C.has_cf_token ? "saved — leave empty to keep it" : "eyJhIjoi…"}"></label>
       </div>
       ${P.cloudflared ? "" : '<p class="small" style="color:var(--crit)">Needs cloudflared: <code>sudo pacman -S cloudflared</code></p>'}
       <p><button class="btn primary" onclick="savePublic('cloudflare', this)">Save and connect</button></p>`,
    custom: `<p class="small">Use this if you already route a public address to this machine yourself. Point it at
       <code>http://localhost:8765</code>: your own cloudflared config, Caddy or nginx, <code>tailscale funnel 8765</code>,
       <code>ngrok http 8765</code>… With a public IP and router port forwarding, turn on the direct port and forward it to this laptop.</p>
       <div class="form">
         <label>Public address<input id="custom_url" ${typing} placeholder="https://brightspace.example.com  or  http://203.0.113.7:8767" value="${esc(C.custom_url)}"></label>
         <label class="check"><input id="direct" type="checkbox" ${typing} ${C.direct ? "checked" : ""}> Open a direct port on this computer (for IP / port forwarding / a proxy on another machine)</label>
         <label>Direct port<input id="direct_port" ${typing} type="number" min="1024" max="65535" value="${esc(C.direct_port)}" style="max-width:120px"></label>
       </div>
       <p class="small muted">Cloud AIs require <b>https</b> with a valid certificate. A plain <code>http://</code> IP works for n8n and your own apps, not for claude.ai or ChatGPT.</p>
       <p><button class="btn primary" onclick="savePublic('custom', this)">Save and connect</button></p>`,
  };
  return `
  <p id="cmsg" class="note" hidden></p>
  <section class="panel">
    <div class="phead"><div><h3>Cloud AI: claude.ai, ChatGPT</h3>
      <p class="muted small">Cloud AIs need a public link to reach the Brightspace data on this laptop. Choose how:</p></div>
      <span class="pill ${P.state}">● ${label}</span></div>
    <div class="modes">
      ${card("off", "Off", "No public link")}
      ${card("quick", "Quick link", "Free, no setup. Slower; address changes on restart.")}
      ${card("cloudflare", "My Cloudflare tunnel", "Your domain, fixed address, faster. Free account.")}
      ${card("custom", "My own URL / IP", "Own domain, proxy, public IP, ngrok, Tailscale…")}
    </div>
    <div class="modeform">${forms[mode]}</div>
    ${P.mode !== "off" && (!draft || draft === P.mode) ? `
      ${P.url ? `<label class="small muted">Your connector link (keep it secret: it works like a password)</label>
        <div class="urlbox"><input readonly value="${esc(P.url)}" onclick="this.select()"><button class="btn primary" onclick="copyText('${esc(P.url)}', this)">Copy</button></div>` : ""}
      <p class="small health ${H.ok === true ? "ok" : H.ok === false ? "bad" : ""}">
        ${H.ok === true ? "✓ Reachable from the internet: " + esc(H.detail)
          : H.ok === false ? "✕ Not reachable: " + esc(H.detail)
          : P.state === "on" ? "… " + esc(H.detail || "checking reachability") : ""}
        ${checked ? `<span class="muted"> · checked ${checked}</span>` : ""}
        ${P.state === "on" ? ' <button class="btn small" onclick="checkNow(this)">Check now</button>' : ""}</p>
      ${P.direct.on ? `<p class="small muted">Direct port ${P.direct.port} is open on all network interfaces.</p>` : ""}
      ${P.error ? `<p class="small" style="color:var(--warn)">${esc(P.error)}</p>` : ""}` : ""}
    <div class="steps">
      <div><h4>claude.ai</h4><ol>
        <li>Set up a link above and press <b>Copy</b>.</li>
        <li>Open <a href="https://claude.ai/settings/connectors" target="_blank" rel="noopener">claude.ai → Settings → Connectors</a>.</li>
        <li><b>Add custom connector</b>, name it <i>Brightspace</i>, paste the link, leave the advanced settings empty, then <b>Add</b>.</li>
        <li>In a chat, open the tools menu and switch <i>Brightspace</i> on. Ask “what’s due this week?”</li></ol></div>
      <div><h4>ChatGPT</h4><ol>
        <li>Set up a link above and press <b>Copy</b>.</li>
        <li>Open <a href="https://chatgpt.com/#settings/Connectors" target="_blank" rel="noopener">ChatGPT → Settings → Apps &amp; Connectors</a> → Advanced → turn on <b>Developer mode</b>.</li>
        <li><b>Create</b>: name <i>Brightspace</i>, paste the link, Authentication <b>No authentication</b>, tick “I trust this app”, then <b>Create</b>.</li>
        <li>In a chat, pick it from <b>+ → More → Developer mode</b>.</li></ol></div>
    </div>
    <p class="small muted">If an AI says it can’t reach Brightspace, check the line above: it says what’s wrong. With the quick link,
      the address changes after a restart, so copy the new link and replace it in the connector’s settings.</p>
  </section>

  <section class="panel">
    <h3>AI apps on this computer</h3>
    <p class="muted small">These start the connector themselves when needed. No link required.</p>
    <div class="list">${ST.clients.map(c => `<div class="row"><span class="title">${esc(c.name)}</span>
      <span class="right">${c.connected ? '<span class="badge ok">✓ Connected</span>'
        : c.installed ? `<button class="btn" onclick="addClient('${c.key}', this)">Add</button>` : '<span class="muted small">not installed</span>'}</span>
      ${c.connected ? `<span class="meta">${esc(c.how)}</span>` : ""}</div>`).join("")}</div>
  </section>

  <section class="panel">
    <div class="phead"><div><h3>Your data</h3><p class="muted small">Last synced ${esc(ST.last_sync || "never")} · syncs by itself at 08:00, 14:00 and 20:00.</p></div>
      ${ST.session_expired ? '<span class="pill error">● Login expired</span>' : '<span class="pill on">● Signed in</span>'}</div>
    <p><button class="btn primary" ${ST.sync.state === "running" ? "disabled" : ""} onclick="runJob('sync')">${ST.sync.state === "running" ? "Syncing…" : "Sync now"}</button>
       <button class="btn" ${ST.login.state === "running" ? "disabled" : ""} onclick="runJob('login')">${ST.login.state === "running" ? "Waiting for sign-in…" : "Log in again"}</button></p>
    <p class="small muted">“Log in again” opens a browser window. Sign in with your UDST account as usual and it closes by itself.
      Reload this page after a sync to see new data.</p>
    ${job(ST.sync)}${job(ST.login)}
  </section>

  <section class="panel">
    <div class="phead"><div><h3>Semestra</h3>
      <p class="muted small">Send your courses and grades to Semestra after every sync — every course you're enrolled
      in, even before grades are posted. In Semestra, create a connector key (Settings → Connectors), then paste its
      address and key here. Pressing <b>Sync now</b> in Semestra also works: this app checks every
      ${Math.round((ST.semestra.poll_seconds || 120) / 60)} min and then pulls fresh data. Only courses, grade
      structure, your grades and deadlines are sent: never announcements, files or your login.</p></div>
      ${ST.semestra.configured ? '<span class="pill on">● Connected</span>' : '<span class="pill off">● Not set up</span>'}</div>
    <div class="form">
      <label>Connector address<input id="sem_url" ${'oninput="editing = true" onfocus="editing = true"'} value="${esc(ST.semestra.url)}"
        placeholder="https://<project>.supabase.co/functions/v1/connector-ingest"></label>
      <label>Connector key<input id="sem_key" type="password" autocomplete="off" ${'oninput="editing = true" onfocus="editing = true"'}
        placeholder="${ST.semestra.has_key ? "saved — leave empty to keep it" : "sk_semestra_…"}"></label>
    </div>
    <p><button class="btn primary" onclick="saveSemestra(this)">Save</button>
       ${ST.semestra.configured ? '<button class="btn" onclick="pushSemestra(this)">Send now</button>' : ""}</p>
    ${ST.semestra.last ? `<p class="small ${ST.semestra.last.ok ? "health ok" : "health bad"}">${ST.semestra.last.ok ? "✓" : "✕"}
        ${esc(ST.semestra.last.message)} <span class="muted">· ${esc(new Date(ST.semestra.last.at).toLocaleString())}</span></p>` : ""}
    <p class="small muted">Prefer copying by hand? Each course on the <b>Grades</b> tab has a <b>Copy for Semestra</b> button for Semestra's Import page.</p>
  </section>

  <section class="panel">
    <h3>n8n and your own apps</h3>
    <table class="kv">
      <tr><td>REST API</td><td><code>${esc(ST.rest.base)}</code> e.g. <code>/deadlines?days=7</code></td></tr>
      <tr><td>MCP over HTTP</td><td><code>${esc(ST.rest.mcp)}</code></td></tr>
      <tr><td>Token</td><td><code>${showToken ? esc(ST.rest.token) : "••••••••••••"}</code>
        <button class="btn small" onclick="showToken = !showToken; refresh()">${showToken ? "Hide" : "Show"}</button>
        <button class="btn small" onclick="copyText('${esc(ST.rest.token)}', this)">Copy</button>
        <button class="btn small" onclick="rotateToken(this)">${Date.now() - rotateArmed < 5000 ? "Click again to confirm" : "New token"}</button>
        <span class="muted small">send as <code>Authorization: Bearer …</code></span></td></tr>
      <tr><td>Notifications</td><td>${ST.notify.channels.map(esc).join(", ") || "none"}${ST.notify.webhook ? "" : ' · <span class="muted">no webhook set (<code>D2L_WEBHOOK_URL</code> in .env) for n8n / WhatsApp</span>'}</td></tr>
    </table>
    <p class="small"><a href="/docs/connect-ai.html" target="_blank">Full guide</a> · <a href="/docs/commands.html" target="_blank">All commands</a></p>
  </section>`;
}

const VIEWS = {overview, new: whatsNew, announcements, grades, assignments, files, connect};

function render() {
  document.getElementById("tiles").innerHTML = tiles()
    .map(([n, l]) => `<div class="tile"><div class="n">${n}</div><div class="l">${esc(l)}</div></div>`).join("");
  document.getElementById("tabs").innerHTML = Object.entries(TABS)
    .map(([k, l]) => `<button role="tab" aria-selected="${k === tab}" onclick="setTab('${k}')">${l}${k in counts ? `<span class="count">${counts[k]}</span>` : ""}</button>`).join("");
  document.getElementById("controls").style.display = tab === "connect" ? "none" : "";
  document.getElementById("view").innerHTML = VIEWS[tab]();
}
function setTab(k) { tab = k; try { localStorage.setItem("d2l.tab", k); } catch {} history.replaceState(null, "", "#" + k); render(); }
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme")
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", cur === "dark" ? "light" : "dark");
}
document.getElementById("course").innerHTML = '<option value="all">All courses</option>' +
  DATA.courses.map(c => `<option value="${c.id}">${esc(c.code)}${c.section ? " · " + esc(c.section) : ""} — ${esc(c.name)}</option>`).join("");
document.getElementById("term").textContent = termName();
document.title = termName() + " · Brightspace";
document.getElementById("stamp").textContent = DATA.fetched_at;
render();
</script></body></html>"""


def render(live: bool = False) -> str:
    """The dashboard page. live=True when `d2l app` serves it, which turns on the Connect AI controls."""
    if not store.DB.exists():
        raise SystemExit("Nothing stored yet — run `uv run d2l sync` first.")
    with store.connect() as db:
        last = store.get_meta(db, "last_sync")
        data = {"fetched_at": f"synced {query.local(last)}" if last else "",
                "courses": query.courses(db),
                "announcements": query.announcements(db, limit=10_000, full=True),
                "grades": query.grades(db), "assignments": query.assignments(db),
                "deadlines": query.deadlines(db, 60),
                "events": query.events(db, limit=200), "files": query.files(db),
                "semestra": {str(x["course_id"]): {"payload": x["payload"], "warnings": x["warnings"]}
                             for x in semestra.all_payloads(db)}}
    # "</script>" inside a scraped title would end the script block early
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")   # no "</script>", "<!--" or "<script" possible
    # AGPL §13: people using this over a network must be offered its source; forks set D2L_SOURCE_URL to theirs
    source = html.escape(os.environ.get("D2L_SOURCE_URL") or SOURCE_URL, quote=True)
    return (PAGE.replace("__DATA__", blob).replace("__LIVE__", "true" if live else "false")
            .replace("__SOURCE__", source))


def build(open_browser: bool = True) -> str:
    html = render()
    out = ROOT / "dashboard.html"
    out.write_text(html)
    print(f"wrote {out} ({len(html) // 1024} KB)")
    if open_browser:
        webbrowser.open(f"file://{out}")
    return str(out)
