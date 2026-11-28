"""Kill switch: если VPN неожиданно отключился, интернет мимо него закрыт.

Пока VPN работает, разрешено только то, что идёт через него: сам движок VPN (sing-box, клиент
AmneziaWG), трафик через адрес туннеля, адрес сервера VPN, локальная сеть и localhost. Всё
остальное исходящее блокируется — упал движок, и программы не уходят в интернет напрямую
с домашнего IP, а ждут, пока BypassHub переподключит VPN.

Windows — фильтры WFP (Windows Filtering Platform) в динамическом сеансе: Windows сама убирает
их, когда BypassHub закрывается (даже аварийно) и после перезагрузки, — остаться без интернета
«навсегда» нельзя. Linux — таблица nftables; её ставит помощник с правами root, и он же убирает
её, когда BypassHub закрывается.
"""
from __future__ import annotations

import ipaddress
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import winutil
from .log import log

# локальная сеть, широковещательные и групповые адреса (DHCP, принтеры, телефон для TG WS Proxy)
LAN_V4 = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
          "224.0.0.0/4", "255.255.255.255/32")
LAN_V6 = ("fe80::/10", "fc00::/7", "ff00::/8")

# тексты для окна (оба интерфейса)
KILLSWITCH_INTRO = ("Если VPN неожиданно отключится — упадёт движок, оборвётся связь, — программы не уйдут в интернет "
                    "напрямую с вашего настоящего IP: интернет мимо VPN закрыт, пока BypassHub не подключит VPN "
                    "снова (он делает это сам).")
STRICT_INTRO = ("Интернет закрыт и тогда, когда VPN выключен: без VPN не работает ничего, кроме локальной сети "
                "и самого BypassHub (чтобы можно было включить VPN, обновить подписку и проверить серверы).")
STRICT_DETAILS = ("Работает, пока открыт BypassHub (и свёрнутый в трей) — при выходе из BypassHub интернет снова "
                  "открыт. Включите VPN — и интернет пойдёт через него; выключите — снова закрыт. Подключён ли "
                  "при этом VPN в режиме «Весь трафик» — не важно: строгий режим работает в любом. Пока VPN "
                  "подключён в режиме «Весь трафик», дальше действует обычный kill switch.")

KILLSWITCH_DETAILS = ("Работает в режиме «Весь трафик», пока открыт BypassHub (и свёрнутый в трей). Локальная сеть "
                      "(принтер, телефон для TG WS Proxy) доступна всегда, а российские сайты и программы мимо VPN — "
                      "пока VPN подключён. Когда вы сами выключаете VPN или закрываете BypassHub, интернет снова "
                      "работает как обычно.")

NFT_TABLE = "bypasshub_killswitch"
# этой меткой sing-box помечает свои пакеты в Linux (route.default_mark) — их kill switch пропускает
LINUX_MARK = 0x4248
ZAPRET_MARK = 0x40000000  # linux.DESYNC_MARK: этой меткой nfqws помечает свои пакеты


@dataclass
class Plan:
    """Что пропускать, пока включён kill switch."""
    apps: List[Path] = field(default_factory=list)        # программы VPN (Windows)
    tunnel_addrs: List[str] = field(default_factory=list)  # адреса туннелей: через них — можно
    endpoints: List[str] = field(default_factory=list)     # IP серверов VPN
    tunnel_ifaces: List[str] = field(default_factory=list)  # имена интерфейсов туннеля (Linux)
    dns: bool = False   # пропускать запросы DNS (порт 53): VPN выключен, а адрес сервера нужно узнать

    def key(self) -> Tuple:
        return (tuple(str(p).lower() for p in self.apps), tuple(sorted(self.tunnel_addrs)),
                tuple(sorted(self.endpoints)), tuple(sorted(self.tunnel_ifaces)), self.dns)


