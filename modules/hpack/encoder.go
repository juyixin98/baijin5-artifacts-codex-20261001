package hpack

import (
	"hpacklab.local/codec"
)

// Encoder is the sending side of one connection's HPACK state. It maintains
// its own dynamic table, independent of every Decoder and every other
// connection's Encoder.
type Encoder struct {
	dt *dynamicTable
	// pendingSize, when non-nil, is a size update that must be emitted at
	// the start of the next block (RFC 7541 section 4.2).
	pendingSize *uint32
	huffman     bool
}

// EncoderOptions configures NewEncoder.
type EncoderOptions struct {
	// MaxTableSize is the peer-advertised SETTINGS_HEADER_TABLE_SIZE.
	MaxTableSize uint32
	// Huffman turns Huffman encoding on for literal names and values.
	Huffman bool
}

// NewEncoder builds a connection-scoped encoder.
func NewEncoder(opts EncoderOptions) *Encoder {
	return &Encoder{dt: newDynamicTable(opts.MaxTableSize), huffman: opts.Huffman}
}

// SetHuffman toggles Huffman encoding for subsequent blocks.
func (e *Encoder) SetHuffman(on bool) { e.huffman = on }

// DynamicTableSize reports live byte occupancy.
func (e *Encoder) DynamicTableSize() uint32 { return e.dt.size }

// DynamicTableLen reports the number of entries.
func (e *Encoder) DynamicTableLen() int { return e.dt.len() }

// UpdateMaxTableSize records a new SETTINGS_HEADER_TABLE_SIZE value from
// the peer, which is the authoritative ceiling from then on. The dynamic
// table size update representation is emitted at the start of the next
// block, before any header field, per section 4.2.
func (e *Encoder) UpdateMaxTableSize(size uint32) {
	e.dt.allowedMaxSize = size
	s := size
	e.pendingSize = &s
}

// EncodeBlock encodes one ordered list of header fields. Sensitive fields
// are emitted as never-indexed (section 6.2.3) and never enter the dynamic
// table. A pending size update, if any, is written first.
func (e *Encoder) EncodeBlock(fields []HeaderField) []byte {
	dst := make([]byte, 0, 64)
	if e.pendingSize != nil {
		dst = codec.AppendInteger(dst, 0x20, 5, uint64(*e.pendingSize))
		e.dt.setMaxSize(*e.pendingSize)
		e.pendingSize = nil
	}
	for _, f := range fields {
		dst = e.encodeField(dst, f)
	}
	return dst
}

func (e *Encoder) encodeField(dst []byte, f HeaderField) []byte {
	// Resolve every possible reference once.
	dynNameIdx, dynExact := e.dt.find(f)
	staticIdx, staticMatch := lookupStatic(f)

	if !f.Sensitive {
		// Indexed representation: exact dynamic match wins over static.
		if dynExact {
			return codec.AppendInteger(dst, 0x80, 7, uint64(staticLen+dynNameIdx))
		}
		if staticMatch == staticNameValue {
			return codec.AppendInteger(dst, 0x80, 7, uint64(staticIdx))
		}
		// Literal with incremental indexing, section 6.2.1 (prefix 01).
		return e.appendLiteral(dst, 0x40, 6, f, true, dynNameIdx, staticIdx, staticMatch)
	}
	// Never-indexed, section 6.2.3 (prefix 0001). A name-only reference is
	// safe to use; the value is always literal.
	return e.appendLiteral(dst, 0x10, 4, f, false, dynNameIdx, staticIdx, staticMatch)
}

// appendLiteral writes a literal representation. add controls insertion into
// the encoder's dynamic table; the already-resolved table references avoid
// a second lookup.
func (e *Encoder) appendLiteral(dst []byte, lead byte, prefix uint, f HeaderField, add bool,
	dynNameIdx int, staticIdx int, staticMatch staticMatch) []byte {
	// Name referencing: a dynamic name match first, then a static name (or
	// name/value) match.
	nameIdx := 0
	if dynNameIdx > 0 {
		nameIdx = staticLen + dynNameIdx
	} else if staticMatch != staticNone {
		nameIdx = staticIdx
	}
	if nameIdx > 0 {
		dst = codec.AppendInteger(dst, lead, prefix, uint64(nameIdx))
	} else {
		dst = codec.AppendInteger(dst, lead, prefix, 0)
		dst = e.appendString(dst, f.Name)
	}
	dst = e.appendString(dst, f.Value)
	if add {
		e.dt.add(f)
	}
	return dst
}

func (e *Encoder) appendString(dst []byte, s string) []byte {
	if e.huffman {
		encoded := codec.AppendHuffman(make([]byte, 0, codec.HuffmanEncodedLen([]byte(s))), []byte(s))
		dst = codec.AppendInteger(dst, 0x80, 7, uint64(len(encoded)))
		return append(dst, encoded...)
	}
	dst = codec.AppendInteger(dst, 0x00, 7, uint64(len(s)))
	return append(dst, s...)
}
