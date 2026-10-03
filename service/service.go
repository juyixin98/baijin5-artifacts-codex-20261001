// Package service wraps the HPACK state machines in a controlled,
// auditable service: every connection gets isolated encoder/decoder
// state, every processed header block gets a request identity, and every
// step and failure is logged and persisted to a SQLite audit store.
package service

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"sync"

	"hpacklab/state"
)

// Config controls a Service.
type Config struct {
	// MaxDynamicTableSize is the per-connection dynamic table capacity
	// announced to peers (SETTINGS_HEADER_TABLE_SIZE equivalent).
	MaxDynamicTableSize int

	// Limits bounds decompression per header block.
	Limits state.Limits

	// Audit persists one row per processing step and per outcome.
	// Required; use OpenAudit to create one.
	Audit *Audit

	// Logger receives human-readable step logs. Optional.
	Logger *log.Logger
}

// Service owns a set of connections with isolated HPACK state.
type Service struct {
	cfg Config

	mu    sync.Mutex
	conns map[string]*Conn
}

// New creates a Service. Connections are opened with OpenConn.
func New(cfg Config) (*Service, error) {
	if cfg.Audit == nil {
		return nil, errors.New("service: Config.Audit is required")
	}
	if cfg.MaxDynamicTableSize < 0 {
		return nil, errors.New("service: negative MaxDynamicTableSize")
	}
	return &Service{cfg: cfg, conns: make(map[string]*Conn)}, nil
}

// Conn is one logical connection with isolated HPACK state.
type Conn struct {
	id  string
	svc *Service
	dec *state.Decoder
	enc *state.Encoder
}

// OpenConn creates (or replaces) the connection with the given id. Each
// connection gets a fresh encoder and decoder: table state is never
// shared between connections.
func (s *Service) OpenConn(id string) (*Conn, error) {
	if id == "" {
		return nil, errors.New("service: empty connection id")
	}
	c := &Conn{
		id:  id,
		svc: s,
		dec: state.NewDecoder(s.cfg.MaxDynamicTableSize, s.cfg.Limits),
		enc: state.NewEncoder(s.cfg.MaxDynamicTableSize, true),
	}
	s.mu.Lock()
	s.conns[id] = c
	s.mu.Unlock()
	return c, nil
}

// CloseConn drops the connection's state.
func (s *Service) CloseConn(id string) {
	s.mu.Lock()
	delete(s.conns, id)
	s.mu.Unlock()
}

// Conn returns the connection with the given id, or nil.
func (s *Service) Conn(id string) *Conn {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.conns[id]
}

// ID returns the connection id.
func (c *Conn) ID() string { return c.id }

// Result is the explainable outcome of processing one header block.
type Result struct {
	RequestID string        // unique per processed block
	ConnID    string        // owning connection
	Fields    []state.Field // decoded fields (nil on failure)
	Events    []state.Event // step-by-step state machine log
	Failure   string        // empty on success; failure category + cause otherwise
}

// newRequestID returns 16 random bytes as hex, for correlating logs and
// audit rows for one processed block.
func newRequestID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", fmt.Errorf("service: request id: %w", err)
	}
	return hex.EncodeToString(b[:]), nil
}

// Decode processes one inbound header block on this connection. The
// request identity, every state machine step, and the outcome (including
// the failure category on error) are logged and written to the audit
// store. A failed decode leaves the connection's decoder desynchronized;
// further Decode calls report state.ErrDesync.
func (c *Conn) Decode(block []byte) (Result, error) {
	reqID, err := newRequestID()
	if err != nil {
		return Result{}, err
	}
	res := Result{RequestID: reqID, ConnID: c.id}

	fields, events, decErr := c.dec.Decode(block)
	res.Fields = fields
	res.Events = events

	rows := make([]AuditRow, 0, len(events)+1)
	for _, ev := range events {
		rows = append(rows, AuditRow{
			RequestID: reqID, ConnID: c.id, Phase: "step",
			Detail: fmt.Sprintf("offset=%d %s: %s", ev.Offset, ev.Kind, ev.Detail),
		})
	}
	if decErr != nil {
		res.Failure = classify(decErr)
		rows = append(rows, AuditRow{
			RequestID: reqID, ConnID: c.id, Phase: "failure",
			Detail: res.Failure + ": " + decErr.Error(),
		})
	} else {
		rows = append(rows, AuditRow{
			RequestID: reqID, ConnID: c.id, Phase: "outcome",
			Detail: fmt.Sprintf("decoded %d fields", len(fields)),
		})
	}
	if err := c.svc.cfg.Audit.Write(rows); err != nil {
		return res, fmt.Errorf("service: audit write: %w", err)
	}
	if lg := c.svc.cfg.Logger; lg != nil {
		for _, r := range rows {
			lg.Printf("req=%s conn=%s phase=%s %s", r.RequestID, r.ConnID, r.Phase, r.Detail)
		}
	}
	return res, decErr
}

// Encode produces one outbound header block on this connection, logging
// the encoder's decisions under a fresh request identity.
func (c *Conn) Encode(fields []state.Field) ([]byte, Result, error) {
	reqID, err := newRequestID()
	if err != nil {
		return nil, Result{}, err
	}
	block, events := c.enc.Encode(fields)
	res := Result{RequestID: reqID, ConnID: c.id, Fields: fields, Events: events}

	rows := make([]AuditRow, 0, len(events)+1)
	for _, ev := range events {
		rows = append(rows, AuditRow{
			RequestID: reqID, ConnID: c.id, Phase: "step",
			Detail: fmt.Sprintf("offset=%d %s: %s", ev.Offset, ev.Kind, ev.Detail),
		})
	}
	rows = append(rows, AuditRow{
		RequestID: reqID, ConnID: c.id, Phase: "outcome",
		Detail: fmt.Sprintf("encoded %d fields into %d bytes", len(fields), len(block)),
	})
	if err := c.svc.cfg.Audit.Write(rows); err != nil {
		return nil, res, fmt.Errorf("service: audit write: %w", err)
	}
	if lg := c.svc.cfg.Logger; lg != nil {
		for _, r := range rows {
			lg.Printf("req=%s conn=%s phase=%s %s", r.RequestID, r.ConnID, r.Phase, r.Detail)
		}
	}
	return block, res, nil
}

// Decoder exposes the connection's decoder for inspection (tests).
func (c *Conn) Decoder() *state.Decoder { return c.dec }

// Encoder exposes the connection's encoder for inspection (tests).
func (c *Conn) Encoder() *state.Encoder { return c.enc }
