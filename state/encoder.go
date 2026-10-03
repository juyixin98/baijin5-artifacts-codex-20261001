package state

import (
	"fmt"

	"hpacklab/codec"
	"hpacklab/table"
)

// Encoder is the HPACK encoding state machine for one connection
// direction. It owns its dynamic table and mirrors the peer decoder's
// table as long as every emitted block is delivered in order.
type Encoder struct {
	tab        *table.Table
	useHuffman bool

	// pendingSizeUpdates holds dynamic table size changes queued by
	// SetMaxDynamicSize; they are emitted at the start of the next
	// header block, as RFC 7541 Section 4.2 requires.
	pendingSizeUpdates []int
}

// NewEncoder returns an Encoder with the given dynamic table capacity.
// If huffman is true, string literals are Huffman-coded when shorter.
func NewEncoder(maxDynamicSize int, huffman bool) *Encoder {
	return &Encoder{tab: table.NewTable(maxDynamicSize), useHuffman: huffman}
}

// Table exposes the encoder's table for inspection (tests, diagnostics).
func (e *Encoder) Table() *table.Table { return e.tab }

// SetMaxDynamicSize changes the dynamic table capacity and queues a size
// update to be emitted at the start of the next encoded block.
func (e *Encoder) SetMaxDynamicSize(size int) {
	e.tab.Dyn.SetMaxSize(size)
	e.pendingSizeUpdates = append(e.pendingSizeUpdates, size)
}

// Encode encodes one header block. Sensitive fields are emitted as
// "never indexed" and are never added to the dynamic table; all other
// fields use incremental indexing unless they match an existing entry
// exactly. Queued table size updates are emitted first, before any field
// representation, per RFC 7541 Section 4.2.
func (e *Encoder) Encode(fields []Field) ([]byte, []Event) {
	var out []byte
	var events []Event

	for _, size := range e.pendingSizeUpdates {
		off := len(out)
		out = codec.AppendInteger(out, uint64(size), 5, 0x20)
		events = append(events, Event{off, EvSizeUpdate,
			fmt.Sprintf("announced max table size %d", size)})
	}
	e.pendingSizeUpdates = e.pendingSizeUpdates[:0]

	for _, f := range fields {
		off := len(out)
		if f.Sensitive {
			out = e.appendLiteral(out, f, 0x10, 4)
			events = append(events, Event{off, EvLiteralNever,
				fmt.Sprintf("%s=<redacted, never indexed>", f.Name)})
			continue
		}
		if idx := e.tab.FindEntry(f.Name, f.Value); idx != 0 {
			out = codec.AppendInteger(out, idx, 7, 0x80)
			events = append(events, Event{off, EvIndexed,
				fmt.Sprintf("index=%d %s=%q", idx, f.Name, f.Value)})
			continue
		}
		out = e.appendLiteral(out, f, 0x40, 6)
		entry := table.Entry{Name: f.Name, Value: f.Value}
		e.tab.Dyn.Add(entry)
		events = append(events, Event{off, EvLiteralIndexed,
			fmt.Sprintf("%s=%q inserted (cost %d, table %d/%d)",
				f.Name, f.Value, entry.Size(), e.tab.Dyn.Size(), e.tab.Dyn.MaxSize())})
	}
	return out, events
}

// appendLiteral emits a literal representation with the given tag bits and
// index prefix width, using a name index when one exists.
func (e *Encoder) appendLiteral(dst []byte, f Field, tag byte, prefixBits uint) []byte {
	if idx := e.tab.FindName(f.Name); idx != 0 {
		dst = codec.AppendInteger(dst, idx, prefixBits, tag)
	} else {
		dst = codec.AppendInteger(dst, 0, prefixBits, tag)
		dst = codec.AppendString(dst, f.Name, e.useHuffman)
	}
	return codec.AppendString(dst, f.Value, e.useHuffman)
}
