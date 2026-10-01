// Command stund runs the controlled local STUN Binding test server.
//
// Minimal by design and intended for loopback UDP testing only: no TURN
// relay, no nonce-based long-term credential exchange; integrity uses a
// symmetric pre-shared HMAC key.
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"

	"localstun/internal/audit"
	"localstun/internal/server"
	"localstun/internal/store"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:3478", "UDP listen address (use [::1]:3478 for IPv6)")
	key := flag.String("key", "localstun-test-key", "pre-shared HMAC key (empty disables MESSAGE-INTEGRITY)")
	fingerprint := flag.Bool("fingerprint", true, "attach FINGERPRINT to responses")
	dbPath := flag.String("db", "results/audit.db", "SQLite audit database path (empty disables)")
	runID := flag.String("run-id", "", "audit run id (default: generated timestamp)")
	flag.Parse()

	var sink audit.Sink = audit.Noop
	if *dbPath != "" {
		sqliteSink, err := store.OpenSQLite(*dbPath)
		if err != nil {
			log.Fatalf("open audit db: %v", err)
		}
		sink = sqliteSink
		fmt.Fprintf(os.Stderr, "stund: audit database %s\n", *dbPath)
	}

	var sharedKey []byte
	if *key != "" {
		sharedKey = []byte(*key)
	}
	if *fingerprint && sharedKey == nil {
		log.Fatal("fingerprint requires a non-empty key")
	}

	srv, err := server.New(server.Config{
		ListenAddr:  *addr,
		SharedKey:   sharedKey,
		Fingerprint: *fingerprint,
		Sink:        sink,
		RunID:       *runID,
	})
	if err != nil {
		log.Fatalf("server config: %v", err)
	}
	errc := srv.Serve()
	fmt.Fprintf(os.Stderr, "stund: listening on %s (run=%s, integrity=%v, fingerprint=%v)\n",
		srv.Addr().String(), srv.RunID(), sharedKey != nil, *fingerprint)

	sigc := make(chan os.Signal, 1)
	signal.Notify(sigc, syscall.SIGINT, syscall.SIGTERM)
	select {
	case err := <-errc:
		if err != nil {
			log.Fatalf("server stopped: %v", err)
		}
	case sig := <-sigc:
		fmt.Fprintf(os.Stderr, "stund: received %s, shutting down\n", sig)
	}
	if err := srv.Close(); err != nil {
		log.Printf("close: %v", err)
	}
	if err := sink.Close(); err != nil {
		log.Printf("audit close: %v", err)
	}
	fmt.Fprintf(os.Stderr, "stund: stopped\n")
}
