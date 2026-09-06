"""Self-contained HTML status dashboard for the HTTP API."""

from __future__ import annotations

STATUS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>brightcon-environ</title>
<style>
  :root {
    --bg: #0f1419;
    --panel: #1a222c;
    --border: #2a3540;
    --text: #e7ecf1;
    --muted: #8b9aab;
    --ok: #3dba74;
    --warn: #d4a017;
    --fail: #e05a5a;
    --run: #4a9eff;
    --queued: #9aa7b5;
    --row-hover: #222c38;
    --selected: #243044;
    --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    --sans: "Segoe UI", system-ui, sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font: 14px/1.45 var(--sans);
    color: var(--text);
    background: var(--bg);
    min-height: 100vh;
  }
  header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem 1.25rem;
    padding: 1rem 1.25rem;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
  }
  h1 {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 600;
    letter-spacing: 0.02em;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1rem;
    color: var(--muted);
    font-size: 0.85rem;
  }
  .meta strong { color: var(--text); font-weight: 500; }
  .pill {
    display: inline-block;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .pill.ok { background: color-mix(in srgb, var(--ok) 25%, transparent); color: var(--ok); }
  .pill.fail { background: color-mix(in srgb, var(--fail) 25%, transparent); color: var(--fail); }
  .pill.running { background: color-mix(in srgb, var(--run) 25%, transparent); color: var(--run); }
  .pill.queued { background: color-mix(in srgb, var(--queued) 25%, transparent); color: var(--queued); }
  .pill.succeeded { background: color-mix(in srgb, var(--ok) 25%, transparent); color: var(--ok); }
  .pill.failed { background: color-mix(in srgb, var(--fail) 25%, transparent); color: var(--fail); }
  .spacer { flex: 1; }
  .updated { color: var(--muted); font-size: 0.8rem; }
  .err {
    margin: 0.75rem 1.25rem 0;
    padding: 0.6rem 0.8rem;
    border: 1px solid var(--fail);
    border-radius: 4px;
    color: var(--fail);
    background: color-mix(in srgb, var(--fail) 12%, transparent);
    display: none;
  }
  .err.show { display: block; }
  main {
    display: grid;
    grid-template-columns: 1fr;
    gap: 1.25rem;
    padding: 1.25rem;
  }
  @media (min-width: 960px) {
    main { grid-template-columns: 1fr 1fr; }
    section.jobs { grid-column: 1 / -1; }
  }
  section {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  section h2 {
    margin: 0;
    padding: 0.7rem 1rem;
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
  }
  .empty {
    padding: 1.25rem 1rem;
    color: var(--muted);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }
  th, td {
    text-align: left;
    padding: 0.45rem 0.75rem;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  th {
    color: var(--muted);
    font-weight: 500;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  tr:last-child td { border-bottom: none; }
  tbody tr.selectable { cursor: pointer; }
  tbody tr.selectable:hover { background: var(--row-hover); }
  tbody tr.selected { background: var(--selected); }
  .mono { font-family: var(--mono); font-size: 0.8rem; }
  .flag {
    display: inline-block;
    min-width: 1.1rem;
    text-align: center;
  }
  .flag.yes { color: var(--ok); }
  .flag.no { color: var(--muted); }
  .muted { color: var(--muted); }
  .log-wrap {
    border-top: 1px solid var(--border);
    background: #0c1015;
  }
  .log-wrap h3 {
    margin: 0;
    padding: 0.55rem 1rem;
    font-size: 0.8rem;
    font-weight: 500;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
  }
  pre.log {
    margin: 0;
    padding: 0.85rem 1rem;
    max-height: 28rem;
    overflow: auto;
    font: 12px/1.4 var(--mono);
    white-space: pre-wrap;
    word-break: break-word;
    color: #c8d2dc;
  }
  footer {
    padding: 0.75rem 1.25rem 1.5rem;
    color: var(--muted);
    font-size: 0.8rem;
  }
  footer a { color: var(--run); text-decoration: none; }
  footer a:hover { text-decoration: underline; }
</style>
</head>
<body>
<header>
  <h1>brightcon-environ</h1>
  <span id="status-pill" class="pill fail">…</span>
  <div class="meta" id="health-meta"></div>
  <span class="spacer"></span>
  <span class="updated" id="updated">Loading…</span>
</header>
<div id="error" class="err" role="alert"></div>
<main>
  <section>
    <h2>Environments</h2>
    <div id="envs"></div>
  </section>
  <section class="jobs">
    <h2>Recent jobs</h2>
    <div id="jobs"></div>
    <div id="log-panel" class="log-wrap" hidden>
      <h3 id="log-title">Log</h3>
      <pre class="log" id="log"></pre>
    </div>
  </section>
</main>
<footer>
  Raw JSON:
  <a id="link-healthz" href="healthz">/healthz</a> ·
  <a id="link-jobs" href="jobs">/jobs</a> ·
  <a id="link-environments" href="environments">/environments</a>
  · OpenAPI <a id="link-docs" href="docs">/docs</a>
</footer>
<script>
(function () {
  const REFRESH_MS = 5000;
  let selectedJobId = null;
  let refreshGen = 0;
  let abort = null;
  let lastOkAt = null;

  // Resolve API paths under the reverse-proxy prefix. Absolute "/healthz"
  // would hit the site root when this page is served at e.g. /path/to/.
  function apiUrl(path) {
    let base = window.location.pathname;
    if (!base.endsWith("/")) base += "/";
    const u = new URL(String(path).replace(/^\\//, ""), window.location.origin + base);
    return u.pathname + u.search;
  }

  ["link-healthz", "link-jobs", "link-environments", "link-docs"].forEach(function (id) {
    const a = document.getElementById(id);
    if (a) a.setAttribute("href", apiUrl(a.getAttribute("href")));
  });

  function esc(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function shortSha(s) {
    if (!s) return "—";
    return s.length > 12 ? s.slice(0, 7) : s;
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso);
      return d.toLocaleString(undefined, {
        month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      });
    } catch (_) {
      return String(iso);
    }
  }

  function flag(yes) {
    return yes
      ? '<span class="flag yes" title="yes">✓</span>'
      : '<span class="flag no" title="no">·</span>';
  }

  function setError(msg) {
    const el = document.getElementById("error");
    if (!msg) {
      el.classList.remove("show");
      el.textContent = "";
      return;
    }
    el.textContent = msg;
    el.classList.add("show");
  }

  function setUpdated() {
    const el = document.getElementById("updated");
    if (!lastOkAt) {
      el.textContent = "Waiting for data…";
      return;
    }
    const sec = Math.max(0, Math.round((Date.now() - lastOkAt) / 1000));
    el.textContent = sec === 0 ? "Updated just now" : "Updated " + sec + "s ago";
  }

  function renderHealth(h) {
    const pill = document.getElementById("status-pill");
    const ok = h && h.status === "ok";
    pill.textContent = ok ? "ok" : "down";
    pill.className = "pill " + (ok ? "ok" : "fail");
    const meta = document.getElementById("health-meta");
    let html =
      "<span>version <strong>" + esc(h.version) + "</strong></span>" +
      "<span>branch <strong class='mono'>" + esc(h.branch) + "</strong></span>" +
      "<span>queued <strong>" + esc(h.queued) + "</strong></span>";
    if (h.repo) {
      html += "<span>repo <strong class='mono'>" + esc(h.repo) + "</strong></span>";
    }
    meta.innerHTML = html;
  }

  function renderEnvs(data) {
    const root = document.getElementById("envs");
    const list = (data && data.environments) || [];
    const untracked = (data && data.untracked) || [];
    if (!list.length && !untracked.length) {
      root.innerHTML = '<div class="empty">No environments recorded yet.</div>';
      return;
    }
    let html =
      "<table><thead><tr>" +
      "<th>Name</th><th>Backend</th><th>Present</th><th>Kernel</th>" +
      "<th>Built</th><th>Commit</th>" +
      "</tr></thead><tbody>";
    for (const e of list) {
      html += "<tr>";
      html += "<td><span class='mono'>" + esc(e.name) + "</span>";
      if (e.display_name && e.display_name !== e.name) {
        html += "<br><span class='muted'>" + esc(e.display_name) + "</span>";
      }
      html += "</td>";
      html += "<td>" + esc(e.backend) + "</td>";
      html += "<td>" + flag(e.present) + "</td>";
      html += "<td>" + flag(e.kernel) + "</td>";
      html += "<td>" + esc(fmtTime(e.built_at)) + "</td>";
      html += "<td class='mono'>" + esc(shortSha(e.commit)) + "</td>";
      html += "</tr>";
    }
    html += "</tbody></table>";
    if (untracked.length) {
      html += '<div class="empty">Untracked on disk: ';
      html += untracked.map(function (n) {
        return "<span class='mono'>" + esc(n) + "</span>";
      }).join(", ");
      html += "</div>";
    }
    root.innerHTML = html;
  }

  function renderJobs(data) {
    const root = document.getElementById("jobs");
    const list = (data && data.jobs) || [];
    if (!list.length) {
      root.innerHTML = '<div class="empty">No jobs yet.</div>';
      return;
    }
    let html =
      "<table><thead><tr>" +
      "<th>Status</th><th>Id</th><th>Mode</th><th>Trigger</th>" +
      "<th>Created</th><th>Commit</th>" +
      "</tr></thead><tbody>";
    for (const j of list) {
      const sel = j.id === selectedJobId ? " selected" : "";
      html += '<tr class="selectable' + sel + '" data-job-id="' + esc(j.id) + '">';
      html += '<td><span class="pill ' + esc(j.status) + '">' + esc(j.status) + "</span></td>";
      html += "<td class='mono'>" + esc(j.id) + "</td>";
      html += "<td>" + esc(j.mode) + "</td>";
      html += "<td>" + esc(j.trigger);
      if (j.pr_number != null) html += " · PR #" + esc(j.pr_number);
      html += "</td>";
      html += "<td>" + esc(fmtTime(j.created_at)) + "</td>";
      html += "<td class='mono'>" + esc(shortSha(j.commit)) + "</td>";
      html += "</tr>";
    }
    html += "</tbody></table>";
    root.innerHTML = html;
    root.querySelectorAll("tr.selectable").forEach(function (row) {
      row.addEventListener("click", function () {
        selectJob(row.getAttribute("data-job-id"));
      });
    });
  }

  async function selectJob(id) {
    selectedJobId = id;
    document.querySelectorAll("#jobs tr.selectable").forEach(function (row) {
      row.classList.toggle("selected", row.getAttribute("data-job-id") === id);
    });
    const panel = document.getElementById("log-panel");
    const title = document.getElementById("log-title");
    const pre = document.getElementById("log");
    panel.hidden = false;
    title.textContent = "Log · " + id;
    pre.textContent = "Loading…";
    try {
      const res = await fetch(apiUrl("jobs/" + encodeURIComponent(id)));
      if (!res.ok) throw new Error("HTTP " + res.status);
      const detail = await res.json();
      if (selectedJobId !== id) return;
      const lines = detail.log || [];
      pre.textContent = lines.length ? lines.join("\\n") : "(empty log)";
    } catch (err) {
      if (selectedJobId !== id) return;
      pre.textContent = "Failed to load log: " + err;
    }
  }

  async function refresh() {
    const gen = ++refreshGen;
    if (abort) abort.abort();
    abort = new AbortController();
    const signal = abort.signal;
    try {
      const [hRes, jRes, eRes] = await Promise.all([
        fetch(apiUrl("healthz"), { signal: signal }),
        fetch(apiUrl("jobs?limit=20"), { signal: signal }),
        fetch(apiUrl("environments"), { signal: signal }),
      ]);
      if (gen !== refreshGen) return;
      if (!hRes.ok || !jRes.ok || !eRes.ok) {
        throw new Error(
          "HTTP " + [hRes.status, jRes.status, eRes.status].join("/")
        );
      }
      const [health, jobs, envs] = await Promise.all([
        hRes.json(),
        jRes.json(),
        eRes.json(),
      ]);
      if (gen !== refreshGen) return;
      renderHealth(health);
      renderJobs(jobs);
      renderEnvs(envs);
      lastOkAt = Date.now();
      setUpdated();
      setError(null);
      if (selectedJobId) {
        const stillThere = (jobs.jobs || []).some(function (j) {
          return j.id === selectedJobId;
        });
        if (stillThere) selectJob(selectedJobId);
      }
    } catch (err) {
      if (err && err.name === "AbortError") return;
      if (gen !== refreshGen) return;
      setError("Refresh failed: " + err);
      setUpdated();
    }
  }

  setInterval(setUpdated, 1000);
  setInterval(refresh, REFRESH_MS);
  refresh();
})();
</script>
</body>
</html>
"""
