// Главная BypassHub: рисуется из состояния, которое присылает программа (window.bh.render(state)).
// Пока программа не подключена — показывается пример (DEMO), чтобы страницу можно было открыть в браузере.
"use strict";

const NAV = [
  ["home", "Главная", "home"], ["zapret", "Zapret", "shield"], ["tg", "TG WS Proxy", "send"],
  ["vpn", "VPN", "vpn"], ["updates", "Обновления", "sync"], ["changelog", "Что нового", "news"],
  ["settings", "Настройки", "settings"], ["log", "Журнал", "list"],
];

const DEMO = {
  version: "1.14.1", theme: "dark", accents: ["#2b7fff", "#00c6ff", "#7f5af0"], changelogDot: true,
  vpn: { on: true, busy: false, status: "Подключён", server: "Нидерланды", ping: 48, since: Date.now() - 754e3,
         chips: ["VLESS", "весь трафик", "российское — напрямую", "kill switch"],
         history: [52, 49, 55, 47, 51, 46, 58, 50, 48, 47, 53, 49, 45, 48, 50, 47, 46, 49, 52, 48] },
  zapret: { on: true, status: "Работает", sub: "Стратегия general (ALT13)" },
  tg: { on: false, status: "Выключен", sub: "Telegram без VPN" },
  quick: { ru: true, ks: true },
  servers: [
    { id: "auto", name: "Авто — лучший сервер", kind: "Выбирает самый быстрый сам", code: "⚡" },
    { id: "1", name: "Нидерланды", kind: "VLESS · Reality", code: "NL", ping: 48, active: true },
    { id: "2", name: "Германия", kind: "VLESS · Reality", code: "DE", ping: 63 },
    { id: "3", name: "Финляндия", kind: "Hysteria2", code: "FI", ping: 71 },
    { id: "4", name: "США", kind: "VLESS · Reality", code: "US", ping: 142 },
  ],
  ip: { ip: "48.103.194.78", country: "Нидерланды", code: "NL", viaVpn: true, checked: "14:32" },
  avail: [
    { name: "Discord", icon: "chat", state: "ok", ms: 84 },
    { name: "YouTube", icon: "play", state: "ok", ms: 61 },
    { name: "Telegram", icon: "send", state: "part", ms: null },
  ],
  updates: { pending: [], version: "1.14.1" },
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pingClass = (ms) => (ms == null ? "" : ms < 150 ? "g" : ms < 350 ? "y" : "r");
let state = DEMO;

// действия — в программу (Python); без неё просто меняем пример
function send(action, arg) {
  if (window.pywebview && window.pywebview.api) return window.pywebview.api.action(action, arg ?? null);
  if (action === "vpn") { state.vpn.on = !state.vpn.on; state.vpn.since = Date.now(); render(state); }
  if (action === "zapret" || action === "tg") { state[action].on = !state[action].on;
    state[action].status = state[action].on ? "Работает" : "Выключен"; render(state); }
}

function renderNav(s) {
  $("nav").innerHTML = NAV.map(([key, title, ic]) =>
    `<a class="nav-item ${key === "home" ? "active" : ""}" data-page="${key}">${icon(ic)}<span>${title}</span>` +
    (key === "changelog" && s.changelogDot ? `<span class="dot"></span>` : "") + `</a>`).join("");
  $("support").innerHTML = icon("heart") + "<span>Поддержать проект</span>";
  $("version").textContent = "v" + s.version;
  $("brand-badge").innerHTML = icon("bolt");
}

function renderHero(v) {
  const hero = $("hero");
  hero.classList.toggle("on", v.on && !v.busy);
  hero.classList.toggle("busy", !!v.busy);
  $("power").querySelector(".core").innerHTML = icon("power");
  $("hero-status").textContent = v.status;
  $("hero-server").innerHTML = v.server
    ? icon("vpn") + `<span>${esc(v.server)}</span>` + (v.on && v.ping ? `<span class="ping ${pingClass(v.ping)}">${v.ping} мс</span>` : "")
    : "";
  $("chips").innerHTML = v.on
    ? v.chips.map((c) => `<span class="chip">${c === "kill switch" ? icon("lock") : ""}${esc(c)}</span>`).join("")
    : `<p class="hint">Нажмите на кнопку, чтобы подключиться</p>`;
  $("spark").innerHTML = v.on && v.history && v.history.length > 2 ? sparkline(v.history) : "";
  tick();
}

function sparkline(data) {
  const w = 180, h = 64, max = Math.max(...data) * 1.15, min = Math.min(...data) * 0.85;
  const pts = data.map((d, i) => [i * (w / (data.length - 1)), h - ((d - min) / (max - min)) * h]);
  const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const avg = Math.round(data.reduce((a, b) => a + b, 0) / data.length);
  const last = pts[pts.length - 1];
  return `<div class="lbl"><span>Задержка</span><span>в среднем <b>${avg} мс</b></span></div>
  <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <defs><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="var(--accent-2)" stop-opacity=".45"/><stop offset="1" stop-color="var(--accent-2)" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${line} L${w} ${h} L0 ${h} Z" fill="url(#sg)"/>
    <path d="${line}" fill="none" stroke="var(--accent-2)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="4" fill="var(--accent-2)"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="9" fill="var(--accent-2)" opacity=".2"/>
  </svg>`;
}

function tick() {
  const v = state.vpn;
  if (!v.on || v.busy || !v.since) { $("timer").textContent = ""; return; }
  const sec = Math.floor((Date.now() - v.since) / 1000);
  const pad = (n) => String(n).padStart(2, "0");
  $("timer").textContent = `подключено ${pad(Math.floor(sec / 3600))}:${pad(Math.floor(sec % 3600 / 60))}:${pad(sec % 60)}`;
}

function renderTile(id, title, ic, t) {
  const el = $(id);
  el.classList.toggle("on", t.on);
  el.innerHTML = `<div class="squircle">${icon(ic)}</div>
    <div class="tile-body"><div class="tile-title">${title}</div>
      <div class="state"><i></i>${esc(t.status)}</div><div class="sub">${esc(t.sub)}</div></div>
    <input type="checkbox" class="switch" ${t.on ? "checked" : ""} tabindex="-1">`;
}

function renderServers(list) {
  $("servers").innerHTML = list.map((sv) => `
    <div class="server ${sv.active ? "active" : ""}" data-id="${esc(sv.id)}">
      <div class="flag">${sv.id === "auto" ? icon("bolt") : esc(sv.code || "—")}</div>
      <div class="grow"><div class="name">${esc(sv.name)}</div><div class="kind">${esc(sv.kind)}</div></div>
      ${sv.ping != null ? `<span class="ping ${pingClass(sv.ping)}">${sv.ping} мс</span>` : ""}
    </div>`).join("");
}

function renderConn(ip) {
  $("conn").innerHTML = `<div class="card-head" style="margin:0"><h2>Подключение</h2></div>
    <div class="ip">${esc(ip ? ip.ip : "—")}</div>
    <div class="country">${icon("pin")}${esc(ip ? ip.country + " · " + ip.code : "Адрес пока не проверен")}</div>
    ${ip ? (ip.viaVpn ? `<span class="badge safe">${icon("lock")}Сайты видят адрес VPN-сервера</span>`
                      : `<span class="badge open">VPN выключен — это ваш адрес</span>`) : ""}
    <div class="checked">${ip ? "Проверено в " + esc(ip.checked) : ""}</div>`;
}

function renderAvail(items) {
  const label = { ok: "Доступен", part: "Частично", bad: "Недоступен" };
  $("avail").innerHTML = items.map((a) => {
    const pct = a.ms == null ? (a.state === "part" ? 45 : 8) : Math.max(8, 100 - a.ms / 4);
    return `<div class="svc ${a.state}"><div class="squircle">${icon(a.icon)}</div>
      <div class="grow" style="flex:1;min-width:0"><div class="n">${a.name}</div>
      <div class="s">${label[a.state] || "—"}${a.ms != null ? " · " + Math.round(a.ms) + " мс" : ""}</div>
      <div class="bar"><span style="width:${pct}%"></span></div></div></div>`;
  }).join("");
}

function renderUpdates(u) {
  const pending = u.pending || [];
  $("updates").innerHTML = `<h2>Обновления</h2>
    <div class="ver">BypassHub ${esc(u.version)}</div>
    <div class="note"><i style="${pending.length ? "background:var(--yellow)" : ""}"></i>${
      pending.length ? "Доступно: " + pending.map(esc).join(", ") : "Всё актуально"}</div>
    <div class="row">${pending.length ? `<button class="btn small primary" data-act="update">${icon("download")}Обновить</button>` : ""}
      <button class="btn small" data-act="updates">Открыть</button></div>`;
}

function summary(s) {
  const on = [["VPN", s.vpn.on], ["Zapret", s.zapret.on], ["TG WS Proxy", s.tg.on]].filter((x) => x[1]).map((x) => x[0]);
  return on.length ? "Работает: " + on.join(", ") : "Всё выключено — нажмите на сервис, чтобы включить";
}

function applyTheme(s) {
  document.documentElement.dataset.theme = s.theme;
  const [a, b, c] = [s.accents[0], s.accents[1] || s.accents[0], s.accents[2] || s.accents[1] || s.accents[0]];
  const st = document.documentElement.style;
  st.setProperty("--accent", a); st.setProperty("--accent-2", b); st.setProperty("--accent-3", c);
}

function render(s) {
  state = s;
  applyTheme(s);
  renderNav(s);
  $("summary").textContent = summary(s);
  $("check").innerHTML = icon("refresh") + "Проверить";
  $("ping").textContent = "Проверить пинг";
  renderHero(s.vpn);
  renderTile("tile-zapret", "Zapret", "shield", s.zapret);
  renderTile("tile-tg", "TG WS Proxy", "send", s.tg);
  $("q-ru").checked = s.quick.ru; $("q-ks").checked = s.quick.ks;
  renderServers(s.servers);
  renderConn(s.ip);
  renderAvail(s.avail);
  renderUpdates(s.updates);
}

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-page],[data-id],[data-act],#power,#tile-zapret,#tile-tg,#check,#ping");
  if (!t) return;
  if (t.id === "power") send("vpn");
  else if (t.id === "tile-zapret") send("zapret");
  else if (t.id === "tile-tg") send("tg");
  else if (t.id === "check") send("check");
  else if (t.id === "ping") send("ping");
  else if (t.dataset.page) send("page", t.dataset.page);
  else if (t.dataset.id) send("server", t.dataset.id);
  else if (t.dataset.act) send(t.dataset.act);
});
$("q-ru").addEventListener("change", (e) => send("ru_direct", e.target.checked));
$("q-ks").addEventListener("change", (e) => send("killswitch", e.target.checked));

window.bh = { render };
const params = new URLSearchParams(location.search);
if (params.get("theme")) DEMO.theme = params.get("theme");
if (params.get("vpn") === "off") { Object.assign(DEMO.vpn, { on: false, status: "Выключен", server: "Сервер: Нидерланды" }); DEMO.ip = { ip: "95.24.118.7", country: "Россия", code: "RU", viaVpn: false, checked: "14:32" }; }
if (params.get("one")) DEMO.servers = [{ id: "1", name: "NL", kind: "AmneziaWG", code: "NL", active: true }];
render(DEMO);
setInterval(tick, 1000);
