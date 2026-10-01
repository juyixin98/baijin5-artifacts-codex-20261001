// Command echod is a standalone, half-close-aware TCP echo server used as a
// local, offline SOCKS5 target for manual checks and the smoke script. It is
// not part of the proxy itself.
package main

import (
	"flag"
	"io"
	"log"
	"net"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:0", "listen address")
	flag.Parse()

	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("echod listen: %v", err)
	}
	log.Printf("echod listening on %s", ln.Addr())
	for {
		c, err := ln.Accept()
		if err != nil {
			return
		}
		go serve(c)
	}
}

func serve(c net.Conn) {
	defer c.Close()
	tc, ok := c.(*net.TCPConn)
	if !ok {
		_, _ = io.Copy(c, c)
		return
	}
	buf := make([]byte, 32*1024)
	for {
		n, err := tc.Read(buf)
		if n > 0 {
			if _, werr := tc.Write(buf[:n]); werr != nil {
				return
			}
		}
		if err != nil {
			// Mirror the peer's half-close so directional EOF is observable.
			_ = tc.CloseWrite()
			_ = tc.CloseRead()
			return
		}
	}
}
