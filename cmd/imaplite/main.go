// Command imaplite runs the local, fixture-backed constrained IMAP service.
//
// It seeds SQLite from synthetic fixtures (no production accounts) and serves
// SELECT / FETCH / UID FETCH / STORE / EXPUNGE over plain TCP for local
// compatibility testing.
package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"imaplite/internal/auth"
	"imaplite/internal/ctl"
	"imaplite/internal/fixture"
	"imaplite/internal/imapwire"
	"imaplite/internal/server"
	"imaplite/internal/store"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:1143", "listen address")
	dbPath := flag.String("db", "", "SQLite database path (default: temp file)")
	dataDir := flag.String("data", fixture.DefaultDataDir(), "fixture data directory")
	ctlAddr := flag.String("ctl-addr", "127.0.0.1:1144", "loopback control address")
	maxLiteral := flag.Int("max-literal", imapwire.MaxLiteralDefault, "max literal bytes")
	flag.Parse()

	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))

	if *dbPath == "" {
		f, err := os.CreateTemp("", "imaplite-*.db")
		if err != nil {
			fatal(logger, "create temp db", err)
		}
		*dbPath = f.Name()
		_ = f.Close()
	}
	dsn := *dbPath
	_ = dsn

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	st, err := store.Open(ctx, *dbPath)
	if err != nil {
		fatal(logger, "open store", err)
	}
	defer st.Close()

	loaded, err := fixture.Load(*dataDir)
	if err != nil {
		fatal(logger, "load fixtures", err)
	}
	if err := loaded.Provision(st); err != nil {
		fatal(logger, "seed", err)
	}
	users := auth.NewUserStore()
	if err := loaded.ProvisionAccounts(users); err != nil {
		fatal(logger, "provision accounts", err)
	}

	srv := server.New(server.Config{
		Addr:   *addr,
		Store:  st,
		Users:  users,
		Logger: logger,
		Limits: server.Limits{MaxLiteral: *maxLiteral},
	})
	if err := srv.Listen(); err != nil {
		fatal(logger, "listen", err)
	}

	ctrl := ctl.New(*ctlAddr, adminAdapter{store: st, load: loaded}, logger)
	if err := ctrl.Listen(); err != nil {
		fatal(logger, "ctl listen", err)
	}

	fmt.Fprintf(os.Stderr, "IMAPlite listening on %s (db=%s, data=%s)\n", srv.Addr(), *dbPath, *dataDir)
	fmt.Fprintf(os.Stderr, "control on %s (INFO/ROTATE/RESEED)\n", ctrl.Addr())

	serveErr := make(chan error, 2)
	go func() { serveErr <- srv.Serve(ctx) }()
	go func() { serveErr <- ctrl.Serve(ctx) }()
	select {
	case <-ctx.Done():
		logger.Info("shutdown signal received")
		_ = srv.Close()
		_ = ctrl.Close()
		<-serveErr
	case err := <-serveErr:
		if err != nil {
			fatal(logger, "serve", err)
		}
	}
}

func fatal(logger *slog.Logger, step string, err error) {
	logger.Error("fatal", "step", step, "err", err.Error())
	fmt.Fprintf(os.Stderr, "fatal: %s: %v\n", step, err)
	os.Exit(1)
}
