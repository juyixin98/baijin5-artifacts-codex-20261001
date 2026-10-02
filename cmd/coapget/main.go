// Command coapget is a small diagnostic client for the local CoAP subset.
//
// It performs a reliable CON exchange and, for large representations, a
// full Block2 download or Block1 upload. It prints a MASKED diagnostic line
// (identifiers + length + SHA-256, never raw payload unless --show is given
// for text). Examples:
//
//	go run ./cmd/coapget -addr 127.0.0.1:5683 get /hello
//	go run ./cmd/coapget -addr 127.0.0.1:5683 get /cd/3kb
//	go run ./cmd/coapget -addr 127.0.0.1:5683 -file ./some.txt put /demo
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"flag"
	"fmt"
	"net"
	"os"
	"strings"
	"time"

	"coaplab/internal/engine"
	"coaplab/internal/transport"
	"coaplab/internal/wire"
)

func resolve(addr string) (*net.UDPAddr, error) {
	return net.ResolveUDPAddr("udp", addr)
}

// trimSlash strips a single leading slash so "/hello" addresses resource
// path "hello" (Uri-Path has no leading slash on the wire).
func trimSlash(p string) string { return strings.TrimPrefix(p, "/") }

func main() {
	addr := flag.String("addr", "127.0.0.1:5683", "server address")
	file := flag.String("file", "", "file to upload (put)")
	show := flag.Bool("show", false, "print the received body verbatim (text fixtures only)")
	szx := flag.Int("szx", 6, "proposed block exponent 0..6 (16..1024 bytes)")
	flag.Parse()

	args := flag.Args()
	if len(args) < 1 {
		usage()
	}
	server, err := resolve(*addr)
	if err != nil {
		fatal(err)
	}

	c, err := transport.DialClient(transport.ClientOptions{
		Retransmit: transport.Retransmit{
			ACKTimeout: 200 * time.Millisecond, ACKRandomFactor: 1.0, MaxRetransmit: 3,
		},
	})
	if err != nil {
		fatal(err)
	}
	defer c.Close()
	bc := engine.NewBlockwiseClient(c)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	switch args[0] {
	case "get":
		if len(args) < 2 {
			usage()
		}
		dl, err := bc.Download(ctx, server, trimSlash(args[1]), uint8(*szx))
		if err != nil {
			fatal(err)
		}
		report("GET", args[1], dl.Body, dl.ETag, dl.Exchanges, dl.SZX, *show)
	case "put":
		if len(args) < 2 || *file == "" {
			usage()
		}
		body, err := os.ReadFile(*file)
		if err != nil {
			fatal(err)
		}
		up, err := bc.Upload(ctx, server, trimSlash(args[1]), wire.PUT, wire.CFOctetStream, body, uint8(*szx))
		if err != nil {
			fatal(err)
		}
		fmt.Printf("PUT %s -> %s, exchanges=%d, negotiated SZX=%d (%d bytes), %d body bytes\n",
			args[1], up.FinalCode, up.Exchanges, up.NegotiatedSZX,
			1<<(up.NegotiatedSZX+4), len(body))
	default:
		usage()
	}
}

func report(method, path string, body, et []byte, exchanges int, szx uint8, show bool) {
	sum := sha256.Sum256(body)
	fmt.Printf("%s %s: %d bytes in %d block exchanges (SZX=%d, %d-byte blocks)\n",
		method, path, len(body), exchanges, szx, 1<<(szx+4))
	fmt.Printf("  ETag   : 0x%s\n", hex.EncodeToString(et))
	fmt.Printf("  SHA-256: %s\n", hex.EncodeToString(sum[:]))
	if show {
		fmt.Printf("  Body   : %q\n", body)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: coapget [-addr host:port] [-show] [-szx n] get /path | put /path -file f")
	os.Exit(2)
}

func fatal(v any) {
	fmt.Fprintf(os.Stderr, "coapget: %v\n", v)
	os.Exit(1)
}
