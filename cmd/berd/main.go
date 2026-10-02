// Command berd runs the BER codec HTTP service.
package main

import (
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"runtime"

	"berd/internal/config"
	"berd/internal/server"
	"berd/internal/store"
	"berd/internal/version"
)

func main() {
	cfgPath := flag.String("config", "configs/berd.json", "config file path")
	listen := flag.String("listen", "", "override listen address")
	dbPath := flag.String("db", "", "override SQLite audit database path")
	flag.Parse()

	cfg, err := config.Load(*cfgPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "config:", err)
		os.Exit(2)
	}
	if *listen != "" {
		cfg.Listen = *listen
	}
	if *dbPath != "" {
		cfg.DBPath = *dbPath
	}

	logger := slog.New(slog.NewJSONHandler(os.Stderr, nil))
	st, err := store.Open(cfg.DBPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "store:", err)
		os.Exit(2)
	}
	defer st.Close()

	srv := server.New(cfg, st, logger)
	logger.Info("starting",
		"run_id", srv.RunID(),
		"version", version.Version,
		"go_version", runtime.Version(),
		"listen", cfg.Listen,
		"db_path", cfg.DBPath,
		"limits", cfg.Limits,
	)
	if err := http.ListenAndServe(cfg.Listen, srv.Handler()); err != nil {
		logger.Error("server stopped", "error", err)
		os.Exit(1)
	}
}
