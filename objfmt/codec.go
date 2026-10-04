package objfmt

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

// Encode serializes o as a self-contained MOBJ byte stream.
// Counts are bounded to the on-disk uint16 limits.
func Encode(o *Object) ([]byte, error) {
	if len(o.Sections) > 65535 {
		return nil, errors.New("objfmt: too many sections")
	}
	if len(o.Symbols) > 65535 {
		return nil, errors.New("objfmt: too many symbols")
	}
	for _, s := range o.Sections {
		if len(s.Data) > 0xFFFFFFFF {
			return nil, errors.New("objfmt: section too large")
		}
	}
	buf := new(bytes.Buffer)
	put := func(v any) { _ = binary.Write(buf, binary.LittleEndian, v) }
	buf.WriteString(Magic)
	put(Version)
	put(uint16(len(o.Sections)))
	put(uint16(len(o.Symbols)))

	for _, s := range o.Sections {
		putStr(buf, s.Name)
		put(s.Flags)
		put(uint32(len(s.Data)))
		buf.Write(s.Data)
	}
	for _, sy := range o.Symbols {
		putStr(buf, sy.Name)
		put(sy.Section)
		put(sy.Value)
		put(sy.Size)
		put(sy.Binding)
		putBool(sy.Export)
	}

	for si, s := range o.Sections {
		rs := o.Relocs[uint16(si)]
		put(uint16(len(rs)))
		for _, r := range rs {
			put(r.Offset)
			t := r.Type
			if r.EdgeWeak {
				t |= 0x80
			}
			put(t)
			put(r.Symbol)
			put(r.Addend)
		}
	}
	return buf.Bytes(), nil
}

// Decode parses data as MOBJ. It rejects truncation, unknown magic/version,
// and any out-of-range index or relocation offset.
func Decode(data []byte) (*Object, error) {
	r := &reader{b: data}
	mag, err := r.bytes(4)
	if err != nil {
		return nil, bad("truncated magic")
	}
	if string(mag) != Magic {
		return nil, bad("bad magic")
	}
	ver, err := r.u16()
	if err != nil {
		return nil, bad("truncated version")
	}
	if ver != Version {
		return nil, bad(fmt.Sprintf("unsupported version %d", ver))
	}
	nsec, err := r.u16()
	if err != nil {
		return nil, bad("truncated section count")
	}
	nsym, err := r.u16()
	if err != nil {
		return nil, bad("truncated symbol count")
	}
	o := &Object{Relocs: map[uint16][]Reloc{}}

	for i := uint16(0); i < nsec; i++ {
		name, err := r.str()
		if err != nil {
			return nil, bad("truncated section name")
		}
		flags, err := r.byte()
		if err != nil {
			return nil, bad("truncated section flags")
		}
		n, err := r.u32()
		if err != nil {
			return nil, bad("truncated section size")
		}
		d, err := r.bytes(int(n))
		if err != nil {
			return nil, bad("truncated section data")
		}
		o.Sections = append(o.Sections, Section{Name: name, Flags: flags, Data: d})
	}
	for i := uint16(0); i < nsym; i++ {
		name, err := r.str()
		if err != nil {
			return nil, bad("truncated symbol name")
		}
		sec, err := r.u16()
		if err != nil {
			return nil, bad("truncated symbol section")
		}
		val, err := r.u32()
		if err != nil {
			return nil, bad("truncated symbol value")
		}
		size, err := r.u32()
		if err != nil {
			return nil, bad("truncated symbol size")
		}
		bnd, err := r.byte()
		if err != nil {
			return nil, bad("truncated symbol binding")
		}
		if bnd > BindGlobal {
			return nil, bad("invalid symbol binding")
		}
		exp, err := r.boolean()
		if err != nil {
			return nil, bad("truncated symbol export flag")
		}
		if sec != UndefinedSection && int(sec) >= len(o.Sections) {
			return nil, bad("symbol section index out of range")
		}
		o.Symbols = append(o.Symbols, Symbol{Name: name, Section: sec, Value: val, Size: size, Binding: bnd, Export: exp})
	}
	for si := uint16(0); si < nsec; si++ {
		nr, err := r.u16()
		if err != nil {
			return nil, bad("truncated reloc count")
		}
		rs := make([]Reloc, 0, nr)
		for i := uint16(0); i < nr; i++ {
			off, err := r.u32()
			if err != nil {
				return nil, bad("truncated reloc offset")
			}
			tb, err := r.byte()
			if err != nil {
				return nil, bad("truncated reloc type")
			}
			sym, err := r.u16()
			if err != nil {
				return nil, bad("truncated reloc symbol")
			}
			add, err := r.i32()
			if err != nil {
				return nil, bad("truncated reloc addend")
			}
			rt := tb &^ uint8(0x80)
			if rt != RelAbs32 && rt != RelPCRel8 {
				return nil, bad("invalid reloc type")
			}
			if int(sym) >= len(o.Symbols) {
				return nil, bad("reloc symbol index out of range")
			}
			size := uint32(4)
			if rt == RelPCRel8 {
				size = 1
			}
			if int(off) < 0 || off+size > uint32(len(o.Sections[si].Data)) {
				return nil, bad("reloc offset out of section")
			}
			rs = append(rs, Reloc{Offset: off, Type: rt, EdgeWeak: tb&0x80 != 0, Symbol: sym, Addend: add})
		}
		o.Relocs[si] = rs
	}
	if r.pos != len(r.b) {
		return nil, bad(fmt.Sprintf("%d trailing bytes after object", len(r.b)-r.pos))
	}
	return o, nil
}

