package fixture

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"sync"

	"modbusfixture/config"
	"modbusfixture/core"
)

// ControlPlane is the loopback-only HTTP surface for inspection: health,
// version/config, live counters, the audit trail, and register snapshots.
// It shares the in-memory banks with the Modbus engine, so reads show the
// state as currently served.
type ControlPlane struct {
	cfg    config.Config
	router *core.Router
	audit  *AuditStore
	stats  *stats
	log    *slog.Logger

	srv  *http.Server
	ln   net.Listener
	once sync.Once
}

// NewControlPlane constructs the control plane (not yet listening).
func NewControlPlane(cfg config.Config, router *core.Router, audit *AuditStore, stats *stats, log *slog.Logger) *ControlPlane {
	if log == nil {
		log = slog.New(slog.NewTextHandler(io.Discard, nil))
	}
	cp := &ControlPlane{
		cfg:    cfg,
		router: router,
		audit:  audit,
		stats:  stats,
		log:    log.With("component", "control-plane", "version", config.Version),
	}
	cp.srv = &http.Server{Handler: cp.routes()}
	return cp
}

// Listen binds the control socket. Call Serve afterwards. Splitting bind
// from serve lets callers learn the ephemeral address before requests are
// served, which also removes the start-up read/write race on Addr.
func (cp *ControlPlane) Listen() error {
	ln, err := net.Listen("tcp", cp.cfg.ControlListen)
	if err != nil {
		return fmt.Errorf("control listen on %s: %w", cp.cfg.ControlListen, err)
	}
	cp.ln = ln
	cp.log.Info("control plane listening", "addr", ln.Addr().String())
	return nil
}

// Serve handles requests until the listener is closed (then it returns
// http.ErrServerClosed, which is treated as nil).
func (cp *ControlPlane) Serve() error {
	if cp.srv == nil || cp.ln == nil {
		return fmt.Errorf("control plane: Listen must run before Serve")
	}
	if err := cp.srv.Serve(cp.ln); err != nil && err != http.ErrServerClosed {
		return err
	}
	return nil
}

// ListenAndServe binds and serves in one call.
func (cp *ControlPlane) ListenAndServe() error {
	if err := cp.Listen(); err != nil {
		return err
	}
	return cp.Serve()
}

func (cp *ControlPlane) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", cp.handleHealth)
	mux.HandleFunc("/version", cp.handleVersion)
	mux.HandleFunc("/stats", cp.handleStats)
	mux.HandleFunc("/audit", cp.handleAudit)
	mux.HandleFunc("/registers", cp.handleRegisters)
	return mux
}

// Addr reports the bound control-plane address.
func (cp *ControlPlane) Addr() net.Addr {
	if cp.ln == nil {
		return nil
	}
	return cp.ln.Addr()
}

// Close shuts the control plane down.
func (cp *ControlPlane) Close() error {
	cp.once.Do(func() {})
	if cp.srv == nil {
		return nil
	}
	return cp.srv.Close()
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	_ = enc.Encode(v)
}

func (cp *ControlPlane) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status":  "ok",
		"version": config.Version,
	})
}

func (cp *ControlPlane) handleVersion(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"version":       config.Version,
		"schema_module": "config",
		"listen":        cp.cfg.Listen,
		"workers":       cp.cfg.Workers,
		"latency":       cp.cfg.Latency,
		"units":         cp.cfg.Units,
	})
}

func (cp *ControlPlane) handleStats(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, cp.stats.snapshot())
}

func (cp *ControlPlane) handleAudit(w http.ResponseWriter, r *http.Request) {
	limit := 100
	rows, err := cp.audit.Recent(limit)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"error": err.Error(),
		})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"count":        len(rows),
		"newest_first": true,
		"rows":         rows,
	})
}

// handleRegisters returns a snapshot of one unit's bank:
// GET /registers?unit=1
func (cp *ControlPlane) handleRegisters(w http.ResponseWriter, r *http.Request) {
	unit := uint8(1)
	if s := r.URL.Query().Get("unit"); s != "" {
		var v int
		if _, err := fmt.Sscanf(s, "%d", &v); err != nil || v < 0 || v > 255 {
			writeJSON(w, http.StatusBadRequest, map[string]string{
				"error": "unit must be an integer 0..255",
			})
			return
		}
		unit = byte(v)
	}
	// Router lookup via the engine bindings is internal; the control plane
	// reads through a small accessor kept in bootstrap.
	bank, ok := cp.router.Lookup(unit)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{
			"error": fmt.Sprintf("unit 0x%02X is not bound", unit),
		})
		return
	}
	snap := bank.Snapshot()
	writeJSON(w, http.StatusOK, map[string]any{
		"unit":           unit,
		"register_count": len(snap),
		"values_hex":     registersToHex(snap),
	})
}

func registersToHex(regs []uint16) []string {
	out := make([]string, len(regs))
	for i, v := range regs {
		out[i] = fmt.Sprintf("%04X", v)
	}
	return out
}
