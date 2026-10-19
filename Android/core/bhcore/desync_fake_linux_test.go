package bhcore

import (
	"io"
	"net"
	"testing"
	"time"
)

// Подставная часть уходит из нашей страницы памяти без копирования: в петле получатель держит ту же
// страницу и, читая позже, видит уже настоящие данные. Именно их ядро и отправит повтором в настоящей сети.
func TestFakeSend(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	got := make(chan []byte, 1)
	go func() {
		c, err := ln.Accept()
		if err != nil {
			return
		}
		defer c.Close()
		time.Sleep(200 * time.Millisecond) // читаем уже после подмены
		c.SetDeadline(time.Now().Add(3 * time.Second))
		b := make([]byte, 10)
		io.ReadFull(c, b)
		got <- b
	}()
	c, err := net.Dial("tcp", ln.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	ok, err := sendFake(c.(*net.TCPConn), []byte("REAL1"), []byte("FAKE1"))
	if err != nil || !ok {
		t.Fatalf("подставная часть не отправлена: %v %v", ok, err)
	}
	if _, err := c.Write([]byte("PART2")); err != nil {
		t.Fatal(err)
	}
	if b := <-got; string(b) != "REAL1PART2" {
		t.Fatalf("сервер получил %q", b)
	}
}

func TestFakePart(t *testing.T) {
	first := []byte("\x16\x03\x01..www.youtu")
	fake := fakePart(first, 5)
	if len(fake) != len(first) || string(fake[:5]) != string(first[:5]) || string(fake[5:]) == "www.youtu" ||
		fake[8] != '.' {
		t.Fatalf("подставная часть %q", fake)
	}
}