func bad(msg string) error { return fmt.Errorf("%s: %w", EBadObjectWrap, errors.New(msg)) }

// EBadObjectWrap keeps codec errors comparable without importing diag here;
// callers map the text to diag.EBadObject.
const EBadObjectWrap = "MOBJ decode error"

func putStr(buf *bytes.Buffer, s string) {
	_ = binary.Write(buf, binary.LittleEndian, uint16(len(s)))
	buf.WriteString(s)
}

func putBool(v bool) {
	if v {
		buf.WriteByte(1)
	} else {
		buf.WriteByte(0)
	}
}

type reader struct {
	b   []byte
	pos int
}

func (r *reader) byte() (byte, error) {
	if r.pos >= len(r.b) {
		return 0, io.ErrUnexpectedEOF
	}
	v := r.b[r.pos]
	r.pos++
	return v, nil
}

func (r *reader) boolean() (bool, error) {
	v, err := r.byte()
	if err != nil {
		return false, err
	}
	if v > 1 {
		return false, errors.New("invalid boolean")
	}
	return v == 1, nil
}

func (r *reader) u16() (uint16, error) {
	if r.pos+2 > len(r.b) {
		return 0, io.ErrUnexpectedEOF
	}
	v := binary.LittleEndian.Uint16(r.b[r.pos:])
	r.pos += 2
	return v, nil
}

func (r *reader) u32() (uint32, error) {
	if r.pos+4 > len(r.b) {
		return 0, io.ErrUnexpectedEOF
	}
	v := binary.LittleEndian.Uint32(r.b[r.pos:])
	r.pos += 4
	return v, nil
}

func (r *reader) i32() (int32, error) {
	v, err := r.u32()
	return int32(v), err
}

func (r *reader) bytes(n int) ([]byte, error) {
	if r.pos+n > len(r.b) {
		return nil, io.ErrUnexpectedEOF
	}
	v := r.b[r.pos : r.pos+n]
	r.pos += n
	return v, nil
}

func (r *reader) str() (string, error) {
	n, err := r.u16()
	if err != nil {
		return "", err
	}
	v, err := r.bytes(int(n))
	return string(v), err
}
