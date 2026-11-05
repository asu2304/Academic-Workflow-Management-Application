/* GradeMate UI behaviour — progressive enhancement, no framework. Every page works without it. */
(() => {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const icon = (name, cls = "") => `<svg class="icon ${cls}"><use href="#i-${name}"/></svg>`;
  document.documentElement.classList.remove("no-js");

  /* ---------- toasts ---------- */
  const toastRoot = document.createElement("div");
  toastRoot.className = "toasts";
  toastRoot.setAttribute("aria-live", "polite");
  document.body.appendChild(toastRoot);
  const TOAST = {
    info: ["info", "tone-primary"], success: ["check", "tone-green"], error: ["alert", "tone-red"], warn: ["alert", "tone-amber"],
  };
  function toast(message, kind = "info") {
    const [ic, tone] = TOAST[kind] || TOAST.info;
    const el = document.createElement("div");
    el.className = "toast";
    el.innerHTML = `<div class="ico ${tone}">${icon(ic, "sm")}</div><div class="msg"></div><button aria-label="Dismiss">${icon("x", "sm")}</button>`;
    $(".msg", el).textContent = message;
    const close = () => { el.classList.add("out"); setTimeout(() => el.remove(), 200); };
    $("button", el).addEventListener("click", close);
    toastRoot.appendChild(el);
    setTimeout(close, kind === "error" ? 8000 : 5000);
  }
  window.toast = toast;
  const flashData = $("#flash-data");
  if (flashData) {
    try { JSON.parse(flashData.textContent).forEach((f) => toast(f.message, f.kind)); } catch (_) { /* ignore */ }
  }

  /* ---------- mobile nav ---------- */
  $$("[data-nav-toggle]").forEach((b) => b.addEventListener("click", () => document.body.classList.toggle("nav-open")));
  $$(".scrim").forEach((s) => s.addEventListener("click", () => document.body.classList.remove("nav-open")));

  /* ---------- local times ---------- */
  const fmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
  const fmtShort = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  function relative(date) {
    const diff = (date - Date.now()) / 1000;
    const abs = Math.abs(diff);
    const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
    for (const [u, s] of units) if (abs >= s) return rtf.format(Math.round(diff / s), u);
    return diff < 0 ? "just now" : "in a moment";
  }
  $$("time[datetime]").forEach((t) => {
    const d = new Date(t.getAttribute("datetime"));
    if (isNaN(d)) return;
    const mode = t.dataset.format || "abs";
    t.title = fmt.format(d);
    if (mode === "rel") t.textContent = relative(d);
    else if (mode === "short") t.textContent = fmtShort.format(d);
    else if (mode === "due") {
      const past = d < Date.now();
      t.textContent = `${past ? "Was due" : "Due"} ${relative(d)}`;
      t.classList.add(past ? "is-past" : (d - Date.now() < 2 * 86400e3 ? "is-soon" : "is-later"));
    } else t.textContent = fmt.format(d);
  });
  // send the browser's timezone offset with forms that carry local date-times
  $$("input[name=tz_offset]").forEach((i) => { i.value = String(new Date().getTimezoneOffset()); });

  /* ---------- tabs ---------- */
  $$("[data-tabs]").forEach((root) => {
    const tabs = $$("[data-tab]", root);
    const panels = $$("[data-panel]", root);
    const show = (name, push) => {
      if (!tabs.some((t) => t.dataset.tab === name)) name = tabs[0]?.dataset.tab;
      tabs.forEach((t) => { const on = t.dataset.tab === name; t.classList.toggle("active", on); t.setAttribute("aria-selected", on); });
      panels.forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
      if (push) history.replaceState(null, "", `#${name}`);
    };
    tabs.forEach((t) => t.addEventListener("click", () => show(t.dataset.tab, true)));
    show((location.hash || "").slice(1), false);
  });

  /* ---------- dialogs ---------- */
  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-open]");
    if (opener) {
      const dlg = document.getElementById(opener.dataset.open);
      if (dlg) { e.preventDefault(); dlg.showModal(); $("textarea, input:not([type=hidden])", dlg)?.focus(); }
    }
    const closer = e.target.closest("[data-close]");
    if (closer) { e.preventDefault(); closer.closest("dialog")?.close(); }
  });
  $$("dialog.modal").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));

  /* ---------- confirm before submit (custom modal, never a browser dialog) ---------- */
  const confirmDlg = $("#confirm-dialog");
  let pendingForm = null;
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (form.dataset.confirm && !form.dataset.confirmed && confirmDlg) {
      e.preventDefault();
      pendingForm = form;
      $("#confirm-title", confirmDlg).textContent = form.dataset.confirmTitle || "Are you sure?";
      $("#confirm-text", confirmDlg).textContent = form.dataset.confirm;
      $("#confirm-ok", confirmDlg).textContent = form.dataset.confirmOk || "Confirm";
      confirmDlg.showModal();
      return;
    }
    // loading state + double-submit guard
    const btn = e.submitter || $("button[type=submit], button:not([type])", form);
    if (btn && !btn.classList.contains("is-loading")) {
      if (form.dataset.submitting) { e.preventDefault(); return; }
      form.dataset.submitting = "1";
      btn.classList.add("is-loading");
      if (btn.dataset.loading) btn.lastChild.textContent = " " + btn.dataset.loading;
    }
    window.__dirty = false;
  }, true);
  if (confirmDlg) {
    $("#confirm-ok", confirmDlg).addEventListener("click", () => {
      confirmDlg.close();
      if (pendingForm) { pendingForm.dataset.confirmed = "1"; pendingForm.requestSubmit(); }
    });
  }

  /* ---------- dropzones ---------- */
  const human = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
  $$(".dropzone").forEach((dz) => {
    const input = $("input[type=file]", dz);
    const list = document.getElementById(dz.dataset.list) || (() => { const l = document.createElement("div"); l.className = "file-list"; dz.after(l); return l; })();
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, () => dz.classList.remove("over")));
    dz.addEventListener("drop", (e) => {
      e.preventDefault();
      if (!e.dataTransfer?.files?.length) return;
      const dt = new DataTransfer();
      if (input.multiple) Array.from(input.files).forEach((f) => dt.items.add(f));
      Array.from(e.dataTransfer.files).slice(0, input.multiple ? 50 : 1).forEach((f) => dt.items.add(f));
      input.files = dt.files;
      render();
    });
    input.addEventListener("change", render);
    function render() {
      list.innerHTML = "";
      Array.from(input.files).forEach((f, idx) => {
        const row = document.createElement("div");
        row.className = "file-item";
        const isImg = f.type.startsWith("image/");
        row.innerHTML = `${isImg ? `<img class="thumb" alt="">` : `<div class="thumb">${icon(f.type === "application/pdf" ? "file-text" : "file")}</div>`}
          <div class="grow"><div class="fname ellipsis"></div><div class="fsize">${human(f.size)}</div></div>
          <button type="button" class="btn quiet sm icon-only" aria-label="Remove">${icon("x", "sm")}</button>`;
        $(".fname", row).textContent = f.name;
        if (isImg) { const img = $("img", row); img.src = URL.createObjectURL(f); img.onload = () => URL.revokeObjectURL(img.src); }
        $("button", row).addEventListener("click", () => {
          const dt = new DataTransfer();
          Array.from(input.files).forEach((g, j) => { if (j !== idx) dt.items.add(g); });
          input.files = dt.files;
          render();
        });
        list.appendChild(row);
      });
    }
  });

  /* ---------- live status polling (replaces page refresh) ---------- */
  const poll = $("[data-poll]");
  if (poll) {
    const url = poll.dataset.poll;
    const initial = poll.dataset.pollKey;
    const tick = async () => {
      try {
        const res = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          if (data.key !== initial) { location.reload(); return; }
        }
      } catch (_) { /* retry next tick */ }
      setTimeout(tick, 3000);
    };
    setTimeout(tick, 3000);
  }

  /* ---------- client-side filtering ---------- */
  $$("[data-search]").forEach((input) => {
    const items = $$(input.dataset.search);
    const empty = document.getElementById(input.dataset.empty || "");
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      let shown = 0;
      items.forEach((el) => { const hit = !q || (el.dataset.text || el.textContent).toLowerCase().includes(q); el.hidden = !hit; shown += hit; });
      if (empty) empty.hidden = shown > 0;
    });
  });
  $$("[data-filter]").forEach((group) => {
    const rows = $$(group.dataset.filter);
    $$(".chip", group).forEach((chip) => chip.addEventListener("click", () => {
      $$(".chip", group).forEach((c) => c.classList.toggle("active", c === chip));
      const want = chip.dataset.value;
      rows.forEach((r) => { r.hidden = want !== "all" && !want.split(",").includes(r.dataset.status); });
    }));
  });

  /* ---------- clickable rows ---------- */
  $$("tr[data-href]").forEach((tr) => {
    tr.classList.add("clickable");
    tr.addEventListener("click", (e) => { if (!e.target.closest("a, button, input, form")) location.href = tr.dataset.href; });
  });

  /* ---------- image lightbox ---------- */
  const lb = $("#lightbox");
  $$("[data-lightbox]").forEach((img) => img.addEventListener("click", (e) => {
    if (!lb) return;
    e.preventDefault();
    $("img", lb).src = img.dataset.lightbox || img.src;
    $("#lightbox-title", lb).textContent = img.alt || "";
    lb.showModal();
  }));

  /* ---------- grading panel: steppers, live total, unsaved changes ---------- */
  const grader = $("[data-grader]");
  if (grader) {
    const shell = $("[data-dirty-scope]") || grader;
    const totalEl = $("[data-total]");
    const inputs = $$("input[data-score]", grader);
    const recalc = () => {
      let total = 0;
      inputs.forEach((inp) => {
        const max = parseFloat(inp.max) || 0;
        let v = parseFloat(inp.value);
        if (isNaN(v)) v = 0;
        total += Math.min(Math.max(v, 0), max);
        const card = inp.closest(".qcard");
        const bar = $(".bar", card);
        const pct = max ? Math.min(Math.max(v, 0), max) / max * 100 : 0;
        if (bar) { $("span", bar).style.width = pct + "%"; bar.className = "bar " + (pct >= 75 ? "good" : pct >= 40 ? "mid" : "low"); }
        card.classList.toggle("changed", String(v) !== String(parseFloat(inp.defaultValue)));
      });
      if (totalEl) totalEl.textContent = (Math.round(total * 100) / 100).toString();
    };
    const markDirty = () => { window.__dirty = true; shell.classList.add("is-dirty"); };
    grader.addEventListener("input", () => { markDirty(); recalc(); });
    $$("[data-step]", grader).forEach((b) => b.addEventListener("click", () => {
      const inp = $("input[data-score]", b.closest(".stepper-input"));
      const step = parseFloat(b.dataset.step);
      const max = parseFloat(inp.max);
      const next = Math.min(Math.max((parseFloat(inp.value) || 0) + step, 0), max);
      inp.value = Math.round(next * 100) / 100;
      markDirty(); recalc();
    }));
    recalc();
    window.addEventListener("beforeunload", (e) => { if (window.__dirty) { e.preventDefault(); e.returnValue = ""; } });
  }

  /* ---------- password visibility ---------- */
  $$("[data-reveal]").forEach((b) => b.addEventListener("click", () => {
    const inp = document.getElementById(b.dataset.reveal);
    inp.type = inp.type === "password" ? "text" : "password";
    b.innerHTML = icon(inp.type === "password" ? "eye" : "eye-off", "sm");
  }));
})();
