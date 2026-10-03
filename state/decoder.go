package state

import (
	"fmt"

	"hpacklab/codec"
	"hpacklab/table"
)

// Field is one decoded header field. Sensitive is true when the field was
// represented as "never indexed" (RFC 7541 Section 6.2.3) or marked so at
// encode time.
type Field struct {
	Name      string
	Value     string
	Sensitive bool
}

// Event is one step of the decode/encode walk, recorded for explainable
// logs. Offset is the byte offset within the header block where the step
// started (-1 for events not tied to input, e.g. encoder decisions).
type Event struct {
	Offset int
	Kind   string
	Detail string
}

// Event kinds emitted by Decoder and Encoder.
const (
	EvIndexed        = "indexed"
	EvLiteralIndexed = "literal-incremental"
	EvLiteralPlain   = "literal-without-indexing"
	EvLiteralNever   = "literal-never-indexed"
	EvSizeUpdate     = "table-size-update"
	EvEvict          = "table-evict"
	EvInsert         = "table-insert"
)

// Decoder is the HPACK decoding state machine for one connection
// direction. It owns its dynamic table; it is not safe for concurrent use.
type Decoder struct {
	tab     *table.Table
	limits  Limits
	maxConf int // maximum dynamic table size announced to the peer

	broken bool // set after any decode failure; see ErrDesync
}

// NewDecoder returns a Decoder whose dynamic table may grow to
// maxDynamicSize bytes. Size updates from the peer larger than
// maxDynamicSize are rejected with ErrSizeUpdateTooLarge.
func NewDecoder(maxDynamicSize int, limits Limits) *Decoder {
	return &Decoder{
		tab:     table.NewTable(maxDynamicSize),
		limits:  limits,
		maxConf: maxDynamicSize,
	}
}

// Table exposes the decoder's table for inspection (tests, diagnostics).
func (d *Decoder) Table() *table.Table { return d.tab }

// Broken reports whether a previous Decode failed; once true, every
// subsequent Decode returns ErrDesync without consuming input.
func (d *Decoder) Broken() bool { return d.broken }

// Decode processes one complete header block and returns the decoded
// fields plus the step-by-step event log. Any failure marks the decoder
// broken: the caller must treat the connection as desynchronized and must
// not feed further blocks into it.
func (d *Decoder) Decode(block []byte) ([]Field, []Event, error) {
	if d.broken {
		return nil, nil, ErrDesync
	}
	fields, events, err := d.decode(block)
	if err != nil {
		d.broken = true
		return nil, events, err
	}
	return fields, events, nil
}

