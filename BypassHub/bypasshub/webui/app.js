// Оболочка окна: меню, переход между страницами, связь с программой (window.pywebview.api),
// регулярный опрос состояния, всплывающие сообщения и окна подтверждения.
"use strict";

const App = (() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let page = "home";
  let rev = null;          // версия описания страницы: изменилась — перерисовать
  let nav = [];
  let dirty = {};          // поля, которые человек изменил, но ещё не сохранил: не затираем при обновлении
  let busy = false;
  let lastDots = {};

  // ------------------------------------------------------------ связь с программой
  const demo = () => window.__DEMO__;
  async function call(method, ...args) {
    if (window.pywebview && window.pywebview.api) return window.pywebview.api[method](...args);
    const d = demo();
    if (!d) return null;
    if (method === "boot") return d.boot;
    if (method === "poll") return { home: d.pages.home, page: d.pages[args[0]], rev: "demo-" + args[0], dots: d.boot.dots };
    return { toast: "Демо: " + args[1] };
  }

  // ------------------------------------------------------------ оформление и меню
  function theme(t) {
    if (!t) return;
    document.documentElement.dataset.theme = t.mode;
    const a = t.accents || ["#2b7fff"];
    const st = document.documentElement.style;
    st.setProperty("--accent", a[0]);
    st.setProperty("--accent-2", a[1] || a[0]);
    st.setProperty("--accent-3", a[2] || a[1] || a[0]);
    st.setProperty("--blob-a", String((t.mode === "light" ? 0.38 : 0.55) * Math.max(0.15, (t.tint ?? 70) / 100)));
  }

  function renderNav() {
    $("nav").innerHTML = nav.map(([key, title, ic]) =>
      `<a class="nav-item ${key === page ? "active" : ""}" data-page="${key}">${icon(ic)}<span>${esc(title)}</span>` +
      (lastDots[key] ? `<span class="dot"></span>` : "") + `</a>`).join("");
  }

  // ------------------------------------------------------------ страницы
  function show(key) {
    if (key !== page) { page = key; rev = null; dirty = {}; $("content").innerHTML = ""; }
    renderNav();
    $("content").scrollTop = 0;
    poll(true);
  }

  function apply(res, force) {
    if (!res) return;
    if (res.theme) theme(res.theme);
    if (res.dots) { lastDots = res.dots; renderNav(); }
    if (page === "home") {
      if (!$("hero")) $("content").innerHTML = Home.html();
      if (res.home) Home.render(res.home);
    } else if (res.page && (force || res.rev !== rev)) {
      const active = document.activeElement;
      const focusId = active && active.dataset ? active.dataset.input : null;
      const scroll = $("content").scrollTop;
      const logEl = $("log");
      const atEnd = !logEl || logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 8;
      $("content").innerHTML = Forms.render(res.page);
      for (const [id, v] of Object.entries(dirty)) {
        if (id.startsWith("seg:")) {
          const seg = document.querySelector(`[data-local="${CSS.escape(id.slice(4))}"]`);
          if (seg) seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.value === v));
        } else if (id.startsWith("chk:")) {
          const el = document.querySelector(`[data-field="${CSS.escape(id.slice(4))}"]`);
          if (el) el.checked = v;
        } else {
          const el = document.querySelector(`[data-input="${CSS.escape(id)}"]`);
          if (el) el.value = v;
        }
      }
      if (focusId) { const el = document.querySelector(`[data-input="${CSS.escape(focusId)}"]`); if (el) el.focus(); }
      $("content").scrollTop = scroll;
      const log = $("log"); if (log && atEnd) log.scrollTop = log.scrollHeight;
      const sh = $("sheet");
      const inner = sh.querySelector(".sheet");
      const sheetScroll = inner ? inner.scrollTop : 0;
      sh.innerHTML = Forms.sheet(res.page.sheet);
      const inner2 = sh.querySelector(".sheet"); if (inner2) inner2.scrollTop = sheetScroll;
    }
    if (page === "home" || (res.page && !res.page.sheet)) { if ($("sheet").innerHTML) $("sheet").innerHTML = ""; }
    rev = res.rev ?? rev;
    (res.toasts || []).forEach((t) => toast(t.text, t.kind));
    alertQueue.push(...(res.alerts || []));
    showAlerts();
  }

  const alertQueue = [];
  let alerting = false;
  async function showAlerts() {
    if (alerting) return;
    alerting = true;
    while (alertQueue.length) {
      const a = alertQueue.shift();
      await modal(a.text, { title: a.title, kind: a.kind });
    }
    alerting = false;
  }

  async function poll(force) {
    try { apply(await call("poll", page), force); } catch (e) { console.error(e); }
  }

  // ------------------------------------------------------------ действия
  async function act(id, value) {
    if (busy && id !== "hotkey.set") return;
    busy = true;
    try {
      const res = await call("act", page, id, value ?? null);
      if (res) {
        if (res.clear) for (const k of res.clear) delete dirty[k];
        if (res.error) await modal(res.error, { title: res.title || "Ошибка", kind: "error" });
        if (res.info) await modal(res.info, { title: res.title || "BypassHub" });
        if (res.toast) toast(res.toast, res.kind);
        if (res.ask) {
          const ok = await modal(res.ask, { title: res.title, confirm: true });
          busy = false;
          if (ok) return act(res.then, res.value ?? value);
        }
        if (res.prompt) {
          const text = await modal(res.prompt, { title: res.title, input: res.value ?? "" });
          busy = false;
          if (text !== null && text !== false) return act(res.then, { arg: res.arg, text });
        }
        if (res.go) { busy = false; return show(res.go); }
      }
    } catch (e) {
      await modal(String(e), { title: "Ошибка", kind: "error" });
    } finally {
      busy = false;
      poll(true);
    }
  }

  // ------------------------------------------------------------ сообщения и окна
  function toast(text, kind) {
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = text;
    $("toasts").appendChild(el);
    setTimeout(() => el.classList.add("out"), 3800);
    setTimeout(() => el.remove(), 4300);
  }

  function modal(text, opts = {}) {
    return new Promise((resolve) => {
      const m = $("modal");
      const ask = opts.confirm || opts.input !== undefined;
      m.innerHTML = `<div class="modal ${opts.kind || ""}"><h2>${esc(opts.title || "BypassHub")}</h2>
        <p>${esc(text).replace(/\n/g, "<br>")}</p>
        ${opts.input !== undefined ? `<input class="input" id="modal-input" value="${esc(opts.input)}">` : ""}
        <div class="btn-row">
        ${ask ? `<button class="btn" data-m="0">Отмена</button><button class="btn primary" data-m="1">${opts.input !== undefined ? "Сохранить" : "Да"}</button>`
              : `<button class="btn primary" data-m="1">OK</button>`}</div></div>`;
      m.hidden = false;
      const inp = $("modal-input");
      if (inp) { inp.focus(); inp.select(); }
      const finish = (ok) => {
        const val = inp ? inp.value : null;
        m.hidden = true; m.innerHTML = ""; m.onclick = null; m.onkeydown = null;
        resolve(opts.input !== undefined ? (ok ? val : null) : ok);
      };
      m.onclick = (e) => {
        const b = e.target.closest("[data-m]");
        if (!b && e.target !== m) return;
        finish(b ? b.dataset.m === "1" : false);
      };
      m.onkeydown = (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); };
    });
  }

  // ------------------------------------------------------------ события
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-page],[data-act],[data-fold],[data-seg] button,[data-local] button,[data-server],[data-hotkey],#support");
    if (!t) return;
    if (t.id === "support") return act("support");
    if (t.dataset.page) return show(t.dataset.page);
    if (t.dataset.fold) { Forms.toggleFold(t.dataset.fold); call("act", page, "ui.fold", Forms.openMap()); return poll(true); }
    if (t.dataset.server) return act("server", t.dataset.server);
    if (t.dataset.hotkey) return captureHotkey(t);
    if (t.parentElement && t.parentElement.dataset.seg) return act(t.parentElement.dataset.seg, t.dataset.value);
    if (t.parentElement && t.parentElement.dataset.local) {  // часть формы — запомнить до «Сохранить»
      t.parentElement.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === t));
      dirty["seg:" + t.parentElement.dataset.local] = t.dataset.value;
      return;
    }
    if (t.dataset.act) {
      e.preventDefault();
      if (t.dataset.confirm && !(await modal(t.dataset.confirm, { confirm: true }))) return;
      if (t.dataset.collect !== undefined) {
        const scope = t.closest(".section, .sheet") || document;
        const form = {};
        scope.querySelectorAll("[data-input]").forEach((el) => { form[el.dataset.input] = el.value; delete dirty[el.dataset.input]; });
        scope.querySelectorAll("[data-field]").forEach((el) => { form[el.dataset.field] = el.checked; delete dirty["chk:" + el.dataset.field]; });
        scope.querySelectorAll("[data-local]").forEach((el) => {
          const on = el.querySelector("button.on"); if (on) form[el.dataset.local] = on.dataset.value; delete dirty["seg:" + el.dataset.local]; });
        if (t.dataset.arg !== undefined) form._arg = t.dataset.arg;
        return act(t.dataset.act, form);
      }
      const input = t.closest(".input-row") && t.closest(".input-row").querySelector("[data-input]");
      if (input) delete dirty[input.dataset.input];
      return act(t.dataset.act, t.dataset.arg ?? (input ? input.value : null));
    }
  });

  document.addEventListener("change", async (e) => {
    const t = e.target;
    if (t.dataset.field) { dirty["chk:" + t.dataset.field] = t.checked; return; }
    if (t.dataset.toggle) {
      if (t.checked && t.dataset.confirmOn && !(await modal(t.dataset.confirmOn, { confirm: true }))) { t.checked = false; return; }
      return act(t.dataset.toggle, t.checked);
    }
    if (t.dataset.change) return act(t.dataset.change, t.value);
    if (t.dataset.check) return act(t.dataset.check, { value: t.dataset.value, on: t.checked });
    if (t.dataset.slide) return act(t.dataset.slide, Number(t.value));
    if (t.dataset.color) return act(t.dataset.color, { index: Number(t.dataset.index), color: t.value });
  });

  document.addEventListener("input", (e) => {
    const t = e.target;
    if (t.dataset.input) dirty[t.dataset.input] = t.value;
    if (t.dataset.slide && t.nextElementSibling) t.nextElementSibling.textContent = t.value + "%";
  });

  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (e.key === "Enter" && t.dataset && t.dataset.input && t.tagName === "INPUT") {
      const b = t.closest(".input-row") && t.closest(".input-row").querySelector("[data-act]");
      const id = t.dataset.submit || (b && b.dataset.act);
      if (id) { delete dirty[t.dataset.input]; act(id, t.value); }
    }
  });

  function captureHotkey(btn) {
    btn.textContent = "Нажмите сочетание…";
    btn.classList.add("rec");
    const onKey = (e) => {
      e.preventDefault();
      if (["Control", "Shift", "Alt", "Meta"].includes(e.key)) return;
      document.removeEventListener("keydown", onKey, true);
      if (e.key === "Escape") return poll(true);
      const parts = [e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift", e.metaKey && "Win"].filter(Boolean);
      // по физической клавише (e.code): в русской раскладке e.key был бы «Я» вместо Z
      const c = e.code || "";
      const named = { Space: "Space", Home: "Home", End: "End", Insert: "Insert", Delete: "Delete", PageUp: "PageUp",
        PageDown: "PageDown", Pause: "Pause", ArrowUp: "Up", ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right" };
      const key = c.startsWith("Key") ? c.slice(3) : c.startsWith("Digit") ? c.slice(5) : c.startsWith("Numpad") && /\d$/.test(c)
        ? "Num" + c.slice(-1) : /^F\d+$/.test(c) ? c : (named[c] || e.key);
      act("hotkey.set", { id: btn.dataset.hotkey, combo: [...parts, key].join("+") });
    };
    document.addEventListener("keydown", onKey, true);
  }

  // ------------------------------------------------------------ запуск
  let started = false;
  async function boot() {
    if (started) return;
    const b = await call("boot");
    if (!b) return;
    started = true;
    nav = b.nav;
    lastDots = b.dots || {};
    theme(b.theme);
    Forms.setOpen(b.open);
    $("brand-badge").innerHTML = icon("bolt");
    $("support").innerHTML = icon("heart") + "<span>Поддержать проект</span>";
    $("version").textContent = "v" + b.version;
    page = b.page || "home";
    show(page);
    setInterval(() => { if (!document.hidden) poll(false); }, b.interval || 1500);
  }

  window.addEventListener("pywebviewready", boot);
  window.addEventListener("DOMContentLoaded", () => { if (demo()) boot(); });
  return { act, show, toast, modal, go: show };
})();
