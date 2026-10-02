// Command stunc performs one STUN Binding transaction against a local lab
// server and prints the reflected endpoint.
//
// Usage:
//
//	stunc -server 127.0.0.1:3478 -key labkey -db evidence/stunc.db -log evidence/stunc.jsonl
//
// Exit codes: 0 success; 2 protocol/STUN error response; 3 timeout; 4 local
// failure. The JSONL log plus SQLite row carry the full replay evidence.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"time"

	"stunlab/internal/client"
	"stunlab/internal/evidence"
	"stunlab/internal/store"
	"stunlab/internal/stun"
)

func main() {
	network := flag.String("net", "udp4", "socket network: udp4 or udp6")
	serverAddr := flag.String("server", "127.0.0.1:3478", "STUN server host:port")
	key := flag.String("key", "", "shared short-term MESSAGE-INTEGRITY key (empty disables)")
	timeout := flag.Duration("timeout", 1500*time.Millisecond, "overall transaction timeout")
	retries := flag.Int("retries", 2, "retransmissions after the first attempt")
	dbPath := flag.String("db", "evidence/stunc.db", "SQLite database path")
	logPath := flag.String("log", "evidence/stunc.jsonl", "JSONL event log path")
	note := flag.String("note", "", "free-text run annotation")
	flag.Parse()

	_ = os.MkdirAll(dirOf(*dbPath), 0o755)
	_ = os.MkdirAll(dirOf(*logPath), 0o755)
	st, err := store.Open(*dbPath)
	if err != nil {
		fatal(4, err.Error())
	}
	defer st.Close()
	lf, err := os.OpenFile(*logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		fatal(4, "open log: "+err.Error())
	}
	defer lf.Close()

	runID := evidence.NewRunID("stunc")
	lg := evidence.NewLogger(runID, "stunc", lf, st, *note)

	cl, err := client.Dial(context.Background(), client.Config{
		Network: *network, Key: []byte(*key), Timeout: *timeout,
		MaxAttempts: *retries + 1, Logger: lg, Store: st,
	})
	if err != nil {
		fatal(4, err.Error())
	}
	defer cl.Close()
	lg.Info("client_start", map[string]any{
		"local_addr": cl.LocalAddr().String(), "server": *serverAddr,
		"integrity": *key != "", "timeout": timeout.String(),
	})

	res, err := cl.Bind(context.Background(), *serverAddr)
	if err != nil {
		kind := stun.ErrorOf(err)
		switch kind {
		case stun.KindTimeout:
			fmt.Fprintf(os.Stderr, "stunc: TIMEOUT after %s: %v\n", timeout, err)
			os.Exit(3)
		case stun.KindIntegrity:
			fmt.Fprintf(os.Stderr, "stunc: INTEGRITY FAILURE: %v\n", err)
			os.Exit(2)
		default:
			fmt.Fprintf(os.Stderr, "stunc: ERROR (%s): %v\n", kind, err)
			os.Exit(4)
		}
	}
	if res.ErrorCode != 0 {
		fmt.Fprintf(os.Stderr, "stunc: STUN error %d %s (attempts=%d)\n",
			res.ErrorCode, res.Detail, res.Attempts)
		os.Exit(2)
	}
	fmt.Printf("run_id=%s txn=%x server=%s endpoint=%s:%d family=%s attempts=%d\n",
		runID, res.TxnID[:], res.Server, res.Endpoint.IP, res.Endpoint.Port,
		family(res.Endpoint.IP.String()), res.Attempts)
}

func family(ip string) string {
	if len(ip) > 4 && ip[0] == '[' {
		return "ipv6"
	}
	for i := 0; i < len(ip); i++ {
		if ip[i] == ':' {
			return "ipv6"
		}
		if ip[i] == '.' {
			return "ipv4"
		}
	}
	return "unknown"
}

func dirOf(p string) string {
	for i := len(p) - 1; i >= 0; i-- {
		if p[i] == '/' {
			return p[:i]
		}
	}
	return "."
}

func fatal(code int, msg string) {
	_, _ = os.Stderr.WriteString("stunc: " + msg + "\n")
	os.Exit(code)
}
