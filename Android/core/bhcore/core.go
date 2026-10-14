// Package bhcore — ядро BypassHub для Android: sing-box (VLESS, раздельное
// туннелирование, обход блокировок) и AmneziaWG в одной библиотеке.
//
// Kotlin-часть вызывает Setup → Start(config, platform) → Stop и реализует
// небольшой интерфейс Platform (открыть TUN через VpnService, защитить сокет,
// список сетевых интерфейсов). Всё остальное — здесь, на Go.
package bhcore

import (
	"context"
	"fmt"
	"os"
	"regexp"
	"sync"

	box "github.com/sagernet/sing-box"
	"github.com/sagernet/sing-box/adapter"
	"github.com/sagernet/sing-box/common/urltest"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/include"
	"github.com/sagernet/sing-box/log"
	"github.com/sagernet/sing-box/option"
	E "github.com/sagernet/sing/common/exceptions"
	"github.com/sagernet/sing/common/json"
	"github.com/sagernet/sing/service"
	"github.com/sagernet/sing/service/filemanager"
)

// Platform реализует Android-часть (VpnService).
type Platform interface {
	// OpenTun создаёт TUN через VpnService.Builder по описанию в JSON
	// (адреса, маршруты, DNS, MTU, исключённые приложения) и возвращает его fd.
	OpenTun(optionsJSON string) (int32, error)
	// Protect выводит сокет из-под VPN (VpnService.protect).
	Protect(fd int32) bool
	// InterfacesJSON — сетевые интерфейсы устройства (на Android 11+ Go не может прочитать их сам).
	InterfacesJSON() string
	// WriteLog — строка журнала для экрана «Журнал».
	WriteLog(message string)
}

var (
	mu          sync.Mutex
	instance    *box.Box
	cancelBox   context.CancelFunc
	current     *platformWrapper
	workingPath string
	tempPath    string
	logPlatform Platform
)

// Setup задаёт рабочие папки (файлы приложения) — вызывается один раз при запуске.
func Setup(working, temp string) error {
	workingPath, tempPath = working, temp
	if err := os.MkdirAll(working, 0o755); err != nil {
		return err
	}
	return os.MkdirAll(temp, 0o755)
}

// Version — версия встроенного sing-box.
func Version() string {
	return C.Version
}

func logf(format string, args ...any) {
	if p := logPlatform; p != nil {
		p.WriteLog(fmt.Sprintf(format, args...))
	}
}

func newContext(ctx context.Context) context.Context {
	ctx = filemanager.WithDefault(ctx, workingPath, tempPath, os.Getuid(), os.Getgid())
	return box.Context(ctx, include.InboundRegistry(), include.OutboundRegistry(), include.EndpointRegistry(),
		include.DNSTransportRegistry(), include.ServiceRegistry(), include.CertificateProviderRegistry())
}

func parse(ctx context.Context, config string) (option.Options, error) {
	options, err := json.UnmarshalExtendedContext[option.Options](ctx, []byte(config))
	if err != nil {
		return option.Options{}, E.Cause(err, "decode config")
	}
	return options, nil
}

// CheckConfig проверяет конфиг sing-box, ничего не запуская.
func CheckConfig(config string) error {
	ctx, cancel := context.WithCancel(newContext(context.Background()))
	defer cancel()
	options, err := parse(ctx, config)
	if err != nil {
		return err
	}
	service.MustRegister[adapter.PlatformInterface](ctx, &platformWrapper{})
	b, err := box.New(box.Options{Context: ctx, Options: options})
	if err == nil {
		_ = b.Close()
	}
	return err
}

// Start запускает sing-box с конфигом config. Перед этим остановит прежний запуск.
func Start(config string, platform Platform) error {
	mu.Lock()
	defer mu.Unlock()
	stopLocked()
	logPlatform = platform
	ctx, cancel := context.WithCancel(newContext(context.Background()))
	options, err := parse(ctx, config)
	if err != nil {
		cancel()
		return err
	}
	wrapper := &platformWrapper{iif: platform}
	service.MustRegister[adapter.PlatformInterface](ctx, wrapper)
	ctx = service.ContextWithPtr(ctx, urltest.NewHistoryStorage())
	b, err := box.New(box.Options{Context: ctx, Options: options, PlatformLogWriter: logWriter{platform}})
	if err != nil {
		cancel()
		return err
	}
	if err := b.Start(); err != nil {
		_ = b.Close()
		cancel()
		return err
	}
	instance, cancelBox, current = b, cancel, wrapper
	return nil
}

// Stop останавливает sing-box.
func Stop() {
	mu.Lock()
	defer mu.Unlock()
	stopLocked()
}

func stopLocked() {
	if instance != nil {
		_ = instance.Close()
		instance = nil
	}
	if cancelBox != nil {
		cancelBox()
		cancelBox = nil
	}
	current = nil
}

// IsRunning — запущен ли sing-box.
func IsRunning() bool {
	mu.Lock()
	defer mu.Unlock()
	return instance != nil
}

// UpdateDefaultInterface сообщает о смене основной сети (Wi-Fi ↔ мобильная).
// index = -1 — сети нет.
func UpdateDefaultInterface(name string, index int32, expensive bool) {
	mu.Lock()
	w := current
	mu.Unlock()
	if w != nil {
		w.updateDefaultInterface(name, index, expensive)
	}
}

type logWriter struct{ p Platform }

var ansi = regexp.MustCompile(`\x1b\[[0-9;]*m`)

func (l logWriter) WriteMessage(level log.Level, message string) {
	if l.p != nil {
		l.p.WriteLog(ansi.ReplaceAllString(message, ""))
	}
}
