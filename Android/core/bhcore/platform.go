package bhcore

// Реализация adapter.PlatformInterface sing-box для Android (по образцу libbox,
// но только с тем, что нужно BypassHub).

import (
	"context"
	"encoding/json"
	"net"
	"net/netip"
	"sync"
	"syscall"

	"github.com/sagernet/sing-box/adapter"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/option"
	tun "github.com/sagernet/sing-tun"
	"github.com/sagernet/sing/common/control"
	E "github.com/sagernet/sing/common/exceptions"
	"github.com/sagernet/sing/common/logger"
	"github.com/sagernet/sing/common/x/list"
)

type platformWrapper struct {
	iif            Platform
	networkManager adapter.NetworkManager
	myTunName      string
	myTunAddress   []netip.Addr
	monitor        *defaultInterfaceMonitor
}

var _ adapter.PlatformInterface = (*platformWrapper)(nil)

func (w *platformWrapper) Initialize(networkManager adapter.NetworkManager) error {
	w.networkManager = networkManager
	return nil
}

func (w *platformWrapper) UsePlatformAutoDetectInterfaceControl() bool { return true }

// AutoDetectInterfaceControl помечает сокет ядра, чтобы он шёл мимо VPN. BypassHub и так исключён
// из своего VPN (addDisallowedApplication), поэтому неудача protect не мешает — только пишем в журнал.
func (w *platformWrapper) AutoDetectInterfaceControl(fd int) error {
	if w.iif != nil && !w.iif.Protect(int32(fd)) {
		protectWarn.Do(func() { logf("VpnService.protect не удался — сокеты и так идут мимо VPN") })
	}
	return nil
}

var protectWarn sync.Once

func (w *platformWrapper) UsePlatformInterface() bool { return true }

// tunRequest — описание TUN для VpnService.Builder (Kotlin читает этот JSON).
type tunRequest struct {
	MTU            uint32   `json:"mtu"`
	Inet4Address   []string `json:"inet4Address"`
	Inet6Address   []string `json:"inet6Address"`
	Inet4Route     []string `json:"inet4Route"`
	Inet6Route     []string `json:"inet6Route"`
	DNSServers     []string `json:"dnsServers"`
	IncludePackage []string `json:"includePackage"`
	ExcludePackage []string `json:"excludePackage"`
	HTTPProxyHost  string   `json:"httpProxyHost,omitempty"`
	HTTPProxyPort  uint16   `json:"httpProxyPort,omitempty"`
}

func prefixes(p []netip.Prefix, v4 bool) []string {
	out := []string{}
	for _, it := range p {
		if it.Addr().Is4() == v4 {
			out = append(out, it.String())
		}
	}
	return out
}

func (w *platformWrapper) OpenInterface(options *tun.Options, platformOptions option.TunPlatformOptions) (tun.Tun, error) {
	if w.iif == nil {
		return nil, E.New("нет платформы")
	}
	routeRanges, err := options.BuildAutoRouteRanges(true)
	if err != nil {
		return nil, err
	}
	dnsServers, err := options.DNSServerAddress()
	if err != nil {
		return nil, err
	}
	req := tunRequest{
		MTU:            options.MTU,
		Inet4Address:   prefixes(options.Inet4Address, true),
		Inet6Address:   prefixes(options.Inet6Address, false),
		Inet4Route:     prefixes(routeRanges, true),
		Inet6Route:     prefixes(routeRanges, false),
		IncludePackage: options.IncludePackage,
		ExcludePackage: options.ExcludePackage,
	}
	for _, d := range dnsServers {
		req.DNSServers = append(req.DNSServers, d.String())
	}
	if platformOptions.HTTPProxy != nil && platformOptions.HTTPProxy.Enabled {
		req.HTTPProxyHost = platformOptions.HTTPProxy.Server
		req.HTTPProxyPort = platformOptions.HTTPProxy.ServerPort
	}
	data, _ := json.Marshal(req)
	fd, err := w.iif.OpenTun(string(data))
	if err != nil {
		return nil, err
	}
	if fd < 0 {
		// iOS: NEPacketTunnelProvider не отдаёт fd напрямую — находим utun среди своих дескрипторов
		if fd, err = findTunFD(); err != nil {
			return nil, err
		}
	}
	name, err := tunName(fd)
	if err != nil {
		return nil, E.Cause(err, "имя TUN")
	}
	options.Name = name
	options.InterfaceMonitor.RegisterMyInterface(name)
	dupFd, err := syscall.Dup(int(fd))
	if err != nil {
		return nil, E.Cause(err, "dup TUN")
	}
	options.FileDescriptor = dupFd
	w.myTunName = name
	w.myTunAddress = nil
	for _, p := range append(append([]netip.Prefix{}, options.Inet4Address...), options.Inet6Address...) {
		w.myTunAddress = append(w.myTunAddress, p.Addr())
	}
	return tun.New(*options)
}

