// Command coapd starts the controlled local CoAP service.
//
// It loads a key=value config, seeds synthetic resources from a fixture
// document, opens SQLite and serves CoAP (RFC 7252/7959 subset) on
// loopback UDP. No real devices or accounts are involved.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"coaplab/internal/config"
	"coaplab/internal/diag"
	"coaplab/internal/fixtures"
	"coaplab/internal/service"
)

func main() {
	cfgPath := flag.String("config", "configs/coaplab.conf", "key=value config file")
	fixPath := flag.String("fixtures", "test/fixtures/data/resources.json", "synthetic fixture document")
	dbPath := flag.String("db", "", "override db_path")
	listen := flag.String("listen", "", "override listen address")
	quiet := flag.Bool("quiet", false, "suppress diagnostic output")
	flag.Parse()

	cfg, err := config.Load(*cfgPath)
	if err != nil {
		fatal("load config", err)
	}
	cfg.EnvOverride()
	if *dbPath != "" {
		cfg.DBPath = *dbPath
	}
	if *listen != "" {
		cfg.ListenAddr = *listen
	}
	if err := cfg.Validate(); err != nil {
		fatal("validate config", err)
	}

	seeds, err := loadSeeds(*fixPath)
	if err != nil {
		fatal("load seed data", err)
	}

	rec := diag.NewRecorder(os.Stderr)
	if *quiet {
		rec.Quiet()
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	app, err := service.New(cfg, seeds, rec)
	if err != nil {
		fatal("start service", err)
	}
	defer app.Close()

	serveErr := make(chan error, 1)
	go func() { serveErr <- app.Serve(ctx) }()

	logf("coapd started on %s (db=%s, preferred SZX=%d => %d bytes, block1 retention=%s)",
		app.Addr().String(), cfg.DBPath, cfg.PreferredSZX, 1<<(cfg.PreferredSZX+4), cfg.Block1Retention)
	logf("retransmission: ACK_TIMEOUT=%s ACK_RANDOM_FACTOR=%.2f MAX_RETRANSMIT=%d (max %d transmissions)",
		cfg.ACKTimeout, cfg.ACKRandomFactor, cfg.MaxRetransmit, cfg.MaxRetransmit+1)
	logf("seeded %d synthetic resources; payload contents are never logged (masked diagnostics)", len(seeds))

	select {
	case <-ctx.Done():
		logf("shutting down...")
	case err := <-serveErr:
		if err != nil {
			fatal("serve", err)
		}
	}
}

func loadSeeds(path string) ([]config.SeedResource, error) {
	doc, err := fixtures.Load(path)
	if err != nil {
		return nil, err
	}
	out := make([]config.SeedResource, 0, len(doc.Resources))
	for _, r := range doc.Resources {
		body, err := r.Body()
		if err != nil {
			return nil, err
		}
		out = append(out, config.SeedResource{
			Path: r.Path, ContentFormat: r.ContentFormat, Body: body,
		})
	}
	return out, nil
}

func logf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "%s %s\n", time.Now().Format("15:04:05"), fmt.Sprintf(format, args...))
}

func fatal(what string, err error) {
	fmt.Fprintf(os.Stderr, "coapd: %s: %v\n", what, err)
	os.Exit(1)
}