func (d *Decoder) decode(block []byte) ([]Field, []Event, error) {
	var fields []Field
	var events []Event
	listBytes := 0
	fieldSeen := false // any non-size-update representation in this block

	pos := 0
	for pos < len(block) {
		off := pos
		b := block[pos]

		switch {
		case b&0x80 != 0: // indexed header field, Section 6.1
			idx, n, err := codec.DecodeInteger(block[pos:], 7)
			if err != nil {
				return nil, events, fmt.Errorf("offset %d: indexed field: %w", off, err)
			}
			pos += n
			e, err := d.tab.Lookup(idx)
			if err != nil {
				return nil, events, fmt.Errorf("offset %d: indexed field: %w", off, err)
			}
			events = append(events, Event{off, EvIndexed,
				fmt.Sprintf("index=%d %s=%q", idx, e.Name, e.Value)})
			fieldSeen = true
			fields = append(fields, Field{Name: e.Name, Value: e.Value})

		case b&0xc0 == 0x40: // literal with incremental indexing, Section 6.2.1
			name, value, n, err := d.decodeLiteral(block[pos:], 6)
			if err != nil {
				return nil, events, fmt.Errorf("offset %d: literal with indexing: %w", off, err)
			}
			pos += n
			entry := table.Entry{Name: name, Value: value}
			d.tab.Dyn.Add(entry)
			events = append(events, Event{off, EvLiteralIndexed,
				fmt.Sprintf("%s=%q inserted at dynamic index %d (cost %d, table %d/%d)",
					name, value, table.StaticLen+1, entry.Size(), d.tab.Dyn.Size(), d.tab.Dyn.MaxSize())})
			fieldSeen = true
			fields = append(fields, Field{Name: name, Value: value})

		case b&0xe0 == 0x20: // dynamic table size update, Section 6.3
			if fieldSeen {
				return nil, events, fmt.Errorf("offset %d: %w", off, ErrSizeUpdatePlacement)
			}
			size, n, err := codec.DecodeInteger(block[pos:], 5)
			if err != nil {
				return nil, events, fmt.Errorf("offset %d: size update: %w", off, err)
			}
			pos += n
			if size > uint64(d.maxConf) {
				return nil, events, fmt.Errorf("offset %d: size update %d > max %d: %w",
					off, size, d.maxConf, ErrSizeUpdateTooLarge)
			}
			before := d.tab.Dyn.MaxSize()
			evictedBefore := d.tab.Dyn.Evictions
			d.tab.Dyn.SetMaxSize(int(size))
			events = append(events, Event{off, EvSizeUpdate,
				fmt.Sprintf("max table size %d -> %d (evicted %d entries, table now %d bytes)",
					before, size, d.tab.Dyn.Evictions-evictedBefore, d.tab.Dyn.Size())})

		default: // literal without indexing (0x00) or never indexed (0x10)
			never := b&0xf0 == 0x10
			name, value, n, err := d.decodeLiteral(block[pos:], 4)
			if err != nil {
				kind := "literal without indexing"
				if never {
					kind = "literal never indexed"
				}
				return nil, events, fmt.Errorf("offset %d: %s: %w", off, kind, err)
			}
			pos += n
			kind := EvLiteralPlain
			if never {
				kind = EvLiteralNever
			}
			events = append(events, Event{off, kind, fmt.Sprintf("%s=%q", name, value)})
			fieldSeen = true
			fields = append(fields, Field{Name: name, Value: value, Sensitive: never})
		}

		// Decompression limits, checked after every field.
		if len(fields) > 0 {
			last := fields[len(fields)-1]
			listBytes += len(last.Name) + len(last.Value) + table.EntryOverhead
			if d.limits.MaxHeaderListBytes > 0 && listBytes > d.limits.MaxHeaderListBytes {
				return nil, events, fmt.Errorf("offset %d: %w (%d > %d bytes)",
					off, ErrHeaderListTooLarge, listBytes, d.limits.MaxHeaderListBytes)
			}
			if d.limits.MaxHeaderCount > 0 && len(fields) > d.limits.MaxHeaderCount {
				return nil, events, fmt.Errorf("offset %d: %w (%d > %d)",
					off, ErrTooManyHeaders, len(fields), d.limits.MaxHeaderCount)
			}
		}
	}
	return fields, events, nil
}

// decodeLiteral decodes the name (indexed or literal) and value of a
// literal representation starting at buf[0]. prefixBits is the index
// prefix width of the representation (6 for incremental indexing, 4 for
// without/never indexed). It returns the name, value and bytes consumed.
func (d *Decoder) decodeLiteral(buf []byte, prefixBits uint) (name, value string, n int, err error) {
	idx, used, err := codec.DecodeInteger(buf, prefixBits)
	if err != nil {
		return "", "", 0, err
	}
	n += used
	if idx == 0 {
		name, used, err = codec.DecodeString(buf[n:], d.limits.MaxStringLen)
		if err != nil {
			return "", "", 0, fmt.Errorf("name: %w", err)
		}
		n += used
	} else {
		e, err := d.tab.Lookup(idx)
		if err != nil {
			return "", "", 0, fmt.Errorf("name index %d: %w", idx, err)
		}
		name = e.Name
	}
	value, used, err = codec.DecodeString(buf[n:], d.limits.MaxStringLen)
	if err != nil {
		return "", "", 0, fmt.Errorf("value: %w", err)
	}
	n += used
	return name, value, n, nil
}
