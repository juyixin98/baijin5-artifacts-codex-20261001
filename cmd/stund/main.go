// Command stund is the controlled local STUN Binding responder.
//
// Usage:
//
//	stund -addr 127.0.0.1:3478 -key labkey -db evidence/stund.db -log evidence/stund.jsonl
//
// It serves Binding only (no TURN relay), and records every exchange in both
// JSONL and SQLite.
package main

import (
	"context"
	"flag"
	"os"
	"os/signal"
	"syscall"
	"time"

	"stunlab/internal/evidence"
	"stunlab/internal/server"
	"stunlab/internal/store"
)

func main() {
	network := flag.String("net", "udp4", "listener network: udp4 or udp6")
	addr := flag.String("addr", "127.0.0.1:3478", "listen address (use [::1]:3478 for IPv6)")
	key := flag.String("key", "", "shared short-term MESSAGE-INTEGRITY key (empty disables)")
	dbPath := flag.String("db", "evidence/stund.db", "SQLite database path")
	logPath := flag.String("log", "evidence/stund.jsonl", "JSONL event log path")
	note := flag.String("note", "", "free-text run annotation")
	flag.Parse()

	if err := os.MkdirAll(dirOf(*dbPath), 0o755); err != nil {
		fatal("mkdir db: " + err.Error())
	}
	if err := os.MkdirAll(dirOf(*logPath), 0o755); err != nil {
		fatal("mkdir log: " + err.Error())
	}
	st, err := store.Open(*dbPath)
	if err != nil {
		fatal(err.Error())
	}
	defer st.Close()
	lf, err := os.OpenFile(*logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		fatal("open log: " + err.Error())
	}
	defer lf.Close()

	runID := evidence.NewRunID("stund")
	lg := evidence.NewLogger(runID, "stund", lf, st, *note)
	lg.Info("server_starting", map[string]any{
		"network": *network, "addr": *addr, "integrity": *key != "",
		"argv": os.Args,
	})

	srv, err := server.Listen(server.Config{
		Network: *network, Addr: *addr, Key: []byte(*key), Logger: lg, Store: st,
	})
	if err != nil {
		fatal(err.Error())
	}
	defer srv.Close()
	lg.Info("server_listening", map[string]any{
		"bound_addr": srv.LocalAddr().String(), "started_at": time.Now().UTC().Format(time.RFC3339Nano),
	})

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	if err := srv.Serve(ctx); err != nil {
		fatal(err.Error())
	}
	lg.Info("server_stopped", map[string]any{"run_id": runID})
}

func dirOf(p string) string {
	for i := len(p) - 1; i >= 0; i-- {
		if p[i] == '/' {
			return p[:i]
		}
	}
	return "."
}

func fatal(msg string) {
	_, _ = os.Stderr.WriteString("stund: " + msg + "\n")
	os.Exit(1)
}