def _split_ips(items: Iterable[str]) -> Tuple[List[ipaddress.IPv4Network], List[ipaddress.IPv6Network]]:
    v4: List[ipaddress.IPv4Network] = []
    v6: List[ipaddress.IPv6Network] = []
    for item in items:
        try:
            net = ipaddress.ip_network(str(item).strip(), strict=False)
        except ValueError:
            continue
        (v4 if net.version == 4 else v6).append(net)
    return v4, v6


def host_addr(cidr: str) -> str:
    """«172.19.0.1/30» → «172.19.0.1»: адрес самого интерфейса, а не сети."""
    return str(cidr).split("/", 1)[0].strip()


# ====================================================================== Linux (nftables)

def nft_rules(plan: Plan) -> str:
    v4, v6 = _split_ips(plan.endpoints)
    lines = [f"table inet {NFT_TABLE} {{",
             "  chain out {",
             "    type filter hook output priority filter - 10; policy accept;",
             '    oifname "lo" accept',
             f"    meta mark {LINUX_MARK:#x} accept",
             # пакеты, которые nfqws (zapret) отправляет вместо исходных, уже пропущенных выше
             f"    meta mark & {ZAPRET_MARK:#010x} != 0 accept"]
    ifaces = [i for i in plan.tunnel_ifaces if i]
    if ifaces:
        lines.append("    oifname { " + ", ".join(f'"{i}"' for i in ifaces) + " } accept")
    if v4:
        lines.append("    ip daddr { " + ", ".join(map(str, v4)) + " } accept")
    if v6:
        lines.append("    ip6 daddr { " + ", ".join(map(str, v6)) + " } accept")
    if plan.dns:
        lines += ["    udp dport 53 accept", "    tcp dport 53 accept"]
    lines += ["    ip daddr { " + ", ".join(LAN_V4) + " } accept",
              "    ip6 daddr { " + ", ".join(LAN_V6) + " } accept",
              "    meta l4proto tcp reject with tcp reset",
              "    reject",
              "  }",
              "}"]
    return "\n".join(lines) + "\n"


def _nft_script(plan: Plan) -> str:
    """Убрать прежнюю таблицу и поставить новую одной транзакцией nft."""
    return f"table inet {NFT_TABLE}\ndelete table inet {NFT_TABLE}\n" + nft_rules(plan)


class _LinuxBackend:
    def __init__(self) -> None:
        self._on = False

    def enable(self, plan: Plan) -> None:
        from . import linux
        if not linux.which("nft"):
            raise RuntimeError("Для kill switch нужен nftables: установите пакет nftables (sudo pacman -S nftables)")
        res = linux.helper().run(["nft", "-f", "-"], input=_nft_script(plan), timeout=30)
        if res["rc"] != 0:
            raise RuntimeError("nftables не принял правила kill switch: " + (res["out"].strip() or f"код {res['rc']}"))
        self._on = True

    def disable(self) -> None:
        if not self._on:
            return  # не включали в этом сеансе — не спрашиваем пароль администратора зря
        from . import linux
        linux.helper().run(["nft", "delete", "table", "inet", NFT_TABLE], timeout=30)
        self._on = False


def linux_cleanup_command() -> List[str]:
    """Чем помощник убирает правила, когда BypassHub закрывается."""
    return ["nft", "delete", "table", "inet", NFT_TABLE]


# ====================================================================== Windows (WFP)

