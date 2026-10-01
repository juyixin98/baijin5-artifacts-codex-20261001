// Command socks5d runs the local-whitelist-only SOCKS5 CONNECT proxy.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"socks5d.local/socks5d/internal/auth"
	"socks5d.local/socks5d/internal/config"
	"socks5d.local/socks5d/internal/logx"
	"socks5d.local/socks5d/internal/policy"
	"socks5d.local/socks5d/internal/proto"
	"socks5d.local/socks5d/internal/server"
	"socks5d.local/socks5d/internal/store"
)

func main() {
	cfgPath := flag.String("config", "configs/socks5d.json", "path to configuration file")
	logPath := flag.String("log", "", "path to structured JSON log (default: stderr)")
	flag.Parse()

	if err := run(*cfgPath, *logPath); err != nil {
		// Last-resort fatal line; normal failures are structured events.
		os.Stderr.WriteString("socks5d: " + err.Error() + "\n")
		os.Exit(1)
	}
}

func run(cfgPath, logPath string) error {
	cfg, err := config.Load(cfgPath)
	if err != nil {
		return err
	}

	logFile := os.Stderr
	if logPath != "" {
		f, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
		if err != nil {
			return err
		}
		defer f.Close()
		logFile = f
	}
	lg := logx.New(logFile, server.AppVersion)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	st, err := store.Open(ctx, cfg.Database.Path)
	if err != nil {
		return err
	}
	defer st.Close()
	if err := st.ReplaceRules(ctx, cfg.Rules); err != nil {
		return err
	}

	pol, err := policy.FromRules(cfg.Rules)
	if err != nil {
		return err
	}

	resolver, err := policy.NewStaticResolver(cfg.Resolver.Hosts)
	if err != nil {
		return err
	}

	var authn proto.Authenticator
	if u := os.Getenv("SOCKS5D_USERNAME"); u != "" {
		a, err := auth.NewUserPass(u, os.Getenv("SOCKS5D_PASSWORD"))
		if err != nil {
			return fmt.Errorf("invalid SOCKS5D credentials: %w", err)
		}
		authn = a
	}

	srv := server.New(cfg, pol, resolver, authn, st, lg)
	if err := srv.Listen(); err != nil {
		return err
	}
	lg.Root().Info("listen", "startup", "addr", srv.Addr().String(),
		"rules", len(cfg.Rules), "max_connections", cfg.MaxConnections,
		"byte_budget", cfg.ByteBudget)

	serveErr := make(chan error, 1)
	go func() { serveErr <- srv.Serve(ctx) }()

	select {
	case <-ctx.Done():
		lg.Root().Info("shutdown_signal", "shutdown")
	case err := <-serveErr:
		if err != nil {
			return err
		}
	}

	srv.Shutdown(cfg.ShutdownTimeout.Duration)
	return nil
}
