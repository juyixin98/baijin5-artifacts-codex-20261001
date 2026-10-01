// Package audit defines the record contract shared by the STUN client and
// server and the sinks that persist those records (console, SQLite). Keeping
// the contract separate from the SQLite implementation means protocol code
// never imports a database driver.
package audit

import "time"

// Record is one replayable protocol observation. Every field that can vary a
// judgment (run id, monotonic sequence, transaction id, source address,
// failure category, wire bytes) is captured so a problem can be replayed
// from the log alone.
type Record struct {
	RunID     string    `json:"run_id"`
	Seq       uint64    `json:"seq"`
	Timestamp time.Time `json:"ts"`
	Component string    `json:"component"` // "server" | "client"
	Event     string    `json:"event"`
	Kind      string    `json:"kind"` // stunerror.Kind label, "" for success events
	TxID      string    `json:"tx_id_hex"`
	SrcAddr   string    `json:"src_addr,omitempty"`
	DstAddr   string    `json:"dst_addr,omitempty"`
	Detail    string    `json:"detail,omitempty"`
	WireHex   string    `json:"wire_hex,omitempty"`
}

// Sink consumes audit records. Implementations must be safe for concurrent use.
type Sink interface {
	Write(Record) error
	Close() error
}

// Noop is a Sink that discards everything.
var Noop Sink = noopSink{}

type noopSink struct{}

func (noopSink) Write(Record) error { return nil }
func (noopSink) Close() error       { return nil }