if winutil.IS_WINDOWS:
    import ctypes
    import uuid
    from ctypes import POINTER, Structure, Union, byref, c_int, c_uint8, c_uint16, c_uint32, c_uint64, \
        c_void_p, c_wchar_p, c_ubyte

    class GUID(Structure):
        _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16), ("Data4", c_ubyte * 8)]

        @classmethod
        def of(cls, text: Optional[str] = None) -> "GUID":
            u = uuid.UUID(text) if text else uuid.uuid4()
            return cls.from_buffer_copy(u.bytes_le)

    class FWP_BYTE_BLOB(Structure):
        _fields_ = [("size", c_uint32), ("data", POINTER(c_ubyte))]

    class FWP_V4_ADDR_AND_MASK(Structure):
        _fields_ = [("addr", c_uint32), ("mask", c_uint32)]

    class FWP_V6_ADDR_AND_MASK(Structure):
        _fields_ = [("addr", c_ubyte * 16), ("prefixLength", c_ubyte)]

    class _FwpValueUnion(Union):
        _fields_ = [("uint8", c_uint8), ("uint16", c_uint16), ("uint32", c_uint32), ("uint64", POINTER(c_uint64)),
                    ("byteBlob", POINTER(FWP_BYTE_BLOB)), ("v4AddrMask", POINTER(FWP_V4_ADDR_AND_MASK)),
                    ("v6AddrMask", POINTER(FWP_V6_ADDR_AND_MASK)), ("ptr", c_void_p)]

    class FWP_VALUE0(Structure):  # он же FWP_CONDITION_VALUE0: такое же устройство
        _fields_ = [("type", c_int), ("u", _FwpValueUnion)]

    class FWPM_DISPLAY_DATA0(Structure):
        _fields_ = [("name", c_wchar_p), ("description", c_wchar_p)]

    class FWPM_FILTER_CONDITION0(Structure):
        _fields_ = [("fieldKey", GUID), ("matchType", c_int), ("conditionValue", FWP_VALUE0)]

    class FWPM_ACTION0(Structure):
        _fields_ = [("type", c_uint32), ("filterType", GUID)]

    class _FilterContext(Union):
        _fields_ = [("rawContext", c_uint64), ("providerContextKey", GUID)]

    class FWPM_FILTER0(Structure):
        _fields_ = [("filterKey", GUID), ("displayData", FWPM_DISPLAY_DATA0), ("flags", c_uint32),
                    ("providerKey", POINTER(GUID)), ("providerData", FWP_BYTE_BLOB), ("layerKey", GUID),
                    ("subLayerKey", GUID), ("weight", FWP_VALUE0), ("numFilterConditions", c_uint32),
                    ("filterCondition", POINTER(FWPM_FILTER_CONDITION0)), ("action", FWPM_ACTION0),
                    ("context", _FilterContext), ("reserved", POINTER(GUID)), ("filterId", c_uint64),
                    ("effectiveWeight", FWP_VALUE0)]

    class FWPM_SUBLAYER0(Structure):
        _fields_ = [("subLayerKey", GUID), ("displayData", FWPM_DISPLAY_DATA0), ("flags", c_uint32),
                    ("providerKey", POINTER(GUID)), ("providerData", FWP_BYTE_BLOB), ("weight", c_uint16)]

    class FWPM_SESSION0(Structure):
        _fields_ = [("sessionKey", GUID), ("displayData", FWPM_DISPLAY_DATA0), ("flags", c_uint32),
                    ("txnWaitTimeoutInMSec", c_uint32), ("processId", c_uint32), ("sid", c_void_p),
                    ("username", c_wchar_p), ("kernelMode", c_int)]

    FWP_UINT8, FWP_UINT32, FWP_BYTE_BLOB_TYPE = 1, 3, 12
    FWP_V4_ADDR_MASK, FWP_V6_ADDR_MASK = 0x100, 0x101
    FWP_MATCH_EQUAL, FWP_MATCH_FLAGS_ALL_SET = 0, 6
    FWP_ACTION_BLOCK, FWP_ACTION_PERMIT = 0x1001, 0x1002
    FWP_CONDITION_FLAG_IS_LOOPBACK = 0x1
    FWPM_SESSION_FLAG_DYNAMIC = 0x1
    RPC_C_AUTHN_WINNT = 10

    LAYER_CONNECT_V4 = "c38d57d1-05a7-4c33-904f-7fbceee60e82"   # FWPM_LAYER_ALE_AUTH_CONNECT_V4
    LAYER_CONNECT_V6 = "4a72393b-319f-44bc-84c3-ba54dcb3b6b4"   # FWPM_LAYER_ALE_AUTH_CONNECT_V6
    COND_APP_ID = "d78e1e87-8644-4ea5-9437-d809ecefc971"        # FWPM_CONDITION_ALE_APP_ID
    COND_LOCAL_ADDR = "d9ee00de-c1ef-4617-bfe3-ffd8f5a08957"    # FWPM_CONDITION_IP_LOCAL_ADDRESS
    COND_REMOTE_ADDR = "b235ae9a-1d64-49b8-a44c-5ff3d9095045"   # FWPM_CONDITION_IP_REMOTE_ADDRESS
    COND_FLAGS = "632ce23b-5167-435c-86d7-e903684aa80c"         # FWPM_CONDITION_FLAGS
    COND_REMOTE_PORT = "c35a604d-d22b-4e1a-91b4-68f674ee674b"   # FWPM_CONDITION_IP_REMOTE_PORT
    FWP_UINT16 = 2

    def _api():
        fw = ctypes.WinDLL("fwpuclnt.dll")
        H = c_void_p
        fw.FwpmEngineOpen0.argtypes = [c_wchar_p, c_uint32, c_void_p, POINTER(FWPM_SESSION0), POINTER(H)]
        fw.FwpmEngineClose0.argtypes = [H]
        fw.FwpmTransactionBegin0.argtypes = [H, c_uint32]
        fw.FwpmTransactionCommit0.argtypes = [H]
        fw.FwpmTransactionAbort0.argtypes = [H]
        fw.FwpmSubLayerAdd0.argtypes = [H, POINTER(FWPM_SUBLAYER0), c_void_p]
        fw.FwpmFilterAdd0.argtypes = [H, POINTER(FWPM_FILTER0), c_void_p, POINTER(c_uint64)]
        fw.FwpmGetAppIdFromFileName0.argtypes = [c_wchar_p, POINTER(POINTER(FWP_BYTE_BLOB))]
        fw.FwpmFreeMemory0.argtypes = [POINTER(c_void_p)]
        for name in ("FwpmEngineOpen0", "FwpmEngineClose0", "FwpmTransactionBegin0", "FwpmTransactionCommit0",
                     "FwpmTransactionAbort0", "FwpmSubLayerAdd0", "FwpmFilterAdd0", "FwpmGetAppIdFromFileName0"):
            getattr(fw, name).restype = c_uint32
        fw.FwpmFreeMemory0.restype = None
        return fw


