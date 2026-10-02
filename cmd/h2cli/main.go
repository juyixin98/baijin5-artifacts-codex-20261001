// h2cli sends one predefined simple request over prior-knowledge HTTP/2 and
// prints the frames it exchanges. It exists so the service can be exercised
// from a clean checkout without external tooling.
package main

import (
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"time"

	"h2svc/internal/frame"
	"h2svc/internal/hpack"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:8080", "server address")
	path := flag.String("path", "/", "predefined route: /, /echo, /large")
	body := flag.String("body", "", "request body (only used with -path /echo)")
	timeout := flag.Duration("timeout", 10*time.Second, "overall deadline")
	flag.Parse()

	if err := run(*addr, *path, *body, *timeout); err != nil {
		log.Fatalf("h2cli: %v", err)
	}
}

func run(addr, path, body string, timeout time.Duration) error {
	nc, err := net.DialTimeout("tcp", addr, timeout)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer nc.Close()
	_ = nc.SetDeadline(time.Now().Add(timeout))

	if _, err := io.WriteString(nc, frame.ClientPreface); err != nil {
		return err
	}
	// Empty client SETTINGS.
	if _, err := nc.Write(mustFrame(frame.Settings, 0, 0, nil)); err != nil {
		return err
	}

	// HEADERS for the predefined request.
	var block []byte
	block = append(block, hpack.EncodeIndexed(2)...) // :method: GET
	if path == "/echo" && body != "" {
		block = block[:0]
		block = append(block, hpack.EncodeIndexed(3)...) // :method: POST
	}
	block = append(block, hpack.EncodeLiteralWithoutIndexing(":path", path)...)
	block = append(block, hpack.EncodeLiteralWithoutIndexing(":scheme", "http")...)
	block = append(block, hpack.EncodeLiteralWithoutIndexing(":authority", addr)...)

	flags := uint8(frame.FlagEndHeaders)
	if body == "" {
		flags |= frame.FlagEndStream
	}
	if _, err := nc.Write(mustFrame(frame.Headers, flags, 1, block)); err != nil {
		return err
	}
	if body != "" {
		if _, err := nc.Write(mustFrame(frame.Data, frame.FlagEndStream, 1, []byte(body))); err != nil {
			return err
		}
	}
	// Grant generous flow-control windows so large responses are not stalled
	// (the server correctly refuses to send beyond the default 65535).
	wu := []byte{0x40, 0x00, 0x00, 0x00} // increment 0x40000000 = 1 GiB
	for _, sid := range []uint32{0, 1} {
		if _, err := nc.Write(mustFrame(frame.WindowUpdate, 0, sid, wu)); err != nil {
			return err
		}
	}

	var respBody int
	var status string
	dec := hpack.NewDecoder()
	for {
		h, err := frame.ReadHeader(nc)
		if err != nil {
			return fmt.Errorf("read frame: %w", err)
		}
		payload := make([]byte, h.Length)
		if _, err := io.ReadFull(nc, payload); err != nil {
			return fmt.Errorf("read payload: %w", err)
		}
		fmt.Printf("<- %s\n", h)
		switch h.Type {
		case frame.Settings:
			if h.Flags&frame.FlagAck == 0 {
				if _, err := nc.Write(mustFrame(frame.Settings, frame.FlagAck, 0, nil)); err != nil {
					return err
				}
			}
		case frame.Headers:
			fields, err := dec.Decode(payload)
			if err != nil {
				return fmt.Errorf("decode response headers: %w", err)
			}
			for _, f := range fields {
				fmt.Printf("   %s\n", f)
				if f.Name == ":status" {
					status = f.Value
				}
			}
		case frame.Data:
			respBody += len(payload)
			if h.Flags&frame.FlagEndStream != 0 {
				fmt.Printf("status=%s body_bytes=%d\n", status, respBody)
				return nil
			}
		case frame.GoAway:
			return fmt.Errorf("server sent GOAWAY")
		}
	}
}

func mustFrame(t frame.Type, flags uint8, stream uint32, payload []byte) []byte {
	h := frame.Header{Length: uint32(len(payload)), Type: t, Flags: flags, StreamID: stream}
	hdr := h.Marshal()
	out := append(hdr[:], payload...)
	if len(out) > 1<<20 {
		fmt.Fprintln(os.Stderr, "frame too large")
		os.Exit(1)
	}
	return out
}
