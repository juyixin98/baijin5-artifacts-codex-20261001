// Command modbus-fixture runs the local Modbus TCP slave fixture with an
// HTTP control plane and SQLite audit trail. Everything binds to loopback.
//
// Usage:
//
//	modbus-fixture -config config.json
//
// With no -config the built-in defaults are used (127.0.0.1:5020, unit 1,
// 125 registers, audit in ./modbus_fixture.db).
package main

import (
	"context"
	"flag"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	"modbusfixture/config"
	"modbusfixture/fixture"
)

func main() {
	configPath := flag.String("config", "", "path to JSON config (defaults built-in)")
	dbPath := flag.String("db", "", "override sqlite_db path")
	flag.Parse()

	log := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))

	var cfg config.Config
	if *configPath != "" {
		c, err := config.Load(*configPath)
		if err != nil {
			log.Error("config load failed", "err", err)
			os.Exit(2)
		}
		cfg = c
	} else {
		cfg = config.Default()
	}
	if *dbPath != "" {
		cfg.SQLiteDB = *dbPath
	}

	if cfg.SQLiteDB != "" && cfg.SQLiteDB != ":memory:" {
		if err := os.MkdirAll(filepath.Dir(cfg.SQLiteDB), 0o755); err != nil &&
			filepath.Dir(cfg.SQLiteDB) != "." {
			log.Error("cannot create db directory", "err", err)
			os.Exit(2)
		}
	}

	bundle, err := fixture.Build(cfg, log)
	if err != nil {
		log.Error("build failed", "err", err)
		os.Exit(2)
	}

	if err := bundle.Server.Listen(); err != nil {
		log.Error("modbus listen failed", "err", err)
		os.Exit(1)
	}
	go func() {
		if err := bundle.Control.ListenAndServe(); err != nil {
			log.Error("control plane failed", "err", err)
		}
	}()

	ctx, stop := signal.NotifyContext(context.Background(),
		syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	go func() {
		<-ctx.Done()
		log.Info("shutdown signal received")
		_ = bundle.Server.Close()
		_ = bundle.Control.Close()
	}()

	log.Info("fixture ready",
		"modbus", bundle.Server.Addr().String(),
		"control", bundle.Control.Addr().String())
	if err := bundle.Server.Serve(ctx); err != nil {
		log.Error("serve failed", "err", err)
		os.Exit(1)
	}
	_ = bundle.Close()
	log.Info("fixture stopped")
}
