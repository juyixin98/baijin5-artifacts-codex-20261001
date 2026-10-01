// Command stunc is a small STUN Binding client used to exercise the local
// test server. It prints the server-reflexive result and emits JSONL audit
// events to stderr (or a file) so runs are replayable.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"time"

	"localstun/internal/audit"
	"localstun/internal/client"
	"localstun/internal/stun"
	"localstun/internal/stunerror"
)

func main() {
	serverAddr := flag.String("server", "127.0.0.1:3478", "STUN server UDP address ([::1]:3478 for IPv6)")
	localAddr := flag.String("local", "", "pin local UDP address (e.g. [::1]:0)")
	key := flag.String("key", "localstun-test-key", "pre-shared HMAC key (empty: no integrity)")
	fingerprint := flag.Bool("fingerprint", true, "send/expect FINGERPRINT")
	timeout := flag.Duration("timeout", 2*time.Second, "per-request timeout")
	count := flag.Int("count", 1, "number of Binding requests")
	runID := flag.String("run-id", "", "audit run id (default generated)")
	flag.Parse()

	if *fingerprint && *key == "" {
		fmt.Fprintln(os.Stderr, "stunc: fingerprint requires a non-empty key")
		os.Exit(2)
	}

	srvUDP, err := net.ResolveUDPAddr("udp", *serverAddr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "stunc: resolve server: %v\n", err)
		os.Exit(2)
	}
	var localUDP *net.UDPAddr
	if *localAddr != "" {
		localUDP, err = net.ResolveUDPAddr("udp", *localAddr)
		if err != nil {
			fmt.Fprintf(os.Stderr, "stunc: resolve local: %v\n", err)
			os.Exit(2)
		}
	}

	run := *runID
	if run == "" {
		run = "client-" + time.Now().UTC().Format("20060102T150405.000000000Z")
	}
	var seq uint64
	enc := json.NewEncoder(os.Stderr)

	c, err := client.New(client.Config{
		ServerAddr:     srvUDP,
		LocalAddr:      localUDP,
		SharedKey:      []byte(*key),
		Fingerprint:    *fingerprint,
		Timeout:        *timeout,
		MaxOutstanding: 64,
		OnEvent: func(e client.Event) {
			seq++
			kind := ""
			if e.Kind != stunerror.KindUnknown {
				kind = e.Kind.String()
			}
			rec := audit.Record{
				RunID:     run,
				Seq:       seq,
				Timestamp: time.Now().UTC(),
				Component: "client",
				Event:     string(e.Type),
				Kind:      kind,
				TxID:      txHex(e.TxID),
				Detail:    e.Detail,
			}
			if e.Src != nil {
				rec.SrcAddr = e.Src.String()
			}
			_ = enc.Encode(rec)
		},
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "stunc: %v\n", err)
		os.Exit(1)
	}
	defer c.Close()

	failures := 0
	for i := 0; i < *count; i++ {
		ctx, cancel := context.WithTimeout(context.Background(), *timeout+time.Second)
		res, err := c.RoundTrip(ctx)
		cancel()
		if err != nil {
			failures++
			fmt.Fprintf(os.Stderr, "stunc: request %d FAILED: %v\n", i+1, err)
			continue
		}
		fmt.Printf("request %d: tx=%s xor-mapped=%s:%d rtt=%s verified=%v\n",
			i+1, txHex(res.TxID), res.IP, res.Port, res.RTT.Round(time.Microsecond), res.Verified)
	}
	if failures > 0 {
		os.Exit(1)
	}
}

func txHex(id stun.TransactionID) string {
	const hexd = "0123456789abcdef"
	out := make([]byte, 24)
	for i, b := range id {
		out[2*i] = hexd[b>>4]
		out[2*i+1] = hexd[b&0x0F]
	}
	return string(out)
}
