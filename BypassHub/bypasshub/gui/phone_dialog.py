"""Окно «Подключить телефон»: открыть прокси для домашней сети и показать QR-код."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import customtkinter as ctk
from PIL import Image, ImageDraw

from .. import tgproxy
from . import theme as T
from . import widgets as W

if TYPE_CHECKING:
    from .app import App

QR_SIZE = 230


def qr_image(text: str, size: int) -> Image.Image:
    """Чёткий QR-код: каждый модуль — целое число пикселей, на белой скруглённой подложке."""
    import qrcode
    qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    pad = max(8, size // 14)
    module = max(1, (size - 2 * pad) // n)
    full = module * n + 2 * pad
    img = Image.new("RGBA", (full, full), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                x0, y0 = pad + x * module, pad + y * module
                d.rectangle([x0, y0, x0 + module - 1, y0 + module - 1], fill=(17, 19, 24, 255))
    img.putalpha(T.aa_mask(img.size, "rrect", pad * 1.2))
    return img


class PhoneDialog(ctk.CTkToplevel):
    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        P = W.P
        self.title("Подключить телефон")
        self.resizable(False, False)
        self.configure(fg_color=P.surface)
        self.transient(app)
        self.bind("<Escape>", lambda e: self.destroy())

        ctk.CTkLabel(self, text="Telegram на телефоне через этот компьютер", font=W.title_font(16),
                     anchor="w").pack(fill="x", padx=22, pady=(18, 2))
        ctk.CTkLabel(self, text="Телефон должен быть в той же Wi-Fi-сети, а компьютер — включён.",
                     anchor="w", text_color=P.muted).pack(fill="x", padx=22)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=22, pady=(12, 18))
        self._qr_img = None
        self.render()
        self.after(10, self._center)

    def _center(self) -> None:
        self.update_idletasks()
        x = self.app.winfo_rootx() + (self.app.winfo_width() - self.winfo_width()) // 2
        y = self.app.winfo_rooty() + (self.app.winfo_height() - self.winfo_height()) // 4
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        from .app import set_dark_titlebar
        set_dark_titlebar(self, W.P.mode == "dark")
        self.lift()
        self.focus_force()

    def restyle(self) -> None:
        self.render()

    # ------------------------------------------------------------------
    def render(self, server: Optional[str] = None) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        P = W.P
        tg = self.app.core.tg
        if not tg.is_installed():
            ctk.CTkLabel(self.body, text="tg-ws-proxy ещё не загружен.", text_color=W.YELLOW).pack(anchor="w")
            return
        if not tg.phone_access():
            ctk.CTkLabel(self.body, justify="left", anchor="w", wraplength=420, text=(
                "Сейчас прокси принимает подключения только с этого компьютера. Чтобы подключить телефон, "
                "BypassHub откроет его для домашней сети (адрес 0.0.0.0) и добавит правило в брандмауэр "
                "Windows. Выключить можно здесь же в любой момент.")).pack(fill="x")
            bar = ctk.CTkFrame(self.body, fg_color="transparent")
            bar.pack(fill="x", pady=(14, 0))
            W.GradientButton(bar, text="Разрешить доступ с телефона", command=self._enable).pack(side="left")
            W.add_button(bar, "Закрыть", self.destroy, secondary=True, width=110)
            return

        lan = tgproxy.lan_addresses()
        if not lan:
            ctk.CTkLabel(self.body, text="Не найден адрес компьютера в локальной сети. Подключите компьютер "
                                         "к роутеру (Wi-Fi или кабель).", text_color=W.RED,
                         wraplength=420, justify="left").pack(fill="x")
            return
        ips = [ip for ip, _ in lan]
        server = server if server in ips else ips[0]
        cfg = tg.load_config()
        link = tgproxy.phone_link(cfg, server)

        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x")
        qr = qr_image(link, T.px(QR_SIZE))
        self._qr_img = T.to_ctk(qr)
        ctk.CTkLabel(row, text="", image=self._qr_img).pack(side="left")
        steps = ctk.CTkFrame(row, fg_color="transparent")
        steps.pack(side="left", fill="both", expand=True, padx=(18, 0))
        for i, text in enumerate((
                "Откройте камеру телефона и наведите её на QR-код.",
                "Откройте ссылку — запустится Telegram.",
                "Нажмите «Подключить прокси».")):
            line = ctk.CTkFrame(steps, fg_color="transparent")
            line.pack(fill="x", pady=4)
            ctk.CTkLabel(line, text=str(i + 1), width=24, height=24, corner_radius=12, fg_color=P.accent,
                         text_color=P.on_accent, font=ctk.CTkFont(weight="bold")).pack(side="left", anchor="n")
            ctk.CTkLabel(line, text=text, justify="left", anchor="w", wraplength=210).pack(
                side="left", fill="x", padx=(8, 0))

        addr = ctk.CTkFrame(self.body, fg_color="transparent")
        addr.pack(fill="x", pady=(14, 0))
        ctk.CTkLabel(addr, text="Адрес компьютера:", anchor="w").pack(side="left")
        if len(ips) > 1:
            labels = [f"{ip}  ({name})" for ip, name in lan]
            box = ctk.CTkComboBox(addr, values=labels, state="readonly", width=300,
                                  command=lambda v: self.render(v.split()[0]))
            box.set(labels[ips.index(server)])
            box.pack(side="left", padx=(8, 0))
        else:
            ctk.CTkLabel(addr, text=server, font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(8, 0))
        vpns = tgproxy.vpn_adapters()
        if vpns:
            warn = ctk.CTkFrame(self.body, fg_color=T.mix(P.surface, "#f6c343", 0.16), corner_radius=10,
                                border_width=1, border_color=T.mix(P.surface, "#f6c343", 0.55))
            warn.pack(fill="x", pady=(12, 0))
            ctk.CTkLabel(warn, text="⚠  Включён VPN: " + ", ".join(vpns), anchor="w",
                         font=ctk.CTkFont(weight="bold")).pack(fill="x", padx=12, pady=(8, 0))
            ctk.CTkLabel(warn, anchor="w", justify="left", wraplength=450, text=(
                "VPN может не пропускать подключения из домашней сети — тогда телефон не подключится. "
                "Если так случилось: разрешите в настройках VPN доступ к локальной сети (обычно "
                "«Локальная сеть», «Allow LAN» или «Split tunneling»), либо временно выключите VPN. "
                "Адрес в QR-коде — от Wi-Fi/Ethernet, а не от VPN; если адресов несколько, выберите "
                "адрес вида 192.168.x.x.")).pack(fill="x", padx=12, pady=(2, 8))
        else:
            ctk.CTkLabel(self.body, text="Если телефон не подключается, проверьте, что он в той же Wi-Fi-сети "
                                         "(не в гостевой) и что брандмауэр не блокирует BypassHub.",
                         text_color=P.muted, font=ctk.CTkFont(size=11), anchor="w", justify="left",
                         wraplength=470).pack(fill="x", pady=(8, 0))

        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.pack(fill="x", pady=(14, 0))
        W.add_button(bar, "Скопировать ссылку", lambda: self._copy(link), width=170)
        W.add_button(bar, "Отключить доступ", self._disable, secondary=True, width=160)
        W.add_button(bar, "Закрыть", self.destroy, secondary=True, width=110)

    def _copy(self, link: str) -> None:
        self.app.copy_to_clipboard(link)
        self.app.info("Ссылка скопирована. Отправьте её себе в Telegram и откройте на телефоне.")

    def _enable(self) -> None:
        def done(_, err):
            if not self.winfo_exists():
                return
            if err:
                self.app.error(f"Не удалось открыть доступ: {err}")
            self.render()
            tg_page = self.app.pages.get("tg")
            if tg_page:
                tg_page[1].reload()
        self.app.run_task(lambda: self.app.core.tg.set_phone_access(True), done, "Доступ с телефона")

    def _disable(self) -> None:
        def done(_, err):
            if err:
                self.app.error(str(err))
            tg_page = self.app.pages.get("tg")
            if tg_page:
                tg_page[1].reload()
            if self.winfo_exists():
                self.destroy()
        self.app.run_task(lambda: self.app.core.tg.set_phone_access(False), done, "Доступ с телефона")
