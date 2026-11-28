"""Кирпичики описания страниц (то, что рисует webui/forms.js) и ответы на действия."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

Block = Dict[str, Any]


def page(title: str, blocks: List[Block], subtitle: str = "", actions: Optional[List[Block]] = None) -> Block:
    return {"title": title, "subtitle": subtitle, "blocks": [b for b in blocks if b], "actions": actions or []}


def service(sid: str, title: str, icon: str, on: bool, status: str, color: str = "muted", subtitle: str = "",
            details: str = "", busy: bool = False, buttons: Optional[List[Block]] = None) -> Block:
    return {"type": "service", "id": sid, "title": title, "icon": icon, "on": on, "status": status,
            "statusColor": color, "subtitle": subtitle, "details": details, "busy": busy, "buttons": buttons or []}


def section(title: str, items: List[Block], help: str = "", hint: str = "", collapsible: bool = False,
            key: str = "", note: str = "") -> Block:
    return {"type": "section", "title": title, "items": [i for i in items if i], "help": help, "hint": hint,
            "collapsible": collapsible, "key": key or title, "note": note}


def group(title: str) -> Block:
    return {"type": "group", "title": title}


def notice(text: str, kind: str = "warn") -> Block:
    return {"type": "notice", "text": text, "kind": kind}


def toggle(tid: str, label: str, value: bool, help: str = "", hint: str = "", disabled: bool = False,
           confirm_on: str = "", local: bool = False) -> Block:
    """local — часть формы: не отправляется сразу, а вместе с кнопкой collect."""
    return {"type": "toggle", "id": tid, "label": label, "value": bool(value), "help": help, "hint": hint,
            "disabled": disabled, "confirmOn": confirm_on or None, "local": local}


def segmented(sid: str, options: List[str], value: str, label: str = "", help: str = "",
              local: bool = False) -> Block:
    return {"type": "segmented", "id": sid, "options": list(options), "value": value, "label": label, "help": help,
            "local": local}


def select(sid: str, options: List[str], value: str, label: str = "", help: str = "") -> Block:
    return {"type": "select", "id": sid, "options": list(options), "value": value, "label": label, "help": help}


def field(fid: str, value: str = "", label: str = "", placeholder: str = "", mono: bool = False,
          buttons: Optional[List[Block]] = None, help: str = "", hint: str = "", width: int = 0) -> Block:
    return {"type": "input", "id": fid, "value": value, "label": label, "placeholder": placeholder, "mono": mono,
            "buttons": buttons or [], "help": help, "hint": hint, "width": width or None}


def textarea(tid: str, value: str = "", placeholder: str = "", rows: int = 5, label: str = "") -> Block:
    return {"type": "textarea", "id": tid, "value": value, "placeholder": placeholder, "rows": rows, "label": label}


def button(bid: str, label: str, primary: bool = False, icon: str = "", confirm: str = "", arg: Any = None,
           disabled: bool = False, danger: bool = False, small: bool = False, tip: str = "",
           collect: bool = False) -> Block:
    """collect — передать в действие значения всех полей, сегментов и переключателей своего раздела
    (форма с кнопкой «Сохранить»)."""
    return {"id": bid, "label": label, "primary": primary, "icon": icon, "confirm": confirm or None, "arg": arg,
            "disabled": disabled, "danger": danger, "small": small, "tip": tip or None, "collect": collect}


def buttons(*items: Block) -> Block:
    return {"type": "buttons", "items": [b for b in items if b]}


def text(value: str, style: str = "", color: str = "") -> Block:
    return {"type": "text", "text": value, "style": style, "color": color or None}


def checks(cid: str, options: List[Block], remove_act: str = "") -> Block:
    return {"type": "checks", "id": cid, "options": options, "removeAct": remove_act or None}


def listing(lid: str, rows: List[Block], empty: str = "") -> Block:
    return {"type": "list", "id": lid, "rows": rows, "empty": empty}


def row(label: str, *items: Block, help: str = "") -> Block:
    return {"type": "row", "label": label, "items": [i for i in items if i], "help": help}


# ------------------------------------------------------------------ ответы на действия

def done(toast: str = "", **extra: Any) -> Dict[str, Any]:
    res: Dict[str, Any] = {"toast": toast} if toast else {}
    res.update(extra)
    return res


def error(message: str, title: str = "") -> Dict[str, Any]:
    return {"error": message, "title": title or None}


def info(message: str, title: str = "") -> Dict[str, Any]:
    return {"info": message, "title": title or None}


def ask(question: str, then: str, value: Any = None, title: str = "") -> Dict[str, Any]:
    """Спросить и, если человек согласен, выполнить действие then (со значением value)."""
    return {"ask": question, "then": then, "value": value, "title": title or None}
