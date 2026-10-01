// smtpsink is a local-only SMTP sink: it accepts mail over SMTP and
// persists it to local disk (SQLite index + .eml files). It never
// relays or sends mail externally.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"

	"smtpsink/internal/config"
	"smtpsink/internal/server"
	"smtpsink/internal/storage"
)

func main() {
	configPath := flag.String("config", "configs/smtpsink.json", "path to JSON configuration")
	flag.Parse()

	cfg, err := config.Load(*configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "smtpsink: %v\n", err)
		os.Exit(2)
	}

	store, err := storage.OpenSQLite(cfg.StorageDir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "smtpsink: %v\n", err)
		os.Exit(1)
	}
	defer store.Close()

	logger := log.New(os.Stderr, "smtpsink ", log.LstdFlags|log.Lmsgprefix)
	srv := server.New(cfg, store, logger)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	logger.Printf("event=start listen=%s storage=%s domains=%v", cfg.Listen, cfg.StorageDir, cfg.LocalDomains)
	if err := srv.ListenAndServe(ctx); err != nil {
		logger.Printf("event=fatal err=%q", err)
		os.Exit(1)
	}
	logger.Printf("event=stop")
}
