"""Build a single self-contained HTML dashboard from data/*.json and open it in the browser.

No server, no build step: the JSON is embedded in the page, so the file works offline and can be moved anywhere.
Tables, not charts — the data here is items × attributes (course, title, due date, status), which a table shows
better than any chart would. Status is icon + word + colour, never colour alone.
"""
import json
import webbrowser
from datetime import datetime

from .fetch import OUT

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Brightspace</title>
<style>
:root {
  color-scheme: light;
  --surface: #fcfcfb; --card: #ffffff; --line: #e6e5e0;
  --text: #0b0b0b; --muted: #52514e;
  --good: #0ca30c; --warn: #fab219; --crit: #d03b3b; --accent: #2a78d6;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  color-scheme: dark;
  --surface: #1a1a19; --card: #232321; --line: #383835;
  --text: #ffffff; --muted: #c3c2b7; --accent: #3987e5;
} }
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface: #1a1a19; --card: #232321; --line: #383835;
  --text: #ffffff; --muted: #c3c2b7; --accent: #3987e5;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--text); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }
header { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 4px; }
h1 { font-size: 22px; margin: 0; letter-spacing: -0.01em; }
.sub { color: var(--muted); font-size: 13px; }
button.theme { margin-left: auto; background: none; border: 1px solid var(--line); color: var(--muted);
  border-radius: 8px; padding: 4px 10px; cursor: pointer; font-size: 13px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 20px 0 8px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
.tile .n { font-size: 30px; font-weight: 650; letter-spacing: -0.02em; }
.tile .l { color: var(--muted); font-size: 13px; }
nav { display: flex; gap: 4px; margin: 20px 0 12px; flex-wrap: wrap; }
nav button { background: none; border: 1px solid transparent; border-radius: 8px; padding: 6px 12px;
  color: var(--muted); cursor: pointer; font-size: 14px; }
nav button[aria-selected="true"] { background: var(--card); border-color: var(--line); color: var(--text); font-weight: 600; }
.controls { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
input, select { background: var(--card); color: var(--text); border: 1px solid var(--line);
  border-radius: 8px; padding: 7px 10px; font-size: 14px; }
input { flex: 1 1 260px; }
table { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
th, td { text-align: start; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); font-weight: 600; cursor: pointer; white-space: nowrap; }
tr:last-child td { border-bottom: none; }
td.course { color: var(--muted); font-size: 13px; white-space: nowrap; }
td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.badge { display: inline-flex; align-items: center; gap: 5px; font-size: 12.5px; white-space: nowrap; }
.badge::before { content: attr(data-icon); }
.badge.ok { color: var(--good); } .badge.no { color: var(--crit); } .badge.due { color: var(--warn); }
.empty { color: var(--muted); padding: 24px 12px; }
a { color: var(--accent); }
footer { color: var(--muted); font-size: 12.5px; margin-top: 28px; }
@media (max-width: 640px) { td.course { white-space: normal; } h1 { font-size: 19px; } }
</style></head><body><div class="wrap">
<header>
  <h1>Brightspace</h1><span class="sub" id="stamp"></span>
  <button class="theme" onclick="toggleTheme()">theme</button>
</header>
<div class="tiles" id="tiles"></div>
<nav id="tabs"></nav>
<div class="controls">
  <input id="q" placeholder="Search…" oninput="render()">
  <select id="course" onchange="render()"></select>
</div>
<div id="view"></div>
<footer>Read-only snapshot of your own Brightspace account. Re-run <code>uv run d2l dump</code> to refresh, then
<code>uv run d2l ui</code>.</footer>
</div>
<script>
const DATA = __DATA__;
const courseName = Object.fromEntries(DATA.courses.map(c => [c.id, c.name]));
let tab = "assignments", sortKey = null, sortDir = 1;

const TABS = {
  assignments:   { label: "Assignments", cols: [["name","Assignment"],["due","Due"],["status","Status"],["score","Score"]] },
  announcements: { label: "Announcements", cols: [["title","Title"],["date","Posted"]] },
  grades:        { label: "Grades", cols: [["item","Item"],["grade","Grade"]] },
  courses:       { label: "Courses", cols: [["name","Course"],["code","Code"],["active","Active"]] },
};

function tiles() {
  const open = DATA.assignments.filter(a => !a.submitted).length;
  const graded = DATA.grades.filter(g => g.grade && !g.grade.startsWith("-")).length;
  return [[DATA.courses.filter(c => c.active).length, "active courses"],
          [open, "unsubmitted"],
          [DATA.announcements.length, "announcements"],
          [graded, "graded items"]];
}

function rows() {
  const q = document.getElementById("q").value.toLowerCase();
  const ou = document.getElementById("course").value;
  let rs = DATA[tab].map(r => ({ ...r, _course: courseName[r.course_id ?? r.id] || "" }));
  if (ou !== "all") rs = rs.filter(r => String(r.course_id ?? r.id) === ou);
  if (q) rs = rs.filter(r => JSON.stringify(r).toLowerCase().includes(q));
  if (sortKey) rs.sort((a, b) => String(a[sortKey] ?? "").localeCompare(String(b[sortKey] ?? "")) * sortDir);
  return rs;
}

function cell(row, key) {
  const v = row[key];
  if (key === "status" || key === "active") {
    if (tab === "courses") return v ? '<span class="badge ok" data-icon="●">active</span>'
                                    : '<span class="badge" data-icon="○" style="color:var(--muted)">archived</span>';
    return row.submitted ? `<span class="badge ok" data-icon="✓">${v || "submitted"}</span>`
                         : `<span class="badge no" data-icon="✕">${v || "not submitted"}</span>`;
  }
  if (key === "due" && v) return `<span class="badge due" data-icon="◔">${v}</span>`;
  return v == null || v === "" ? '<span style="color:var(--muted)">—</span>' : String(v);
}

function render() {
  document.getElementById("tiles").innerHTML = tiles()
    .map(([n, l]) => `<div class="tile"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
  document.getElementById("tabs").innerHTML = Object.entries(TABS)
    .map(([k, t]) => `<button role="tab" aria-selected="${k === tab}" onclick="setTab('${k}')">${t.label} <span style="opacity:.6">${DATA[k].length}</span></button>`).join("");
  const cols = TABS[tab].cols, rs = rows();
  document.getElementById("view").innerHTML = rs.length ? `<table><thead><tr>
      ${tab !== "courses" ? "<th>Course</th>" : ""}
      ${cols.map(([k, l]) => `<th onclick="sortBy('${k}')">${l}${sortKey === k ? (sortDir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}
    </tr></thead><tbody>${rs.map(r => `<tr>
      ${tab !== "courses" ? `<td class="course">${r._course}</td>` : ""}
      ${cols.map(([k]) => `<td class="${k === "grade" || k === "score" ? "num" : ""}">${cell(r, k)}</td>`).join("")}
    </tr>`).join("")}</tbody></table>` : '<p class="empty">Nothing here yet.</p>';
}

function setTab(k) { tab = k; sortKey = null; render(); }
function sortBy(k) { sortDir = sortKey === k ? -sortDir : 1; sortKey = k; render(); }
function toggleTheme() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  document.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
}
document.getElementById("course").innerHTML = '<option value="all">All courses</option>' +
  DATA.courses.filter(c => c.active).map(c => `<option value="${c.id}">${c.name}</option>`).join("");
document.getElementById("stamp").textContent = DATA.fetched_at;
render();
</script></body></html>"""


def build(open_browser: bool = True) -> str:
    data = {"fetched_at": "fetched " + datetime.now().strftime("%d %b %Y, %H:%M")}
    for key in ("courses", "announcements", "assignments", "grades"):
        path = OUT / f"{key}.json"
        if not path.exists():
            raise SystemExit(f"{path} missing — run `d2l dump` first")
        data[key] = json.loads(path.read_text())
    html = PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    out = OUT.parent / "dashboard.html"
    out.write_text(html)
    print(f"wrote {out} ({len(html) // 1024} KB)")
    if open_browser:
        webbrowser.open(f"file://{out}")
    return str(out)