class WfpError(RuntimeError):
    pass


def _check(code: int, what: str) -> None:
    if code:
        raise WfpError(f"{what}: ошибка WFP 0x{code & 0xFFFFFFFF:08X}")


class _WfpSession:
    """Один динамический сеанс WFP с нашими фильтрами. Закрыли сеанс — фильтров нет."""

    def __init__(self, plan: Plan) -> None:
        self._fw = _api()
        self._keep: List[Any] = []   # всё, на что ссылаются структуры, — живёт, пока идёт добавление
        self._app_ids: List[Any] = []
        self.filters: Dict[int, str] = {}  # id фильтра → что он делает (для разбора журнала WFP)
        self.handle = c_void_p()
        session = FWPM_SESSION0()
        session.displayData.name = "BypassHub kill switch"
        session.flags = FWPM_SESSION_FLAG_DYNAMIC
        _check(self._fw.FwpmEngineOpen0(None, RPC_C_AUTHN_WINNT, None, byref(session), byref(self.handle)),
               "открыть WFP")
        try:
            self._install(plan)
        except Exception:
            self.close()
            raise
        finally:
            self._free_app_ids()
            self._keep.clear()

    # ---------------------------------------------------------------- значения условий
    def _cond(self, field_key: str, match: int, vtype: int, **value: Any) -> "FWPM_FILTER_CONDITION0":
        c = FWPM_FILTER_CONDITION0()
        c.fieldKey = GUID.of(field_key)
        c.matchType = match
        c.conditionValue.type = vtype
        for k, v in value.items():
            setattr(c.conditionValue.u, k, v)
        return c

    def _v4(self, field_key: str, net: ipaddress.IPv4Network) -> "FWPM_FILTER_CONDITION0":
        m = FWP_V4_ADDR_AND_MASK(int(net.network_address), int(net.netmask))
        self._keep.append(m)
        return self._cond(field_key, FWP_MATCH_EQUAL, FWP_V4_ADDR_MASK, v4AddrMask=ctypes.pointer(m))

    def _v6(self, field_key: str, net: ipaddress.IPv6Network) -> "FWPM_FILTER_CONDITION0":
        m = FWP_V6_ADDR_AND_MASK()
        m.addr[:] = list(net.network_address.packed)
        m.prefixLength = net.prefixlen
        self._keep.append(m)
        return self._cond(field_key, FWP_MATCH_EQUAL, FWP_V6_ADDR_MASK, v6AddrMask=ctypes.pointer(m))

    def _app(self, path: Path) -> Optional["FWPM_FILTER_CONDITION0"]:
        blob = POINTER(FWP_BYTE_BLOB)()
        if self._fw.FwpmGetAppIdFromFileName0(str(path), byref(blob)) != 0 or not blob:
            log.warning("Kill switch: не удалось получить идентификатор программы %s", path)
            return None
        self._app_ids.append(blob)
        return self._cond(COND_APP_ID, FWP_MATCH_EQUAL, FWP_BYTE_BLOB_TYPE, byteBlob=blob)

    def _system(self) -> "FWPM_FILTER_CONDITION0":
        """Программа «System» — ядро Windows. От его имени уходят пакеты, которые драйвер WinDivert
        (zapret) перехватил и отправил заново: исходное соединение kill switch уже пропустил
        (его открыла разрешённая программа), а повторно отправленные пакеты Windows проверяет
        как новые — от «System». Без этого правила при включённом zapret не работает ничего."""
        data = (c_ubyte * 14)(*"System\0".encode("utf-16-le"))
        blob = FWP_BYTE_BLOB(len(data), ctypes.cast(data, POINTER(c_ubyte)))
        self._keep += [data, blob]
        return self._cond(COND_APP_ID, FWP_MATCH_EQUAL, FWP_BYTE_BLOB_TYPE, byteBlob=ctypes.pointer(blob))

    def _free_app_ids(self) -> None:
        for blob in self._app_ids:
            p = ctypes.cast(blob, c_void_p)
            self._fw.FwpmFreeMemory0(byref(p))
        self._app_ids.clear()

    # ---------------------------------------------------------------- фильтры
    def _filter(self, sublayer: "GUID", layer: str, name: str, weight: int, permit: bool,
                conds: List["FWPM_FILTER_CONDITION0"]) -> None:
        f = FWPM_FILTER0()
        f.filterKey = GUID.of()
        f.displayData.name = f"BypassHub kill switch: {name}"
        f.layerKey = GUID.of(layer)
        f.subLayerKey = sublayer
        f.weight.type = FWP_UINT8
        f.weight.u.uint8 = weight
        if conds:
            arr = (FWPM_FILTER_CONDITION0 * len(conds))(*conds)
            self._keep.append(arr)
            f.numFilterConditions = len(conds)
            f.filterCondition = ctypes.cast(arr, POINTER(FWPM_FILTER_CONDITION0))
        f.action.type = FWP_ACTION_PERMIT if permit else FWP_ACTION_BLOCK
        fid = c_uint64()
        _check(self._fw.FwpmFilterAdd0(self.handle, byref(f), None, byref(fid)), f"фильтр «{name}»")
        self.filters[fid.value] = f"{name} ({'v6' if layer == LAYER_CONNECT_V6 else 'v4'})"

    def _install(self, plan: Plan) -> None:
        fw, h = self._fw, self.handle
        _check(fw.FwpmTransactionBegin0(h, 0), "начать транзакцию WFP")
        try:
            sublayer = GUID.of()
            sl = FWPM_SUBLAYER0()
            sl.subLayerKey = sublayer
            sl.displayData.name = "BypassHub kill switch"
            sl.weight = 0xFFFF
            _check(fw.FwpmSubLayerAdd0(h, byref(sl), None), "подуровень WFP")

            apps = [c for c in (self._app(p) for p in plan.apps if Path(p).exists()) if c is not None]
            apps.append(self._system())
            tun4, tun6 = _split_ips(host_addr(a) for a in plan.tunnel_addrs)
            ep4, ep6 = _split_ips(plan.endpoints)
            lan4, lan6 = _split_ips(LAN_V4 + LAN_V6)
            loop = self._cond(COND_FLAGS, FWP_MATCH_FLAGS_ALL_SET, FWP_UINT32, uint32=FWP_CONDITION_FLAG_IS_LOOPBACK)
            for layer, tun, ep, lan, v in ((LAYER_CONNECT_V4, tun4, ep4, lan4, self._v4),
                                          (LAYER_CONNECT_V6, tun6, ep6, lan6, self._v6)):
                # чем больше вес, тем раньше проверяется; первое совпадение решает
                self._filter(sublayer, layer, "localhost", 15, True, [loop])
                self._filter(sublayer, layer, "программы VPN", 14, True, apps)
                if tun:
                    self._filter(sublayer, layer, "через туннель", 13, True, [v(COND_LOCAL_ADDR, n) for n in tun])
                if ep:
                    self._filter(sublayer, layer, "сервер VPN", 12, True, [v(COND_REMOTE_ADDR, n) for n in ep])
                self._filter(sublayer, layer, "локальная сеть", 11, True, [v(COND_REMOTE_ADDR, n) for n in lan])
                if plan.dns:
                    self._filter(sublayer, layer, "DNS", 10, True,
                                 [self._cond(COND_REMOTE_PORT, FWP_MATCH_EQUAL, FWP_UINT16, uint16=53)])
                self._filter(sublayer, layer, "всё остальное", 0, False, [])
            _check(fw.FwpmTransactionCommit0(h), "применить фильтры WFP")
        except Exception:
            fw.FwpmTransactionAbort0(h)
            raise

    def close(self) -> None:
        if self.handle:
            self._fw.FwpmEngineClose0(self.handle)
            self.handle = c_void_p()


