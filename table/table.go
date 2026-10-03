// Package table implements the HPACK static table (RFC 7541 Appendix A)
// and the per-connection dynamic table (RFC 7541 Section 2.3), including
// size accounting and eviction by entry byte cost.
package table

import "errors"

// Entry is one name/value pair in a header table.
type Entry struct {
	Name  string
	Value string
}

// EntryOverhead is the per-entry accounting overhead defined by
// RFC 7541 Section 4.1.
const EntryOverhead = 32

// Size returns the byte cost of the entry: name + value + 32.
func (e Entry) Size() int {
	return len(e.Name) + len(e.Value) + EntryOverhead
}

// Lookup error categories.
var (
	// ErrIndexZero reports use of index 0, which RFC 7541 Section 2.3.3
	// reserves and forbids.
	ErrIndexZero = errors.New("table: index 0 is not a valid table index")

	// ErrIndexOutOfRange reports an index beyond the current combined
	// static+dynamic table length.
	ErrIndexOutOfRange = errors.New("table: index out of range")
)
