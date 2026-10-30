// Command bypasshub-awg — AmneziaWG для Linux-версии BypassHub.
//
// То же ядро, что в Android-версии (bhcore.StartAWG): туннель работает в пространстве
// пользователя (amneziawg-go + сетевой стек gVisor), без модуля ядра и без прав root,
// а наружу отдаётся локальный SOCKS5 на 127.0.0.1. В него BypassHub направляет трафик
// через sing-box. Первая строка вывода — «SOCKS5 127.0.0.1:<порт>».
//
//	bypasshub-awg <файл.conf>
package main

import (
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/Miffk/BypassHub/Android/core/bhcore"
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: bypasshub-awg <file.conf>")
		os.Exit(2)
	}
	conf, err := os.ReadFile(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	port, err := bhcore.StartAWG(string(conf))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Printf("SOCKS5 127.0.0.1:%d\n", port)
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	bhcore.StopAWG()
}