func (w *platformWrapper) ProcessPlatformOptions(options option.TunPlatformOptions) error { return nil }

func (w *platformWrapper) UsePlatformDefaultInterfaceMonitor() bool { return true }

func (w *platformWrapper) CreateDefaultInterfaceMonitor(l logger.Logger) tun.DefaultInterfaceMonitor {
	w.monitor = &defaultInterfaceMonitor{w: w, logger: l}
	return w.monitor
}

// На iOS Go читает интерфейсы сам; на Android 11+ это запрещено — список даёт Kotlin.
func (w *platformWrapper) UsePlatformNetworkInterfaces() bool { return !C.IsIos }

// netInterface — интерфейс из InterfacesJSON (Kotlin: java.net.NetworkInterface + ConnectivityManager).
type netInterface struct {
	Name      string   `json:"name"`
	Index     int      `json:"index"`
	MTU       int      `json:"mtu"`
	Addresses []string `json:"addresses"`
	Type      string   `json:"type"` // wifi | cellular | ethernet | other
	DNS       []string `json:"dns"`
	Metered   bool     `json:"metered"`
	Up        bool     `json:"up"`
}

func (w *platformWrapper) NetworkInterfaces() ([]adapter.NetworkInterface, error) {
	if w.iif == nil {
		return nil, nil
	}
	var list []netInterface
	if err := json.Unmarshal([]byte(w.iif.InterfacesJSON()), &list); err != nil {
		return nil, E.Cause(err, "список интерфейсов")
	}
	var out []adapter.NetworkInterface
	seen := map[string]bool{}
	for _, it := range list {
		if seen[it.Name] || it.Name == "" {
			continue
		}
		seen[it.Name] = true
		var addrs []netip.Prefix
		for _, a := range it.Addresses {
			if p, err := netip.ParsePrefix(a); err == nil {
				addrs = append(addrs, p)
			}
		}
		flags := net.Flags(0)
		if it.Up {
			flags |= net.FlagUp | net.FlagRunning | net.FlagMulticast
		}
		typ, ok := C.StringToInterfaceType[it.Type]
		if !ok {
			typ = C.InterfaceTypeOther
		}
		out = append(out, adapter.NetworkInterface{
			Interface:  control.Interface{Index: it.Index, MTU: it.MTU, Name: it.Name, Addresses: addrs, Flags: flags},
			Type:       typ,
			DNSServers: it.DNS,
			Expensive:  it.Metered,
		})
	}
	return out, nil
}

func (w *platformWrapper) UnderNetworkExtension() bool              { return C.IsIos }
func (w *platformWrapper) NetworkExtensionIncludeAllNetworks() bool { return false }
func (w *platformWrapper) ClearDNSCache()                           {}
func (w *platformWrapper) RequestPermissionForWIFIState() error     { return nil }
func (w *platformWrapper) UsePlatformWIFIMonitor() bool             { return false }

func (w *platformWrapper) ReadWIFIState(ctx context.Context) adapter.WIFIState {
	return adapter.WIFIState{}
}

// Владелец соединения нам не нужен (правил по приложениям нет). Без этого sing-box на Android сам
// ищет процесс через netlink, получает «permission denied» и пишет это в журнал на каждое соединение.
func (w *platformWrapper) UsePlatformConnectionOwnerFinder() bool { return true }

func (w *platformWrapper) FindConnectionOwner(request *adapter.FindConnectionOwnerRequest) (*adapter.ConnectionOwner, error) {
	return &adapter.ConnectionOwner{UserId: -1}, nil
}

func (w *platformWrapper) UsePlatformNotification() bool { return false }

func (w *platformWrapper) SendNotification(notification *adapter.Notification) error { return nil }

func (w *platformWrapper) CancelNotification(identifier string, typeID int32) error { return nil }

func (w *platformWrapper) MyInterfaceAddress() []netip.Addr { return w.myTunAddress }

func (w *platformWrapper) UsePlatformNeighborResolver() bool { return false }

