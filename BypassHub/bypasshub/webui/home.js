// Главная: VPN крупно, сервисы, быстрые настройки, серверы, подключение, доступность, обновления.
"use strict";

window.Home = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pingClass = (ms) => (ms == null ? "" : ms < 150 ? "g" : ms < 350 ? "y" : "r");
  let since = null;

  const html = () => `
    <header class="page-head">
      <div><h1>Главная</h1><p class="summary" id="summary"></p></div>
      <button class="btn" data-act="check" id="check">${icon("refresh")}Проверить</button>
    </header>
    <section class="card hero" id="hero">
      <button class="power" data-act="vpn" aria-label="Включить или выключить VPN"><span class="ring"></span>
        <span class="core">${icon("power")}</span></button>
      <div class="hero-body">
        <div class="hero-top"><span class="eyebrow">VPN</span><span class="timer" id="timer"></span></div>
        <div class="hero-status" id="hero-status"></div>
        <div class="hero-server" id="hero-server"></div>
        <div class="chips" id="chips"></div>
      </div>
      <div class="spark" id="spark"></div>
    </section>
    <div class="grid">
      <div class="col">
        <section class="card tile" id="tile-zapret" data-act="zapret"></section>
        <section class="card tile" id="tile-tg" data-act="tg"></section>
        <section class="card quick">
          <h2>Быстрые настройки VPN</h2>
          <label class="switch-row"><span>Российское — напрямую</span>
            <input type="checkbox" class="switch" data-toggle="ru_direct" id="q-ru"></label>
          <label class="switch-row"><span>Kill switch: без VPN — без интернета</span>
            <input type="checkbox" class="switch" data-toggle="killswitch" id="q-ks"></label>
        </section>
      </div>
      <div class="col">
        <section class="card servers">
          <div class="card-head"><h2>Серверы</h2>
            <button class="btn small" data-act="ping" id="ping">Проверить пинг</button></div>
          <div class="server-list" id="servers"></div>
        </section>
        <section class="card conn" id="conn"></section>
      </div>
    </div>
    <div class="grid bottom">
      <section class="card avail"><h2>Доступность</h2><div class="avail-row" id="avail"></div></section>
      <section class="card updates" id="updates"></section>
    </div>`;

  function hero(v) {
    const el = document.getElementById("hero");
    el.classList.toggle("on", v.on && !v.busy);
    el.classList.toggle("busy", !!v.busy);
    document.getElementById("hero-status").textContent = v.status;
    document.getElementById("hero-server").innerHTML = v.server
      ? icon("vpn") + `<span>${esc(v.server)}</span>` + (v.on && v.ping ? `<span class="ping ${pingClass(v.ping)}">${Math.round(v.ping)} мс</span>` : "")
      : "";
    document.getElementById("chips").innerHTML = v.on && !v.busy
      ? (v.chips || []).map((c) => `<span class="chip">${c === "kill switch" ? icon("lock") : ""}${esc(c)}</span>`).join("")
      : `<p class="hint">${esc(v.hint || "Нажмите на кнопку, чтобы подключиться")}</p>`;
    document.getElementById("spark").innerHTML = v.on && v.history && v.history.length > 2 ? spark(v.history) : "";
    since = v.on && !v.busy && v.since ? v.since : null;
    tick();
  }

  function spark(data) {
    const w = 180, h = 64, max = Math.max(...data) * 1.15, min = Math.min(...data) * 0.85 || 0;
    const pts = data.map((d, i) => [i * (w / (data.length - 1)), h - ((d - min) / (max - min || 1)) * h]);
    const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const avg = Math.round(data.reduce((a, b) => a + b, 0) / data.length);
    const last = pts[pts.length - 1];
    return `<div class="lbl"><span>Задержка</span><span>в среднем <b>${avg} мс</b></span></div>
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="var(--accent-2)" stop-opacity=".45"/><stop offset="1" stop-color="var(--accent-2)" stop-opacity="0"/>
    </linearGradient></defs><path d="${line} L${w} ${h} L0 ${h} Z" fill="url(#sg)"/>
    <path d="${line}" fill="none" stroke="var(--accent-2)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="4" fill="var(--accent-2)"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="9" fill="var(--accent-2)" opacity=".2"/></svg>`;
  }

  function tick() {
    const t = document.getElementById("timer");
    if (!t) return;
    if (!since) { t.textContent = ""; return; }
    const sec = Math.max(0, Math.floor((Date.now() - since) / 1000));
    const pad = (n) => String(n).padStart(2, "0");
    t.textContent = `подключено ${pad(Math.floor(sec / 3600))}:${pad(Math.floor(sec % 3600 / 60))}:${pad(sec % 60)}`;
  }

  function tile(id, title, ic, t) {
    const el = document.getElementById(id);
    el.classList.toggle("on", !!t.on);
    el.classList.toggle("busy", !!t.busy);
    el.innerHTML = `<div class="squircle">${icon(ic)}</div>
      <div class="tile-body"><div class="tile-title">${title}</div>
        <div class="state ${t.warn ? "warn" : ""}"><i></i>${esc(t.status)}</div><div class="sub">${esc(t.sub)}</div></div>
      <input type="checkbox" class="switch" ${t.on ? "checked" : ""} ${t.busy ? "disabled" : ""} tabindex="-1">`;
  }

  function servers(list) {
    const el = document.getElementById("servers");
    el.innerHTML = list.length ? list.map((sv) => `
      <div class="server ${sv.active ? "active" : ""}" data-server="${esc(sv.id)}">
        <div class="flag">${sv.id === "auto" ? icon("bolt") : esc(sv.code || "—")}</div>
        <div class="grow"><div class="name">${esc(sv.name)}</div><div class="kind">${esc(sv.kind)}</div></div>
        ${sv.now ? `<span class="now">сейчас</span>` : ""}
        ${sv.ping != null ? `<span class="ping ${pingClass(sv.ping)}">${sv.ping === false ? "нет ответа" : Math.round(sv.ping) + " мс"}</span>` : ""}
      </div>`).join("")
      : `<div class="empty"><p>Серверов пока нет — добавьте ключ VLESS, Hysteria2 или AmneziaVPN.</p>
         <button class="btn small primary" data-page="vpn">Добавить сервер</button></div>`;
  }

  function conn(ip) {
    document.getElementById("conn").innerHTML = `<div class="card-head" style="margin:0"><h2>Подключение</h2></div>
      <div class="ip">${esc(ip && ip.ip ? ip.ip : "—")}</div>
      <div class="country">${icon("pin")}${esc(ip && ip.ip ? ip.country + (ip.code ? " · " + ip.code : "") : (ip && ip.checking ? "Проверка…" : "Адрес пока не проверен"))}</div>
      ${ip && ip.ip ? (ip.viaVpn ? `<span class="badge safe">${icon("lock")}Сайты видят адрес VPN-сервера</span>`
                                 : `<span class="badge open">VPN выключен — это ваш адрес</span>`) : ""}
      <div class="checked">${ip && ip.checked ? "Проверено в " + esc(ip.checked) : ""}</div>`;
  }

  function avail(items) {
    const label = { ok: "Доступен", part: "Частично", bad: "Недоступен", wait: "Проверка…", none: "Не проверено" };
    document.getElementById("avail").innerHTML = items.map((a) => {
      const pct = a.ms == null ? (a.state === "part" ? 45 : a.state === "bad" ? 8 : 0) : Math.max(8, 100 - a.ms / 4);
      return `<div class="svc ${a.state}"><div class="squircle">${icon(a.icon)}</div>
        <div style="flex:1;min-width:0"><div class="n">${esc(a.name)}</div>
        <div class="s">${label[a.state] || "—"}${a.ms != null ? " · " + Math.round(a.ms) + " мс" : ""}</div>
        <div class="bar"><span style="width:${pct}%"></span></div></div></div>`;
    }).join("");
  }

  function updates(u) {
    const pending = u.pending || [];
    document.getElementById("updates").innerHTML = `<h2>Обновления</h2>
      <div class="ver">BypassHub ${esc(u.version)}</div>
      <div class="note"><i style="${pending.length ? "background:var(--yellow)" : ""}"></i>${
        pending.length ? "Доступно: " + pending.map(esc).join(", ") : esc(u.note || "Всё актуально")}</div>
      <div class="row">${pending.length ? `<button class="btn small primary" data-act="update_all">${icon("download")}Обновить</button>` : ""}
        <button class="btn small" data-page="updates">Открыть</button></div>`;
  }

  function render(s) {
    const on = [["VPN", s.vpn.on], ["Zapret", s.zapret.on], ["TG WS Proxy", s.tg.on]].filter((x) => x[1]).map((x) => x[0]);
    document.getElementById("summary").textContent = on.length ? "Работает: " + on.join(", ")
      : "Всё выключено — нажмите на сервис, чтобы включить";
    const check = document.getElementById("check");
    check.disabled = !!s.checking;
    check.innerHTML = icon("refresh") + (s.checking ? "Проверка…" : "Проверить");
    const ping = document.getElementById("ping");
    ping.disabled = !!s.pinging;
    ping.textContent = s.pinging ? "Проверка…" : "Проверить пинг";
    hero(s.vpn);
    tile("tile-zapret", "Zapret", "shield", s.zapret);
    tile("tile-tg", "TG WS Proxy", "send", s.tg);
    document.getElementById("q-ru").checked = !!s.quick.ru;
    document.getElementById("q-ks").checked = !!s.quick.ks;
    servers(s.servers || []);
    conn(s.ip);
    avail(s.avail || []);
    updates(s.updates || {});
  }

  setInterval(tick, 1000);
  return { html, render };
})();
