// Package hpack implements a deliberately minimal subset of HPACK (RFC 7541):
// integer coding, the static table, and literal representations without
// Huffman coding. It is sufficient for the predefined simple requests this
// service accepts; it is NOT a complete HPACK implementation.
package hpack

import (
	"errors"
	"fmt"
	"strings"
)

// HeaderField is a name/value pair.
type HeaderField struct {
	Name  string
	Value string
}

func (f HeaderField) String() string { return f.Name + ": " + f.Value }

// ErrHuffmanUnsupported is returned when a Huffman-coded string is met.
// Huffman decoding is out of scope for this service (documented limitation).
var ErrHuffmanUnsupported = errors.New("hpack: huffman-coded strings not supported")

// ErrTruncated is returned for malformed integer/string encodings.
var ErrTruncated = errors.New("hpack: truncated or malformed encoding")

// staticTable is RFC 7541 Appendix A (indices 1..61).
var staticTable = []HeaderField{
	{":authority", ""},                  // 1
	{":method", "GET"},                  // 2
	{":method", "POST"},                 // 3
	{":path", "/"},                      // 4
	{":path", "/index.html"},            // 5
	{":scheme", "http"},                 // 6
	{":scheme", "https"},                // 7
	{":status", "200"},                  // 8
	{":status", "204"},                  // 9
	{":status", "206"},                  // 10
	{":status", "304"},                  // 11
	{":status", "400"},                  // 12
	{":status", "404"},                  // 13
	{":status", "500"},                  // 14
	{"accept-charset", ""},              // 15
	{"accept-encoding", "gzip, deflate"},// 16
	{"accept-language", ""},             // 17
	{"accept-ranges", ""},               // 18
	{"accept", ""},                      // 19
	{"access-control-allow-origin", ""}, // 20
	{"age", ""},                         // 21
	{"allow", ""},                       // 22
	{"authorization", ""},               // 23
	{"cache-control", ""},               // 24
	{"content-disposition", ""},         // 25
	{"content-encoding", ""},            // 26
	{"content-language", ""},            // 27
	{"content-length", ""},              // 28
	{"content-location", ""},            // 29
	{"content-range", ""},               // 30
	{"content-type", ""},                // 31
	{"cookie", ""},                      // 32
	{"date", ""},                        // 33
	{"etag", ""},                        // 34
	{"expect", ""},                      // 35
	{"expires", ""},                     // 36
	{"from", ""},                        // 37
	{"host", ""},                        // 38
	{"if-match", ""},                    // 39
	{"if-modified-since", ""},           // 40
	{"if-none-match", ""},               // 41
	{"if-range", ""},                    // 42
	{"if-unmodified-since", ""},         // 43
	{"last-modified", ""},               // 44
	{"link", ""},                        // 45
	{"location", ""},                    // 46
	{"max-forwards", ""},                // 47
	{"proxy-authenticate", ""},          // 48
	{"proxy-authorization", ""},         // 49
	{"range", ""},                       // 50
	{"referer", ""},                     // 51
	{"refresh", ""},                     // 52
	{"retry-after", ""},                 // 53
	{"server", ""},                      // 54
	{"set-cookie", ""},                  // 55
	{"strict-transport-security", ""},   // 56
	{"transfer-encoding", ""},           // 57
	{"user-agent", ""},                  // 58
	{"vary", ""},                        // 59
	{"via", ""},                         // 60
	{"www-authenticate", ""},            // 61
}

// Decoder decodes header blocks. It keeps a dynamic table because peers may
// legitimately use incremental indexing; the table is bounded.
type Decoder struct {
	dyn     []HeaderField // newest first
	maxSize uint32
	size    uint32
}

// NewDecoder returns a Decoder with the default 4096-byte dynamic table.
func NewDecoder() *Decoder {
	return &Decoder{maxSize: 4096}
}

func entrySize(f HeaderField) uint32 { return uint32(len(f.Name) + len(f.Value) + 32) }

func (d *Decoder) insert(f HeaderField) {
	d.size += entrySize(f)
	d.dyn = append([]HeaderField{f}, d.dyn...)
	for d.size > d.maxSize && len(d.dyn) > 0 {
		last := d.dyn[len(d.dyn)-1]
		d.size -= entrySize(last)
		d.dyn = d.dyn[:len(d.dyn)-1]
	}
}

func (d *Decoder) lookup(idx uint64) (HeaderField, error) {
	if idx == 0 {
		return HeaderField{}, fmt.Errorf("hpack: index 0 is invalid")
	}
	if idx <= uint64(len(staticTable)) {
		return staticTable[idx-1], nil
	}
	di := idx - uint64(len(staticTable)) - 1
	if di < uint64(len(d.dyn)) {
		return d.dyn[di], nil
	}
	return HeaderField{}, fmt.Errorf("hpack: index %d out of range", idx)
}

