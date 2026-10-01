package wire

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"strings"
)

// Option is one CoAP option instance; repeated options (e.g. Uri-Path) are
// separate Option values with the same Number.
type Option struct {
	Number int
	Value  []byte
}

// Options is the ordered option list. It is kept sorted by Number on encode.
type Options []Option

// Len implements sort.Interface.
func (o Options) Len() int { return len(o) }

// Less implements sort.Interface; ties keep insertion order (stable sort).
func (o Options) Less(i, j int) bool { return o[i].Number < o[j].Number }

// Swap implements sort.Interface.
func (o Options) Swap(i, j int) { o[i], o[j] = o[j], o[i] }

// Add appends an option and returns the list. Callers normally use the typed
// accessors (AddString, AddUint, ...) instead.
func (o Options) Add(number int, value []byte) Options {
	return append(o, Option{Number: number, Value: append([]byte(nil), value...)})
}

// AddString appends a string-valued option.
func (o Options) AddString(number int, value string) Options {
	return o.Add(number, []byte(value))
}

// AddUint appends an unsigned integer option using minimal-length encoding
// (RFC 7252 section 3.2).
func (o Options) AddUint(number, value int) Options {
	return o.Add(number, EncodeUint(value))
}

// AddBytes appends an opaque option (e.g. ETag).
func (o Options) AddBytes(number int, value []byte) Options {
	return o.Add(number, value)
}

// All returns every value for an option number in wire order.
func (o Options) All(number int) [][]byte {
	var out [][]byte
	for _, opt := range o {
		if opt.Number == number {
			out = append(out, opt.Value)
		}
	}
	return out
}

// Strings returns all values of an option number decoded as UTF-8 strings.
func (o Options) Strings(number int) []string {
	vals := o.All(number)
	out := make([]string, 0, len(vals))
	for _, v := range vals {
		out = append(out, string(v))
	}
	return out
}

// Lookup returns the first value for an option number and whether it existed.
func (o Options) Lookup(number int) ([]byte, bool) {
	for _, opt := range o {
		if opt.Number == number {
			return opt.Value, true
		}
	}
	return nil, false
}

// Uint decodes the first value of an option number as an unsigned integer.
func (o Options) Uint(number int) (int, bool, error) {
	v, ok := o.Lookup(number)
	if !ok {
		return 0, false, nil
	}
	n, err := DecodeUint(v)
	if err != nil {
		return 0, true, err
	}
	return n, true, nil
}

// PathOptions splits a "/a/b/c" style path into Uri-Path options. Empty
// segments and a leading slash are ignored; this subset does not implement
// "/./" or "/../" normalization.
func PathOptions(path string) Options {
	var opts Options
	segments := strings.Split(strings.TrimPrefix(path, "/"), "/")
	for _, seg := range segments {
		if seg != "" {
			opts = opts.AddString(OptUriPath, seg)
		}
	}
	return opts
}

// EncodeUint renders an int in CoAP's minimal-length unsigned integer form.
func EncodeUint(v int) []byte {
	if v < 0 {
		panic(fmt.Sprintf("wire: cannot encode negative uint %d", v))
	}
	if v == 0 {
		return []byte{0}
	}
	var buf [8]byte
	binary.BigEndian.PutUint64(buf[:], uint64(v))
	i := 0
	for i < 7 && buf[i] == 0 {
		i++
	}
	return append([]byte(nil), buf[i:]...)
}

// DecodeUint parses a minimal-length unsigned integer option value.
func DecodeUint(b []byte) (int, error) {
	if len(b) > 8 {
		return 0, fmt.Errorf("%w: uint option longer than 8 bytes", ErrMalformed)
	}
	v := 0
	for _, c := range b {
		v = v<<8 | int(c)
	}
	return v, nil
}

// writeOption emits one option with delta and length extension bytes.
func writeOption(b *bytes.Buffer, number int, value []byte, prev *int) error {
	delta := number - *prev
	if delta < 0 {
		return fmt.Errorf("%w: option %d after %d", ErrOptionOrder, number, *prev)
	}
	head, ext, err := nibbleAndExt(delta)
	if err != nil {
		return fmt.Errorf("option %d delta: %w", number, err)
	}
	lhead, lext, err := nibbleAndExt(len(value))
	if err != nil {
		return fmt.Errorf("option %d length: %w", number, err)
	}
	b.WriteByte(byte(head<<4 | lhead))
	b.Write(ext)
	b.Write(lext)
	b.Write(value)
	*prev = number
	return nil
}

// nibbleAndExt maps delta/length to the 4-bit nibble and extension bytes.
func nibbleAndExt(v int) (int, []byte, error) {
	switch {
	case v < 13:
		return v, nil, nil
	case v <= 13+0xff:
		return 13, []byte{byte(v - 13)}, nil
	case v <= 269+0xffff:
		return 14, []byte{byte((v - 269) >> 8), byte(v - 269)}, nil
	default:
		return 0, nil, fmt.Errorf("%w: delta/length %d too large", ErrMalformed, v)
	}
}

func validateOptions(opts Options) error {
	prev := 0
	for _, o := range opts {
		if o.Number < prev {
			return fmt.Errorf("%w: %d after %d", ErrOptionOrder, o.Number, prev)
		}
		if o.Number == OptBlock1 || o.Number == OptBlock2 {
			if _, err := DecodeBlock(o.Value); err != nil {
				return err
			}
		}
		prev = o.Number
	}
	return nil
}
