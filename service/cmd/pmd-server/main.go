// pmd-server runs the pattern-match compiler HTTP service.
package main

import (
	"flag"
	"log/slog"
	"net/http"
	"os"

	"pmd/service"
)

func main() {
	cfgPath := flag.String("config", "", "path to JSON config file (optional)")
	addr := flag.String("addr", "", "listen address (overrides config)")
	flag.Parse()

	cfg, err := service.Load(*cfgPath)
	if err != nil {
		slog.Error("load config", "path", *cfgPath, "error", err)
		os.Exit(1)
	}
	if *addr != "" {
		cfg.Addr = *addr
	}
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	logger.Info("server starting",
		"addr", cfg.Addr,
		"version", service.Version,
		"config", *cfgPath,
	)
	if err := http.ListenAndServe(cfg.Addr, service.NewHandler(cfg, logger)); err != nil {
		logger.Error("server exited", "error", err)
		os.Exit(1)
	}
}
