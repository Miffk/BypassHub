// Страницы настроек из описания, которое присылает программа (page.blocks): шапка сервиса, разделы,
// переключатели, сегменты, списки, поля, кнопки. Действие — App.act(id, value).
"use strict";

window.Forms = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const attr = (o) => Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== false)
    .map(([k, v]) => (v === true ? k : `${k}="${esc(v)}"`)).join(" ");
  const help = (t) => (t ? `<span class="help" tabindex="0">?<span class="tip">${t}</span></span>` : "");
  const COLORS = { green: "var(--green)", yellow: "var(--yellow)", red: "var(--red)", muted: "var(--muted)", accent: "var(--accent)" };
  let open = {};  // раскрытые сворачиваемые разделы (ключ → да/нет)

  function button(b) {
    return `<button ${attr({ class: "btn" + (b.primary ? " primary" : "") + (b.danger ? " danger" : "") + (b.small ? " small" : ""),
      "data-act": b.id, "data-confirm": b.confirm, "data-arg": b.arg, "data-collect": b.collect, disabled: b.disabled,
      title: b.tip })}>${b.icon ? icon(b.icon) : ""}${esc(b.label)}</button>`;
  }

  function item(it) {
    switch (it.type) {
      case "toggle":
        return `<label class="switch-row ${it.disabled ? "disabled" : ""}"><span class="lbl">${esc(it.label)}${help(it.help)}
          ${it.hint ? `<small>${esc(it.hint)}</small>` : ""}</span>
          <input type="checkbox" class="switch" ${attr({ [it.local ? "data-field" : "data-toggle"]: it.id, checked: !!it.value,
            disabled: it.disabled, "data-confirm-on": it.confirmOn })}></label>`;
      case "segmented":
        return field(it, `<div class="seg" ${it.local ? `data-local="${esc(it.id)}"` : `data-seg="${esc(it.id)}"`}>${it.options.map((o) =>
          `<button class="${o === it.value ? "on" : ""}" data-value="${esc(o)}">${esc(o)}</button>`).join("")}</div>`);
      case "select":
        return field(it, `<div class="select"><select data-change="${esc(it.id)}">${it.options.map((o) =>
          `<option ${o === it.value ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>${icon("chevron")}</div>`);
      case "input":
        return field(it, `<div class="input-row"><input ${attr({ class: "input" + (it.mono ? " mono" : ""), "data-input": it.id,
          value: it.value ?? "", placeholder: it.placeholder, style: it.width ? `max-width:${it.width}px` : undefined,
          "data-submit": it.submit })}>${(it.buttons || []).map(button).join("")}</div>`);
      case "textarea":
        return field(it, `<textarea ${attr({ class: "input mono", "data-input": it.id, rows: it.rows || 5,
          placeholder: it.placeholder })}>${esc(it.value ?? "")}</textarea>`);
      case "slider":
        return field(it, `<div class="slider"><input type="range" ${attr({ "data-slide": it.id, min: it.min ?? 0, max: it.max ?? 100,
          value: it.value })}><b>${esc(it.value)}${esc(it.unit || "")}</b></div>`);
      case "buttons":
        return `<div class="btn-row">${it.items.map(button).join("")}</div>`;
      case "text":
        return `<p class="text ${it.style || ""}" ${it.color ? `style="color:${COLORS[it.color] || it.color}"` : ""}>${it.html ? it.text : esc(it.text)}</p>`;
      case "checks":
        return `<div class="checks">${it.options.map((o) => `<label class="check ${o.checked ? "on" : ""}">
          <input type="checkbox" ${attr({ "data-check": it.id, "data-value": o.value, checked: !!o.checked })}><span class="box">${icon("check")}</span>
          <span>${esc(o.label)}</span>${o.removable ? `<button class="x" data-act="${esc(it.removeAct || it.id + ".remove")}" data-arg="${esc(o.value)}" title="Убрать из списка">✕</button>` : ""}
          </label>`).join("")}</div>`;
      case "list":
        return `<div class="list ${it.wrap ? "wrap" : ""}" ${it.scroll ? `style="max-height:${it.scroll}px;overflow-y:auto"` : ""}>${
          it.rows.length ? it.rows.map(row).join("") : `<p class="text muted">${esc(it.empty || "Пусто")}</p>`}</div>`;
      case "swatches":
        return `<div class="swatches">${it.options.map((o) => `<button class="swatch ${o.selected ? "on" : ""}" data-act="${esc(it.id)}"
          data-arg="${esc(o.key)}" title="${esc(o.name)}"><span style="background:linear-gradient(135deg,${o.colors.join(",")})"></span>
          <small>${esc(o.name)}</small></button>`).join("")}</div>`;
      case "colors":
        return `<div class="colors">${it.colors.map((c, i) => `<label class="color"><input type="color" value="${esc(c)}"
          data-color="${esc(it.id)}" data-index="${i}"><span style="background:${esc(c)}"></span><code>${esc(c)}</code>
          ${it.colors.length > 1 ? `<button class="x" data-act="${esc(it.id)}.remove" data-arg="${i}">✕</button>` : ""}</label>`).join("")}
          ${it.colors.length < 3 ? `<button class="btn small" data-act="${esc(it.id)}.add">${icon("plus")}Цвет</button>` : ""}</div>`;
      case "hotkeys":
        return `<div class="hotkeys">${it.rows.map((r) => `<div class="hk"><span>${esc(r.label)}</span>
          <button class="key ${r.value ? "" : "empty"}" data-hotkey="${esc(r.id)}">${esc(r.value || "не задано")}</button>
          ${r.value ? `<button class="x" data-act="hotkey.clear" data-arg="${esc(r.id)}">✕</button>` : ""}</div>`).join("")}</div>`;
      case "row":
        return `<div class="row-line"><span class="row-label">${esc(it.label)}${help(it.help)}</span>
          <div class="row-items">${it.items.map(item).join("")}</div></div>`;
      case "log":
        return `<pre class="log" ${it.height ? `style="height:${it.height}px;min-height:0"` : `id="log"`}>${esc(it.text)}</pre>`;
      case "progress":
        return `<div class="progress"><span style="width:${Math.round((it.value || 0) * 100)}%"></span></div>`;
      case "qr":
        return `<div class="qr"><img src="${it.src}" alt="QR-код"><ol>${(it.steps || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol></div>`;
      case "image":
        return `<div class="qr"><img src="${it.src}" alt=""><p class="text muted">${esc(it.caption || "")}</p></div>`;
      default:
        return "";
    }
  }

  function field(it, control) {
    if (!it.label) return control;
    return `<div class="row-line"><span class="row-label">${esc(it.label)}${help(it.help)}</span><div class="row-items">${control}</div></div>
      ${it.hint ? `<p class="text small">${esc(it.hint)}</p>` : ""}`;
  }

  function row(r) {
    return `<div class="lrow ${r.active ? "active" : ""} ${r.click ? "clickable" : ""}" ${attr({ "data-act": r.click, "data-arg": r.arg })}>
      ${r.swatch ? `<div class="flag" style="background:linear-gradient(135deg,${r.swatch.join(",")})"></div>` :
        r.code !== undefined ? `<div class="flag" ${r.rightColor && !r.right ? `style="color:${COLORS[r.rightColor]}"` : ""}>${r.icon ? icon(r.icon) : esc(r.code)}</div>` : ""}
      <div class="grow"><div class="name">${esc(r.title)}${r.badge ? `<span class="tag">${esc(r.badge)}</span>` : ""}</div>
        ${r.subtitle ? `<div class="kind">${esc(r.subtitle)}</div>` : ""}</div>
      ${r.right ? `<span class="right" ${r.rightColor ? `style="color:${COLORS[r.rightColor] || r.rightColor}"` : ""}>${esc(r.right)}</span>` : ""}
      ${(r.actions || []).map((a) => `<button ${attr({ class: a.primary ? "btn small primary" : (a.label ? "btn small" : "icon-btn"),
        "data-act": a.id, "data-arg": a.arg ?? r.id, "data-confirm": a.confirm, title: a.tip, disabled: a.disabled })}>${a.icon ? icon(a.icon) : ""}${esc(a.label || "")}</button>`).join("")}
    </div>`;
  }

  function service(b) {
    const color = COLORS[b.statusColor] || "var(--muted)";
    return `<section class="card service ${b.on ? "on" : ""}">
      <div class="squircle">${icon(b.icon || "power")}</div>
      <div class="svc-body"><div class="svc-head"><h2 class="svc-title">${esc(b.title)}</h2>
        <span class="svc-status" style="color:${color}"><i style="background:${color}"></i>${esc(b.status)}</span></div>
        ${b.subtitle ? `<p class="text muted">${esc(b.subtitle)}</p>` : ""}
        ${b.details ? `<p class="text small">${esc(b.details)}</p>` : ""}
        ${b.buttons && b.buttons.length ? `<div class="btn-row">${b.buttons.map(button).join("")}</div>` : ""}</div>
      <input type="checkbox" class="switch big" data-toggle="${esc(b.id)}.power" ${b.on ? "checked" : ""} ${b.busy ? "disabled" : ""}>
    </section>`;
  }

  function section(b) {
    const key = b.key || b.title;
    const isOpen = !b.collapsible || (open[key] ?? !!b.open);
    return `<section class="card section ${b.collapsible ? "collapsible" : ""} ${isOpen ? "open" : ""}" data-key="${esc(key)}">
      <div class="sec-head" ${b.collapsible ? `data-fold="${esc(key)}"` : ""}>
        ${b.collapsible ? `<span class="chev">${icon("chevron")}</span>` : ""}<h2>${esc(b.title)}</h2>${help(b.help)}
        ${b.note ? `<span class="note">${esc(b.note)}</span>` : ""}</div>
      ${isOpen ? `<div class="sec-body">${b.hint ? `<p class="text muted">${esc(b.hint)}</p>` : ""}${(b.items || []).map(item).join("")}</div>` : ""}
    </section>`;
  }

  function block(b) {
    if (b.type === "service") return service(b);
    if (b.type === "section") return section(b);
    if (b.type === "group") return `<div class="group-title">${esc(b.title)}</div>`;
    if (b.type === "notice") return `<div class="notice ${b.kind || "warn"}">${icon(b.kind === "info" ? "news" : "shield")}<span>${esc(b.text)}</span></div>`;
    if (b.type === "grid") return `<div class="grid">${b.blocks.map(block).join("")}</div>`;
    return "";
  }

  function render(page) {
    return `<header class="page-head"><div><h1>${esc(page.title)}</h1>${page.subtitle ? `<p class="summary">${esc(page.subtitle)}</p>` : ""}</div>
      <div class="btn-row">${(page.actions || []).map(button).join("")}</div></header>` + page.blocks.map(block).join("");
  }

  // окно поверх страницы (подбор стратегии, диагностика, телефон)
  function sheet(sh) {
    if (!sh) return "";
    return `<div class="sheet-wrap"><div class="sheet card"><div class="sheet-head"><h2>${esc(sh.title)}</h2>
      <button class="icon-btn" data-act="sheet.close" title="Закрыть">✕</button></div>
      <div class="sec-body">${sh.items.map(item).join("")}</div></div></div>`;
  }

  function toggleFold(key) { open[key] = !(open[key] ?? false); }
  function setOpen(map) { open = Object.assign({}, map || {}); }
  function openMap() { return open; }
  return { render, sheet, toggleFold, setOpen, openMap };
})();
