package fixture

import (
	"fmt"
	"io"
	"log/slog"

	"modbusfixture/config"
	"modbusfixture/core"
)

// Build wires the register banks, router, engine, audit store, server and
// control plane from a validated configuration. The caller starts the
// returned Modbus server (Listen/Serve) and control plane
// (ListenAndServe) and closes all three when done.
type Bundle struct {
	Router  *core.Router
	Engine  *core.Engine
	Audit   *AuditStore
	Server  *Server
	Control *ControlPlane
}

// Build constructs a ready-to-listen bundle.
func Build(cfg config.Config, log *slog.Logger) (*Bundle, error) {
	if err := cfg.Validate(); err != nil {
		return nil, fmt.Errorf("build fixture: %w", err)
	}
	if log == nil {
		log = slog.New(slog.NewTextHandler(io.Discard, nil))
	}

	audit, err := NewAuditStore(cfg.SQLiteDB)
	if err != nil {
		return nil, err
	}

	router := core.NewRouter()
	for _, u := range cfg.Units {
		bank := core.NewRegisterBank(u.RegisterCount)
		if len(u.InitialValues) > 0 {
			bank.Preset(u.InitialValues)
		}
		router.Bind(u.UnitID, bank)
		log.Info("bound unit",
			"unit_id", fmt.Sprintf("0x%02X", u.UnitID),
			"register_count", u.RegisterCount,
			"preset", len(u.InitialValues))
	}
	engine := core.NewEngine(router)

	server := NewServer(cfg, engine, audit, log)
	control := NewControlPlane(cfg, router, audit, server.stats, log)

	return &Bundle{
		Router:  router,
		Engine:  engine,
		Audit:   audit,
		Server:  server,
		Control: control,
	}, nil
}

// Close releases the audit database (the server and control plane must be
// stopped first).
func (b *Bundle) Close() error {
	return b.Audit.Close()
}
