package run

import (
	"time"

	"ntpsim/internal/core"
)

// Store persists samples and selection decisions for later inspection.
// The SQLite implementation lives in store_sqlite.go.
type Store interface {
	// SaveRound atomically records one polling round: header + samples + decision.
	SaveRound(runID string, round int, at time.Time, samples []core.Sample, sel core.SelectionResult) error
	// Close releases underlying resources.
	Close() error
}