class _WindowsBackend:
    def __init__(self) -> None:
        self._session: Optional[_WfpSession] = None

    def enable(self, plan: Plan) -> None:
        new = _WfpSession(plan)            # сначала новые фильтры, потом снимаем старые — без «дыры»
        old, self._session = self._session, new
        if old is not None:
            old.close()

    def disable(self) -> None:
        old, self._session = self._session, None
        if old is not None:
            old.close()


# ====================================================================== общее

class KillSwitch:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._backend: Any = (_WindowsBackend() if winutil.IS_WINDOWS
                              else _LinuxBackend() if winutil.IS_LINUX else None)
        self._plan: Optional[Plan] = None

    @property
    def supported(self) -> bool:
        return self._backend is not None

    @property
    def active(self) -> bool:
        return self._plan is not None

    def enable(self, plan: Plan) -> None:
        if self._backend is None:
            raise RuntimeError("Kill switch на этой системе не поддерживается")
        with self._lock:
            if self._plan is not None and self._plan.key() == plan.key():
                return
            self._backend.enable(plan)
            first = self._plan is None
            self._plan = plan
        if first:
            log.info("Kill switch включён: без VPN интернет закрыт")

    def disable(self) -> None:
        with self._lock:
            if self._plan is None:
                return
            try:
                self._backend.disable()
            finally:
                self._plan = None
        log.info("Kill switch выключен")