// decodeInt decodes an HPACK variable-length integer with the given prefix
// width (RFC 7541 §5.1). It returns the value and bytes consumed.
func decodeInt(buf []byte, prefix uint) (uint64, int, error) {
	if len(buf) == 0 {
		return 0, 0, ErrTruncated
	}
	max := uint64(1<<prefix) - 1
	v := uint64(buf[0]) & max
	if v < max {
		return v, 1, nil
	}
	n := 1
	var m uint
	for {
		if n >= len(buf) {
			return 0, 0, ErrTruncated
		}
		b := buf[n]
		n++
		v += uint64(b&0x7f) << m
		m += 7
		if b&0x80 == 0 {
			break
		}
		if m > 63 {
			return 0, 0, fmt.Errorf("hpack: integer overflow")
		}
	}
	return v, n, nil
}

// decodeString decodes an HPACK string literal (RFC 7541 §5.2). Huffman-coded
// strings are rejected with ErrHuffmanUnsupported.
func decodeString(buf []byte) (string, int, error) {
	if len(buf) == 0 {
		return "", 0, ErrTruncated
	}
	if buf[0]&0x80 != 0 {
		return "", 0, ErrHuffmanUnsupported
	}
	l, n, err := decodeInt(buf, 7)
	if err != nil {
		return "", 0, err
	}
	if uint64(len(buf)-n) < l {
		return "", 0, ErrTruncated
	}
	return string(buf[n : n+int(l)]), n + int(l), nil
}

// Decode decodes one complete header block.
func (d *Decoder) Decode(block []byte) ([]HeaderField, error) {
	var out []HeaderField
	for len(block) > 0 {
		b := block[0]
		switch {
		case b&0x80 != 0: // indexed header field (§6.1)
			idx, n, err := decodeInt(block, 7)
			if err != nil {
				return nil, err
			}
			f, err := d.lookup(idx)
			if err != nil {
				return nil, err
			}
			out = append(out, f)
			block = block[n:]
		case b&0xc0 == 0x40: // literal with incremental indexing (§6.2.1)
			f, n, err := d.decodeLiteral(block, 6)
			if err != nil {
				return nil, err
			}
			d.insert(f)
			out = append(out, f)
			block = block[n:]
		case b&0xe0 == 0x20: // dynamic table size update (§6.3)
			sz, n, err := decodeInt(block, 5)
			if err != nil {
				return nil, err
			}
			if sz > 4096 {
				return nil, fmt.Errorf("hpack: dynamic table size %d exceeds maximum 4096", sz)
			}
			d.maxSize = uint32(sz)
			for d.size > d.maxSize && len(d.dyn) > 0 {
				last := d.dyn[len(d.dyn)-1]
				d.size -= entrySize(last)
				d.dyn = d.dyn[:len(d.dyn)-1]
			}
			block = block[n:]
		default: // literal without indexing (0000) / never indexed (0001) (§6.2.2, §6.2.3)
			f, n, err := d.decodeLiteral(block, 4)
			if err != nil {
				return nil, err
			}
			out = append(out, f)
			block = block[n:]
		}
	}
	return out, nil
}

func (d *Decoder) decodeLiteral(block []byte, prefix uint) (HeaderField, int, error) {
	idx, n, err := decodeInt(block, prefix)
	if err != nil {
		return HeaderField{}, 0, err
	}
	rest := block[n:]
	var name string
	if idx == 0 {
		name, n, err = decodeString(rest)
		if err != nil {
			return HeaderField{}, 0, err
		}
		rest = rest[n:]
	} else {
		f, err := d.lookup(idx)
		if err != nil {
			return HeaderField{}, 0, err
		}
		name = f.Name
	}
	value, n2, err := decodeString(rest)
	if err != nil {
		return HeaderField{}, 0, err
	}
	consumed := len(block) - len(rest) + n2
	return HeaderField{Name: name, Value: value}, consumed, nil
}

// EncodeIndexed encodes an indexed header field (static table index 1..127).
func EncodeIndexed(idx uint64) []byte {
	if idx < 127 {
		return []byte{0x80 | byte(idx)}
	}
	return []byte{0xff, byte(idx - 127)}
}

// EncodeLiteralWithoutIndexing encodes a literal field without indexing,
// using an indexed name when the name exists in the static table.
func EncodeLiteralWithoutIndexing(name, value string) []byte {
	var out []byte
	idx := 0
	for i, f := range staticTable {
		if f.Name == name {
			idx = i + 1
			break
		}
	}
	if idx > 0 && idx < 15 {
		out = append(out, byte(idx))
	} else if idx > 0 {
		out = append(out, 0x0f, byte(idx-15))
	} else {
		out = append(out, 0x00)
		out = appendString(out, name)
	}
	out = appendString(out, value)
	return out
}

func appendString(dst []byte, s string) []byte {
	dst = appendInt(dst, 7, uint64(len(s)))
	return append(dst, s...)
}

func appendInt(dst []byte, prefix uint, v uint64) []byte {
	max := uint64(1<<prefix) - 1
	if v < max {
		return append(dst, byte(v))
	}
	dst = append(dst, byte(max))
	v -= max
	for v >= 128 {
		dst = append(dst, byte(v&0x7f|0x80))
		v >>= 7
	}
	return append(dst, byte(v))
}

// FindStatic returns the static table index of an exact name/value match.
func FindStatic(name, value string) int {
	for i, f := range staticTable {
		if strings.EqualFold(f.Name, name) && f.Value == value {
			return i + 1
		}
	}
	return 0
}
