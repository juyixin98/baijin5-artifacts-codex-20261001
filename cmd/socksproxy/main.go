// Command socksproxy runs the local-whitelist-only SOCKS5 CONNECT proxy and
// manages credentials.
//
//	socksproxy serve   --config configs/proxy.yaml
//	socksproxy adduser --config configs/proxy.yaml --name alice
//
// Passwords are never passed on the command line; adduser reads it from the
// SOCKS_PROXY_PASSWORD environment variable or, when it is a TTY, prompts.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"golang.org/x/term"

	"sockswhitelist/internal/config"
	"sockswhitelist/internal/policy"
	"sockswhitelist/internal/server"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "socksproxy:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		usage()
		return errors.New("a subcommand is required: serve | adduser")
	}
	switch args[0] {
	case "serve":
		return cmdServe(args[1:])
	case "adduser":
		return cmdAddUser(args[1:])
	case "-h", "--help", "help":
		usage()
		return nil
	default:
		usage()
		return fmt.Errorf("unknown subcommand %q", args[0])
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `socksproxy - local-whitelist SOCKS5 CONNECT proxy

usage:
  socksproxy serve   --config configs/proxy.yaml
  socksproxy adduser --config configs/proxy.yaml --name <user>
`)
}

func cmdServe(args []string) error {
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	cfgPath := fs.String("config", "configs/proxy.yaml", "path to YAML config")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := config.Load(*cfgPath)
	if err != nil {
		return err
	}
	ctx := context.Background()
	store, err := policy.Open(ctx, cfg.Database)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()

	if err := store.ReplaceRules(ctx, cfg.Rules); err != nil {
		return fmt.Errorf("load rules: %w", err)
	}

	ln, err := net.Listen("tcp", cfg.Listen)
	if err != nil {
		return fmt.Errorf("listen %s: %w", cfg.Listen, err)
	}
	srv, err := server.New(cfg, store, nil, nil)
	if err != nil {
		return err
	}

	rootCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	errCh := make(chan error, 1)
	go func() { errCh <- srv.Serve(rootCtx, ln) }()

	sig := waitSignal()
	select {
	case <-sig:
		fmt.Fprintln(os.Stderr, "shutting down...")
		cancel()
		srv.Shutdown(10 * time.Second)
	case err := <-errCh:
		return err
	}
	return nil
}

func cmdAddUser(args []string) error {
	fs := flag.NewFlagSet("adduser", flag.ContinueOnError)
	cfgPath := fs.String("config", "configs/proxy.yaml", "path to YAML config")
	name := fs.String("name", "", "username to create or replace")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *name == "" {
		return errors.New("--name is required")
	}
	cfg, err := config.Load(*cfgPath)
	if err != nil {
		return err
	}
	password := os.Getenv("SOCKS_PROXY_PASSWORD")
	if password == "" {
		pw, err := promptPassword()
		if err != nil {
			return err
		}
		password = pw
	}
	if len(password) == 0 {
		return errors.New("empty password")
	}
	ctx := context.Background()
	store, err := policy.Open(ctx, cfg.Database)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	if err := store.SetUser(ctx, *name, password); err != nil {
		return err
	}
	fmt.Printf("user %q provisioned in %s\n", *name, cfg.Database)
	return nil
}

func promptPassword() (string, error) {
	fd := int(os.Stdin.Fd())
	if !term.IsTerminal(fd) {
		return "", errors.New("no password: set SOCKS_PROXY_PASSWORD or run on a TTY")
	}
	fmt.Fprint(os.Stderr, "password: ")
	b, err := term.ReadPassword(fd)
	fmt.Fprintln(os.Stderr)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// waitSignal resolves on SIGINT/SIGTERM for graceful shutdown.
func waitSignal() <-chan os.Signal {
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, syscall.SIGINT, syscall.SIGTERM)
	return ch
}
