// h2svc is the controlled HTTP/2 frame-processing service entry point.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"h2svc/internal/config"
	"h2svc/internal/journal"
	"h2svc/internal/server"
)

func main() {
	cfgPath := flag.String("config", "", "path to JSON config (defaults used when empty)")
	runID := flag.String("run-id", "", "run identity for the diagnostics journal (default: timestamp)")
	flag.Parse()

	cfg := config.Default()
	if *cfgPath != "" {
		var err error
		cfg, err = config.Load(*cfgPath)
		if err != nil {
			log.Fatalf("config: %v", err)
		}
	}
	if err := cfg.Validate(); err != nil {
		log.Fatalf("config: %v", err)
	}

	id := *runID
	if id == "" {
		id = fmt.Sprintf("run-%s", time.Now().UTC().Format("20060102T150405Z"))
	}
	cfgJSON, _ := json.Marshal(cfg)
	j, err := journal.Open(cfg.JournalPath, id, string(cfgJSON))
	if err != nil {
		log.Fatalf("journal: %v", err)
	}
	defer j.Close()

	srv := server.New(cfg, j)
	if err := srv.Listen(); err != nil {
		log.Fatalf("listen: %v", err)
	}
	log.Printf("h2svc %s listening on %s run_id=%s journal=%s",
		journal.Version, srv.Addr(), j.RunID(), cfg.JournalPath)

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	errCh := make(chan error, 1)
	go func() { errCh <- srv.Serve() }()

	select {
	case s := <-sig:
		log.Printf("signal %s: shutting down", s)
	case err := <-errCh:
		if err != nil {
			log.Fatalf("serve: %v", err)
		}
	}
	if err := srv.Close(); err != nil {
		log.Fatalf("close: %v", err)
	}
	log.Printf("h2svc stopped run_id=%s", j.RunID())
}