func (w *platformWrapper) StartNeighborMonitor(listener adapter.NeighborUpdateListener) error {
	return E.New("не поддерживается")
}

func (w *platformWrapper) CloseNeighborMonitor(listener adapter.NeighborUpdateListener) error {
	return nil
}

func (w *platformWrapper) UsePlatformShell() bool { return false }
func (w *platformWrapper) CheckPlatformShell() error {
	return E.New("не поддерживается")
}

func (w *platformWrapper) OpenShellSession(user *adapter.PlatformUser, command string, env []string, term string,
	rows int32, cols int32) (adapter.ShellSession, error) {
	return nil, E.New("не поддерживается")
}

func (w *platformWrapper) LookupUser(username string) (*adapter.PlatformUser, error) {
	return nil, E.New("не поддерживается")
}

func (w *platformWrapper) LookupSFTPServer() (string, error) {
	return "", E.New("не поддерживается")
}
func (w *platformWrapper) ReadSystemSSHHostKey() ([]byte, error) {
	return nil, E.New("не поддерживается")
}
func (w *platformWrapper) TailscaleHostname() string { return "" }
func (w *platformWrapper) UsePlatformBridge() bool   { return false }

func (w *platformWrapper) CreateBridge(options adapter.BridgeOptions) (adapter.BridgeSession, error) {
	return nil, E.New("не поддерживается")
}

func (w *platformWrapper) updateDefaultInterface(name string, index int32, expensive bool) {
	if w.monitor != nil {
		w.monitor.update(name, index, expensive)
	}
}

// ====================================================================== основная сеть

type defaultInterfaceMonitor struct {
	w            *platformWrapper
	logger       logger.Logger
	access       sync.Mutex
	callbacks    list.List[tun.DefaultInterfaceUpdateCallback]
	iface        *control.Interface
	myInterfaces []string
}

var _ tun.DefaultInterfaceMonitor = (*defaultInterfaceMonitor)(nil)

func (m *defaultInterfaceMonitor) Start() error { return nil }
func (m *defaultInterfaceMonitor) Close() error { return nil }

func (m *defaultInterfaceMonitor) DefaultInterface() *control.Interface {
	m.access.Lock()
	defer m.access.Unlock()
	return m.iface
}

func (m *defaultInterfaceMonitor) OverrideAndroidVPN() bool { return false }
func (m *defaultInterfaceMonitor) AndroidVPNEnabled() bool  { return false }

func (m *defaultInterfaceMonitor) RegisterCallback(callback tun.DefaultInterfaceUpdateCallback) *list.Element[tun.DefaultInterfaceUpdateCallback] {
	m.access.Lock()
	defer m.access.Unlock()
	return m.callbacks.PushBack(callback)
}

func (m *defaultInterfaceMonitor) UnregisterCallback(element *list.Element[tun.DefaultInterfaceUpdateCallback]) {
	m.access.Lock()
	defer m.access.Unlock()
	m.callbacks.Remove(element)
}

func (m *defaultInterfaceMonitor) RegisterMyInterface(name string) {
	m.access.Lock()
	defer m.access.Unlock()
	m.myInterfaces = append(m.myInterfaces, name)
}

func (m *defaultInterfaceMonitor) MyInterfaces() []string {
	m.access.Lock()
	defer m.access.Unlock()
	return m.myInterfaces
}

func (m *defaultInterfaceMonitor) update(name string, index int32, expensive bool) {
	if nm := m.w.networkManager; nm != nil {
		if err := nm.UpdateInterfaces(); err != nil && m.logger != nil {
			m.logger.Error(E.Cause(err, "update interfaces"))
		}
	}
	m.access.Lock()
	if index < 0 {
		m.iface = nil
		callbacks := m.callbacks.Array()
		m.access.Unlock()
		for _, cb := range callbacks {
			cb(nil, 0)
		}
		return
	}
	var newIface *control.Interface
	if nm := m.w.networkManager; nm != nil {
		if it, err := nm.InterfaceFinder().ByIndex(int(index)); err == nil {
			newIface = it
		}
	}
	if newIface == nil {
		newIface = &control.Interface{Index: int(index), Name: name}
	}
	old := m.iface
	m.iface = newIface
	callbacks := m.callbacks.Array()
	m.access.Unlock()
	if old != nil && old.Index == newIface.Index && old.Name == newIface.Name {
		return
	}
	logf("сеть: %s", name)
	for _, cb := range callbacks {
		cb(newIface, 0)
	}
}
