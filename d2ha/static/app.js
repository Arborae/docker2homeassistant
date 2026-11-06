/* D2HA web UI. No framework, no external requests. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const TEXTS = JSON.parse($("#i18n")?.textContent || "{}");
  const LANG = document.documentElement.lang || "en";
  const ICONS = $('link[rel="stylesheet"]').href.replace(/app\.css(\?.*)?$/, "icons.svg$1");

  function T(key, params = {}) {
    const text = TEXTS[key] || key;
    return text.replace(/\{(\w+)\}/g, (_, name) => (name in params ? params[name] : `{${name}}`));
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function icon(name, cls = "") {
    return `<svg class="icon ${cls}" aria-hidden="true"><use href="${ICONS}#i-${name}"></use></svg>`;
  }

  function setCookie(name, value) {
    const secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie = `${name}=${value}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
  }

  async function api(method, url, body) {
    const options = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
    if (method !== "GET") {
      options.headers["X-CSRF-Token"] = document.body.dataset.csrf || "";
      if (body !== undefined) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }
    }
    const response = await fetch(url, options);
    if (response.status === 401) {
      location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
      throw new Error("unauthorized");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.code = data.error;
      throw error;
    }
    return data;
  }

  function toast(message, kind = "info") {
    const box = $("#toasts");
    if (!box) return;
    const item = document.createElement("div");
    item.className = `toast ${kind}`;
    item.innerHTML = `${icon(kind === "error" ? "alert" : "check")}<span>${esc(message)}</span>`;
    box.append(item);
    setTimeout(() => item.remove(), 4500);
  }

  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${(bytes / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
  }

  function timeAgo(seconds) {
    if (!seconds) return T("js.never");
    const diff = Math.max(0, Date.now() / 1000 - seconds);
    if (diff < 60) return T("js.ago.now");
    if (diff < 3600) return T("js.ago.minutes", { n: Math.floor(diff / 60) });
    if (diff < 86400) return T("js.ago.hours", { n: Math.floor(diff / 3600) });
    return T("js.ago.days", { n: Math.floor(diff / 86400) });
  }

  /* Safe markdown subset for release notes: HTML is removed, then text is escaped. */
  function markdown(source) {
    const clean = String(source || "").replace(/\r\n?/g, "\n").replace(/<!--[\s\S]*?-->/g, "").replace(/<\/?[a-zA-Z][^>]*>/g, "");
    const inline = (text) => esc(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
    const out = [];
    let list = false;
    let code = null;
    let para = [];
    const flush = () => {
      if (para.length) out.push(`<p>${inline(para.join(" "))}</p>`);
      para = [];
    };
    const closeList = () => {
      if (list) out.push("</ul>");
      list = false;
    };
    for (const line of clean.split("\n")) {
      if (code !== null) {
        if (line.trim().startsWith("```")) {
          out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
          code = null;
        } else code.push(line);
        continue;
      }
      if (line.trim().startsWith("```")) { flush(); closeList(); code = []; continue; }
      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      const item = line.match(/^\s*(?:[-*+]|\d+\.)\s+(.*)$/);
      if (heading) { flush(); closeList(); out.push(`<h4>${inline(heading[2])}</h4>`); }
      else if (item) { flush(); if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(item[1])}</li>`); }
      else if (!line.trim()) { flush(); closeList(); }
      else para.push(line.trim());
    }
    if (code !== null) out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
    flush();
    closeList();
    return out.join("");
  }

  /* ---------------------------------------------------------------- shared UI */

  function initPreferences() {
    $$('[data-action="theme"]').forEach((button) => button.addEventListener("click", () => {
      const order = ["system", "light", "dark"];
      const next = order[(order.indexOf(document.documentElement.dataset.theme) + 1) % order.length];
      document.documentElement.dataset.theme = next;
      setCookie("d2ha_theme", next);
      const name = next === "system" ? "system" : next === "light" ? "sun" : "moon";
      button.querySelector("use")?.setAttribute("href", `${ICONS}#i-${name}`);
    }));
    $$('[data-action="language"]').forEach((button) => button.addEventListener("click", () => {
      setCookie("d2ha_lang", LANG === "it" ? "en" : "it");
      location.reload();
    }));
    document.addEventListener("click", (event) => {
      const closer = event.target.closest("[data-close]");
      if (closer) closer.closest("dialog")?.close();
    });
    $$("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
      if (event.target === dialog && !dialog.dataset.locked) dialog.close();
    }));
  }

  let confirmDialog = null;
  function confirmAction({ title, text, ok, danger = false }) {
    if (!confirmDialog) {
      confirmDialog = $("#dlg-confirm");
      if (!confirmDialog) {
        confirmDialog = document.createElement("dialog");
        confirmDialog.id = "dlg-confirm";
        confirmDialog.innerHTML = `<div class="dlg"><div class="dlg-head"><h2 id="confirm-title"></h2></div>
          <div class="dlg-body"><p id="confirm-text"></p></div>
          <div class="dlg-foot"><button class="btn" type="button" data-close>${esc(T("js.cancel"))}</button>
          <button class="btn btn-primary" type="button" id="confirm-ok"></button></div></div>`;
        document.body.append(confirmDialog);
      }
    }
    $("#confirm-title", confirmDialog).textContent = title;
    $("#confirm-text", confirmDialog).textContent = text;
    const okButton = $("#confirm-ok", confirmDialog);
    okButton.textContent = ok || T("js.confirm");
    okButton.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
    return new Promise((resolve) => {
      const done = (value) => {
        okButton.removeEventListener("click", accept);
        confirmDialog.removeEventListener("close", cancel);
        resolve(value);
      };
      const accept = () => { done(true); confirmDialog.close(); };
      const cancel = () => done(false);
      okButton.addEventListener("click", accept);
      confirmDialog.addEventListener("close", cancel);
      confirmDialog.showModal();
    });
  }

  function initForms() {
    $$("form[data-confirm]").forEach((form) => {
      if (!form.dataset.confirm) return;
      form.addEventListener("submit", async (event) => {
        if (form.dataset.confirmed) return;
        event.preventDefault();
        if (await confirmAction({ title: T("js.confirm"), text: form.dataset.confirm, danger: true })) {
          form.dataset.confirmed = "1";
          form.requestSubmit();
        }
      });
    });
    $$("[data-copy]").forEach((button) => button.addEventListener("click", async () => {
      const text = $(button.dataset.copy)?.textContent || "";
      try {
        await navigator.clipboard.writeText(text);
        toast(T("js.toast.copied"), "success");
      } catch {
        window.getSelection()?.selectAllChildren($(button.dataset.copy));
      }
    }));
    $$("[data-ha-key]").forEach((input) => input.addEventListener("change", async () => {
      try {
        await api("PUT", `/api/v1/containers/${encodeURIComponent(input.dataset.haKey)}/ha`, { ha: input.checked });
        toast(T("js.toast.saved"), "success");
      } catch {
        input.checked = !input.checked;
        toast(T("js.toast.failed"), "error");
      }
    }));
  }

  /* ---------------------------------------------------------------- containers page */

  const STATE_DOT = { running: "running", restarting: "warn", paused: "warn", created: "", exited: "stopped", dead: "stopped" };

  function containersPage() {
    const readOnly = document.body.dataset.readOnly === "1";
    let containers = [];
    let info = null;
    let filter = "all";
    let query = "";
    let checking = false;

    const list = $("#list");
    const search = $("#search");

    function stateText(c) {
      const parts = [T(`js.state.${c.state}`)];
      if (c.health === "unhealthy" || c.health === "starting") parts.push(T(`js.health.${c.health}`));
      return parts.join(" · ");
    }

    function badges(c) {
      const out = [`<span class="badge badge-${esc(c.update)}" ${c.error ? `title="${esc(c.error)}"` : ""}>${esc(T(`status.${c.update}`))}</span>`];
      if (c.breaking) out.push(`<span class="badge badge-breaking">${icon("alert")}${esc(T("js.breaking"))}</span>`);
      return out.join("");
    }

    function row(c) {
      const dot = c.health === "unhealthy" ? "warn" : STATE_DOT[c.state] ?? "";
      const version = c.update === "available"
        ? `<span class="ver">${esc(c.installed_version)} → <b>${esc(c.latest_version)}</b></span>`
        : `<span class="ver">${esc(c.installed_version)}</span>`;
      const actions = [
        `<button class="btn btn-sm btn-icon" type="button" data-logs="${esc(c.key)}" title="${esc(T("js.action.logs"))}" aria-label="${esc(T("js.action.logs"))}">${icon("logs")}</button>`,
      ];
      if (c.has_notes) actions.push(`<button class="btn btn-sm btn-icon" type="button" data-notes="${esc(c.key)}" title="${esc(T("js.action.notes"))}" aria-label="${esc(T("js.action.notes"))}">${icon("notes")}</button>`);
      if (!readOnly && c.update === "available") {
        if (c.can_update) actions.push(`<button class="btn btn-sm btn-primary" type="button" data-update="${esc(c.key)}">${icon("update")}<span>${esc(T("js.action.update"))}</span></button>`);
        else if (c.is_self) actions.push(`<span class="badge" title="${esc(T("js.self"))}">${esc(T("js.self_short"))}</span>`);
        else if (c.is_proxy) actions.push(`<span class="badge" title="${esc(T("js.proxy"))}">${esc(T("js.proxy_short"))}</span>`);
      }
      return `<div class="ctr">
        <span class="dot ${dot}" title="${esc(stateText(c))}"></span>
        <div class="ctr-main"><div class="ctr-name">${esc(c.name)}</div><div class="ctr-meta">${esc(c.image)} · ${esc(stateText(c))}</div></div>
        <div class="ctr-version">${version}<div class="ctr-badges">${badges(c)}</div></div>
        <div class="ctr-actions">${actions.join("")}</div>
      </div>`;
    }

    function visible(c) {
      if (filter === "updates" && c.update !== "available") return false;
      if (filter === "stopped" && c.state === "running") return false;
      if (!query) return true;
      return `${c.name} ${c.image} ${c.stack}`.toLowerCase().includes(query);
    }

    function render() {
      const shown = containers.filter(visible);
      if (!shown.length) {
        list.innerHTML = `<div class="card empty">${esc(T(containers.length ? "js.empty.filtered" : "js.empty.none"))}</div>`;
      } else {
        const groups = new Map();
        shown.forEach((c) => {
          if (!groups.has(c.stack)) groups.set(c.stack, []);
          groups.get(c.stack).push(c);
        });
        list.innerHTML = Array.from(groups, ([stack, items]) => {
          const updates = items.filter((c) => c.update === "available").length;
          return `<details class="card stack" open>
            <summary>${icon("box")}<span>${esc(stack || T("js.no_stack"))}</span>
              <span class="stack-count">${items.length}</span>
              ${updates ? `<span class="badge badge-available">${esc(T("js.stack_updates", { n: updates }))}</span>` : ""}
              ${icon("chevron", "chev")}</summary>
            ${items.map(row).join("")}
          </details>`;
        }).join("");
      }
      renderSummary();
    }

    function renderSummary() {
      const updates = containers.filter((c) => c.update === "available");
      const running = containers.filter((c) => c.state === "running").length;
      $("#summary-title").textContent = updates.length === 0 ? T("js.summary.none")
        : updates.length === 1 ? T("js.summary.one") : T("js.summary.many", { n: updates.length });
      $("#summary-icon").classList.toggle("has-updates", updates.length > 0);
      $("#summary-icon use")?.setAttribute("href", `${ICONS}#i-${updates.length ? "update" : "check"}`);
      $("#summary-sub").textContent = checking ? T("js.summary.checking")
        : T("js.summary.sub", { total: containers.length, running, ago: timeAgo(info?.check?.last) });
      const checkButton = $("#btn-check");
      checkButton.disabled = checking;
      checkButton.classList.toggle("is-busy", checking);
      const updatable = updates.filter((c) => c.can_update).length;
      const updateAll = $("#btn-update-all");
      if (updateAll) {
        updateAll.hidden = updatable < 2;
        $("#update-all-label").textContent = T("js.update_all", { n: updatable });
      }
      const prune = $("#btn-prune");
      if (prune && info) {
        const unused = info.counts?.unused_images ?? 0;
        prune.disabled = !unused;
        $("span", prune).textContent = T("js.prune", { n: unused });
      }
    }

    async function load() {
      try {
        const [data, details] = await Promise.all([api("GET", "/api/v1/containers"), api("GET", "/api/v1/info")]);
        containers = data.containers;
        info = details;
        checking = data.checking;
        render();
      } catch (error) {
        if (error.message !== "unauthorized") toast(T("js.toast.load_failed"), "error");
      }
    }

    /* actions */
    async function checkNow() {
      try {
        await api("POST", "/api/v1/check");
        checking = true;
        renderSummary();
        toast(T("js.toast.check_started"));
        const started = Date.now();
        const poll = setInterval(async () => {
          await load();
          if (!checking || Date.now() - started > 5 * 60 * 1000) {
            clearInterval(poll);
            if (!checking) toast(T("js.toast.check_done"), "success");
          }
        }, 3000);
      } catch {
        toast(T("js.toast.failed"), "error");
      }
    }

    async function updateOne(key) {
      const c = containers.find((item) => item.key === key);
      if (!c) return;
      const ok = await confirmAction({
        title: T("js.confirm.update.title", { name: c.name }),
        text: T("js.confirm.update.text") + (c.breaking ? `\n\n${T("js.confirm.update.breaking")}` : ""),
        ok: T("js.action.update"),
      });
      if (!ok) return;
      try {
        const job = await api("POST", `/api/v1/containers/${encodeURIComponent(key)}/update`);
        follow(job);
      } catch (error) {
        toast(T(`api.${error.code}`) !== `api.${error.code}` ? T(`api.${error.code}`) : T("js.toast.failed"), "error");
      }
    }

    async function updateAll() {
      const n = containers.filter((c) => c.update === "available" && c.can_update).length;
      const ok = await confirmAction({ title: T("js.confirm.update_all.title", { n }), text: T("js.confirm.update_all.text"), ok: T("js.action.update") });
      if (!ok) return;
      try {
        follow(await api("POST", "/api/v1/update-all"));
      } catch {
        toast(T("js.toast.failed"), "error");
      }
    }

    async function prune() {
      const n = info?.counts?.unused_images ?? 0;
      const ok = await confirmAction({ title: T("js.confirm.prune.title"), text: T("js.confirm.prune.text", { n }), ok: T("js.confirm.prune.ok"), danger: true });
      if (!ok) return;
      try {
        follow(await api("POST", "/api/v1/images/prune"));
      } catch {
        toast(T("js.toast.failed"), "error");
      }
    }

    /* job progress */
    const progress = $("#dlg-progress");
    function showJob(job) {
      const titles = { update: T("js.progress.title", { name: job.label }), update_all: T("js.progress.title_all"), prune: T("js.progress.prune") };
      $("#progress-title").textContent = titles[job.kind] || "";
      const steps = $("#progress-steps");
      steps.hidden = job.kind !== "update_all" || !job.steps;
      steps.textContent = T("js.progress.step", { step: job.step, steps: job.steps, name: job.label });
      const finished = job.state === "done" || job.state === "failed";
      $("#progress-phase").hidden = finished;
      $("#progress-phase-text").textContent = T(`phase.${job.state === "queued" ? "queued" : job.phase || "queued"}`);
      const bar = $("#progress-bar");
      const known = typeof job.percent === "number";
      bar.hidden = finished;
      bar.classList.toggle("indeterminate", !known && !finished);
      bar.firstElementChild.style.width = known ? `${job.percent}%` : "";
      $("#progress-amount").textContent = job.phase === "pull" && job.total ? `${formatBytes(job.current)} / ${formatBytes(job.total)}` : known ? `${job.percent}%` : "";
      $("#progress-detail").textContent = finished ? "" : job.detail || "";
      $("#progress-hint").hidden = finished;
      const result = $("#progress-result");
      result.hidden = !finished;
      if (finished) result.innerHTML = resultHtml(job);
    }

    function resultHtml(job) {
      if (job.state === "failed") {
        return `<div class="alert alert-error">${icon("alert")}<span>${esc(T(job.error))}${job.detail ? `<br><small>${esc(job.detail)}</small>` : ""}</span></div>`;
      }
      let text = "";
      if (job.kind === "update") text = job.result?.changed ? T("js.toast.updated", { name: job.label }) : T("js.toast.already", { name: job.label });
      if (job.kind === "update_all") text = T("js.progress.done_all", { ok: job.result?.updated?.length || 0, ko: job.result?.failed?.length || 0 });
      if (job.kind === "prune") text = T("js.toast.pruned", { n: job.result?.removed || 0, size: formatBytes(job.result?.reclaimed || 0) });
      return `<div class="alert alert-success">${icon("check")}<span>${esc(text)}</span></div>`;
    }

    function follow(job, open = true) {
      showJob(job);
      if (open && !progress.open) progress.showModal();
      const finish = (last) => {
        showJob(last);
        load();
        if (!progress.open) toast(last.state === "done" ? $("#progress-result").textContent.trim() : T(last.error), last.state === "done" ? "success" : "error");
      };
      const source = new EventSource(`/api/v1/jobs/${job.id}/events`);
      source.onmessage = (event) => {
        const snapshot = JSON.parse(event.data);
        if (snapshot.state === "done" || snapshot.state === "failed") {
          source.close();
          finish(snapshot);
        } else if (progress.open) showJob(snapshot);
      };
      source.onerror = () => {
        source.close();
        const poll = setInterval(async () => {
          try {
            const snapshot = await api("GET", `/api/v1/jobs/${job.id}`);
            if (snapshot.state === "done" || snapshot.state === "failed") {
              clearInterval(poll);
              finish(snapshot);
            } else if (progress.open) showJob(snapshot);
          } catch {
            clearInterval(poll);
          }
        }, 2000);
      };
    }

    /* logs */
    const logsDialog = $("#dlg-logs");
    const logsView = $("#logs-view");
    let logLines = [];
    let logKey = "";
    let liveSource = null;

    function logLineHtml(line) {
      const clean = line.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "");
      const match = clean.match(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?Z\s?(.*)$/);
      const stamp = match ? new Date(`${match[1]}Z`).toLocaleString(LANG, { dateStyle: "short", timeStyle: "medium" }) : "";
      const text = match ? match[2] : clean;
      // An explicit level word wins (a WRN line may carry an "error=" field).
      const level = /\b(ERR|ERROR|FTL|FATAL|CRIT|CRITICAL|PANIC)\b/.test(text) ? "lv-error"
        : /\b(WRN|WARN|WARNING)\b/.test(text) ? "lv-warn"
        : /\b(INF|INFO|DBG|DEBUG)\b/.test(text) ? ""
        : /level=(error|fatal)|\[error\]|\berror:|traceback|exception/i.test(text) ? "lv-error"
        : /level=warn|\[warn(ing)?\]|\bwarning:/i.test(text) ? "lv-warn" : "";
      return `${stamp ? `<span class="ts">${esc(stamp)}</span> ` : ""}<span class="${level}">${esc(text)}</span>`;
    }

    function renderLogs(keepScroll = false) {
      const filterText = $("#logs-filter").value.trim().toLowerCase();
      const atBottom = logsView.scrollHeight - logsView.scrollTop - logsView.clientHeight < 40;
      const lines = filterText ? logLines.filter((l) => l.toLowerCase().includes(filterText)) : logLines;
      logsView.innerHTML = lines.length ? lines.map(logLineHtml).join("\n") : `<span class="muted">${esc(T("js.logs.empty"))}</span>`;
      if (!keepScroll || atBottom) logsView.scrollTop = logsView.scrollHeight;
    }

    async function loadLogs() {
      logsView.innerHTML = `<span class="muted">${esc(T("js.logs.loading"))}</span>`;
      try {
        const data = await api("GET", `/api/v1/containers/${encodeURIComponent(logKey)}/logs?tail=${$("#logs-tail").value}`);
        logLines = data.lines;
        renderLogs();
      } catch {
        logsView.innerHTML = `<span class="lv-error">${esc(T("js.toast.failed"))}</span>`;
      }
    }

    function setLive(on) {
      const button = $("#logs-live");
      button.setAttribute("aria-pressed", on ? "true" : "false");
      button.querySelector("use")?.setAttribute("href", `${ICONS}#i-${on ? "pause" : "play"}`);
      if (liveSource) { liveSource.close(); liveSource = null; }
      if (!on) return;
      liveSource = new EventSource(`/api/v1/containers/${encodeURIComponent(logKey)}/logs/stream`);
      liveSource.onmessage = (event) => {
        logLines.push(...JSON.parse(event.data));
        if (logLines.length > 5000) logLines = logLines.slice(-5000);
        renderLogs(true);
      };
      liveSource.onerror = () => setLive(false);
    }

    function openLogs(key) {
      const c = containers.find((item) => item.key === key);
      logKey = key;
      $("#logs-title").textContent = c ? c.name : key;
      logLines = [];
      logsDialog.showModal();
      loadLogs();
    }

    $("#logs-tail").addEventListener("change", loadLogs);
    $("#logs-filter").addEventListener("input", () => renderLogs(true));
    $("#logs-live").addEventListener("click", (event) => setLive(event.currentTarget.getAttribute("aria-pressed") !== "true"));
    $("#logs-wrap").addEventListener("click", (event) => {
      const on = event.currentTarget.getAttribute("aria-pressed") !== "true";
      event.currentTarget.setAttribute("aria-pressed", on ? "true" : "false");
      logsView.classList.toggle("wrap", on);
    });
    $("#logs-bottom").addEventListener("click", () => { logsView.scrollTop = logsView.scrollHeight; });
    $("#logs-download").addEventListener("click", () => {
      const blob = new Blob([logLines.join("\n")], { type: "text/plain" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `${$("#logs-title").textContent}-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.log`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    });
    logsDialog.addEventListener("close", () => setLive(false));

    /* release notes */
    async function openNotes(key) {
      const c = containers.find((item) => item.key === key);
      const dialog = $("#dlg-notes");
      $("#notes-body").innerHTML = `<span class="muted">${esc(T("js.logs.loading"))}</span>`;
      dialog.showModal();
      try {
        const notes = await api("GET", `/api/v1/containers/${encodeURIComponent(key)}/notes`);
        $("#notes-title").textContent = T("js.notes.title", { name: c?.name || key, version: notes.version });
        $("#notes-link").href = notes.url;
        const breaking = notes.breaking
          ? `<div class="breaking-box"><h3>${icon("alert")}${esc(T("js.breaking"))}</h3><div class="md">${markdown(notes.breaking)}</div></div>` : "";
        const published = notes.published ? `<p class="muted">${esc(T("js.notes.published", { date: notes.published }))}</p>` : "";
        $("#notes-body").innerHTML = `${published}${breaking}<div class="md">${markdown(notes.body) || esc(T("js.notes.none"))}</div>`;
      } catch {
        $("#notes-body").innerHTML = `<span class="muted">${esc(T("js.notes.none"))}</span>`;
      }
    }

    /* wiring */
    list.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.dataset.logs) openLogs(button.dataset.logs);
      if (button.dataset.notes) openNotes(button.dataset.notes);
      if (button.dataset.update) updateOne(button.dataset.update);
    });
    search.addEventListener("input", () => { query = search.value.trim().toLowerCase(); render(); });
    $$("[data-filter]").forEach((button) => button.addEventListener("click", () => {
      filter = button.dataset.filter;
      $$("[data-filter]").forEach((b) => b.setAttribute("aria-pressed", b === button ? "true" : "false"));
      render();
    }));
    $("#btn-check").addEventListener("click", checkNow);
    $("#btn-update-all")?.addEventListener("click", updateAll);
    $("#btn-prune")?.addEventListener("click", prune);

    load().then(async () => {
      try {
        const { jobs } = await api("GET", "/api/v1/jobs");
        if (jobs.length) follow(jobs[0]);
      } catch { /* nothing running */ }
    });
    setInterval(() => { if (document.visibilityState === "visible" && !logsDialog.open) load(); }, 60000);
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") load(); });
  }

  /* ---------------------------------------------------------------- boot */

  document.addEventListener("DOMContentLoaded", () => {
    initPreferences();
    initForms();
    if (document.body.dataset.page === "containers") containersPage();
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
})();
