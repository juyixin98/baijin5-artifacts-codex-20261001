// Command berserv runs the restricted BER codec HTTP service.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"berconf/internal/config"
	"berconf/internal/logx"
	"berconf/internal/server"
	"berconf/internal/store"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "fatal: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	cfgPath := flag.String("config", "configs/config.json", "path to JSON config (empty = built-in defaults)")
	flag.Parse()

	cfg, err := config.Load(*cfgPath)
	if err != nil {
		return err
	}
	level, err := logx.ParseLevel(cfg.Logging.Level)
	if err != nil {
		return err
	}
	log, err := logx.New(cfg.Logging.OutputPath, cfg.Logging.Format, level, cfg.Service.Version)
	if err != nil {
		return err
	}

	// Best-effort: ensure the directory of a file-based SQLite DSN exists.
	if dir := dbDirFromDSN(cfg.Storage.DSN); dir != "" {
		if err := os.MkdirAll(dir, 0o750); err != nil {
			return fmt.Errorf("create db dir: %w", err)
		}
	}
	st, err := store.Open(cfg.Storage.Driver, cfg.Storage.DSN, cfg.Storage.RequestLogTable, cfg.Storage.MaxOpenConns)
	if err != nil {
		return err
	}
	defer func() {
		if err := st.Close(); err != nil {
			log.Error("store close failed", map[string]any{"err": err.Error()})
		}
	}()

	svc := &server.CodecService{
		Limits:  cfg.Limits,
		Log:     log,
		Store:   st,
		Version: cfg.Service.Version,
	}
	srv := server.New(svc,
		cfg.HTTP.ListenAddr,
		time.Duration(cfg.HTTP.ReadTimeoutMS)*time.Millisecond,
		time.Duration(cfg.HTTP.WriteTimeoutMS)*time.Millisecond,
		cfg.HTTP.MaxBodyBytes,
		log,
	)

	serveErr := make(chan error, 1)
	go func() { serveErr <- srv.ListenAndServe() }()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	select {
	case err := <-serveErr:
		return err
	case sig := <-stop:
		log.Info("shutdown signal", map[string]any{"signal": sig.String()})
		ctx, cancel := context.WithTimeout(context.Background(),
			time.Duration(cfg.HTTP.ShutdownTimeoutMS)*time.Millisecond)
		defer cancel()
		if err := srv.Shutdown(ctx); err != nil && !errors.Is(err, http.ErrServerClosed) {
			return err
		}
		log.Info("shutdown complete", nil)
		return nil
	}
}

// dbDirFromDSN extracts the directory of a "file:path?..." SQLite DSN.
func dbDirFromDSN(dsn string) string {
	const prefix = "file:"
	if len(dsn) < len(prefix) || dsn[:len(prefix)] != prefix {
		return ""
	}
	rest := dsn[len(prefix):]
	for i := 0; i < len(rest); i++ {
		if rest[i] == '?' {
			rest = rest[:i]
			break
		}
	}
	if rest == "" || rest[0] == ':' {
		return ""
	}
	return filepath.Dir(rest)
}
