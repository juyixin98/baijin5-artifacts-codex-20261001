// Package service composes the controlled local CoAP service: SQLite
// store + synthetic seed resources + Block1 assembler + engine handler +
// UDP transport server. It contains no protocol decisions of its own — it
// only wires the modules together from validated configuration.
package service

import (
	"context"
	"fmt"
	"net"
	"time"

	"coaplab/internal/blocks"
	"coaplab/internal/config"
	"coaplab/internal/diag"
	"coaplab/internal/engine"
	"coaplab/internal/store"
	"coaplab/internal/transport"
)

// App is a running controlled service.
type App struct {
	cfg    *config.Config
	rec    *diag.Recorder
	db     *store.Store
	asm    *blocks.Block1Assembler
	server *transport.Server
	engine *engine.Service
}

// Option customises service construction.
type Option func(*options)

type options struct {
	wrapHandler  func(transport.Handler) transport.Handler
	preferredSZX int // <0 means "use cfg"
}

// WithHandlerWrapper installs middleware around the CoAP handler (tests use
// it to count the number of times the handler actually runs versus the
// number of datagrams after MID-level retransmission replay).
func WithHandlerWrapper(w func(transport.Handler) transport.Handler) Option {
	return func(o *options) { o.wrapHandler = w }
}

// WithPreferredSZX overrides the server block-size preference for this app
// (compatibility tests need 128-byte SZX=3 independent of the config file).
func WithPreferredSZX(szx uint8) Option {
	return func(o *options) { o.preferredSZX = int(szx) }
}

// New opens the database, seeds synthetic resources and builds the server
// (not yet serving). rec may be nil for a quiet in-process app.
func New(cfg *config.Config, seeds []config.SeedResource, rec *diag.Recorder, opts ...Option) (*App, error) {
	o := options{preferredSZX: -1}
	for _, opt := range opts {
		opt(&o)
	}
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	prefSZX := uint8(cfg.PreferredSZX)
	if o.preferredSZX >= 0 {
		prefSZX = uint8(o.preferredSZX)
	}
	db, err := store.Open(cfg.DBPath)
	if err != nil {
		return nil, fmt.Errorf("service: %w", err)
	}
	for _, sd := range seeds {
		if _, err := db.Put(sd.Path, sd.ContentFormat, sd.Body); err != nil {
			_ = db.Close()
			return nil, fmt.Errorf("service: seed %q: %w", sd.Path, err)
		}
	}

	asm := blocks.NewBlock1Assembler(cfg.Block1Retention, cfg.MaxBodyBytes, prefSZX, rec)
	eng := engine.NewService(db, asm, prefSZX, cfg.MaxMessageSize, rec)
	var handler transport.Handler = eng
	if o.wrapHandler != nil {
		handler = o.wrapHandler(handler)
	}
	srv, err := transport.Listen(transport.ServerOptions{
		Addr:     cfg.ListenAddr,
		Handler:  handler,
		DedupTTL: cfg.ExchangeLifetime,
		Recorder: rec,
	})
	if err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("service: listen: %w", err)
	}
	return &App{cfg: cfg, rec: rec, db: db, asm: asm, server: srv, engine: eng}, nil
}

// Engine exposes the controlled-service engine for test hooks (e.g. the
// commit observer). Production code should not depend on it.
func (a *App) Engine() *engine.Service { return a.engine }

// Assembler exposes the Block1 assembler (test GC/retention hooks).
func (a *App) Assembler() *blocks.Block1Assembler { return a.asm }

// Addr is the bound UDP address (useful when config asks for port 0).
func (a *App) Addr() *net.UDPAddr { return a.server.Addr() }

// Store exposes the database for test/fixture-side updates (representation
// change scenario) and final-state assertions.
func (a *App) Store() *store.Store { return a.db }

// Serve runs until ctx is cancelled.
func (a *App) Serve(ctx context.Context) error {
	gc := time.NewTicker(time.Second)
	defer gc.Stop()
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case <-gc.C:
				a.asm.GC()
			}
		}
	}()
	return a.server.Serve(ctx)
}

// Close releases all resources.
func (a *App) Close() error {
	errSrv := a.server.Close()
	errDB := a.db.Close()
	if errSrv != nil {
		return errSrv
	}
	return errDB
}
