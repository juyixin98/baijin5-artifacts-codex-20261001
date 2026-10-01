// Command smtpsink runs the loopback-only SMTP sink service.
package main

import (
	"context"
	"flag"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"smtpsink/internal/config"
	"smtpsink/internal/server"
	"smtpsink/internal/storage"
)

func main() {
	configPath := flag.String("config", "configs/smtpsink.json", "path to JSON config")
	verbose := flag.Bool("verbose", false, "enable debug-level logging")
	flag.Parse()

	level := slog.LevelInfo
	if *verbose {
		level = slog.LevelDebug
	}
	log := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: level}))

	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Error("configuration error", "err", err.Error())
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	store, err := storage.Open(ctx, storage.Options{
		Path:          cfg.Storage.Path,
		MaxTotalBytes: cfg.Storage.MaxTotalBytes,
	})
	if err != nil {
		log.Error("storage open failed", "err", err.Error())
		os.Exit(1)
	}
	defer store.Close()

	srv := server.New(cfg, store, log)
	if err := srv.Start(ctx); err != nil {
		log.Error("server start failed", "err", err.Error())
		os.Exit(1)
	}

	log.Info("service ready; press Ctrl-C to stop")
	<-ctx.Done()
	log.Info("shutting down")
	if err := srv.Close(); err != nil {
		log.Error("shutdown error", "err", err.Error())
	}
}
