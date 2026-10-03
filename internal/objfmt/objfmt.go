// Package objfmt defines the synthetic relocatable object format "MKOB v1"
// used by the mylnk project. The format is a compact little-endian binary
// container with sections, symbols (strong/weak/local) and relocations.
package objfmt

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

// Magic and Version identify a mylnk object file.
const (
	Magic   = "MKOB"
	Version = uint16(1)

	// Bind kinds for symbols.
	BindLocal  = 0 // visible only inside the defining object
	BindStrong = 1 // strong global definition/reference
	BindWeak   = 2 // weak global definition or weak undefined reference
)

// ErrTruncated is returned when an object ends unexpectedly.
var ErrTruncated = errors.New("objfmt: truncated object stream")

// Section is one named, independently garbage-collectable section.
type Section struct {
	Name   string
	Data   []byte
	Keep   bool // section is an unconditional GC root (data-like anchor)
	Syms   []*Symbol
	Relocs []Reloc
}

// Symbol is a named definition or reference inside a section.
type Symbol struct {
	Name   string
	Bind   int
	Off    uint32
	Export bool
	Def    bool
	SecIdx uint16 // section index for definitions; 0xFFFF otherwise
}

// UndefSec marks an undefined (referenced-only) symbol's SecIdx.
const UndefSec = uint16(0xFFFF)

// Relocation kinds.
const (
	KindAbs32 = 1 // *site = S + A
	KindRel32 = 2 // *site = S + A - P  (P = VMA of site)
)

// Reloc is a relocation site inside a section.
type Reloc struct {
	Off    uint32 // offset within the section
	SymIdx uint32 // index into Object.Symbols
	Addend int32
	Kind   uint8
}

// Object is one parsed relocatable object file.
type Object struct {
	Name     string
	Sections []*Section
	Symbols  []*Symbol
}

type reader struct {
	b []byte
	p int
}

func (r *reader) bytes(n int) ([]byte, error) {
	if r.p+n > len(r.b) {
		return nil, ErrTruncated
	}
	v := r.b[r.p : r.p+n]
	r.p += n
	return v, nil
}

func (r *reader) u8() (uint8, error) {
	v, err := r.bytes(1)
	if err != nil {
		return 0, err
	}
	return v[0], nil
}

func (r *reader) u16() (uint16, error) {
	v, err := r.bytes(2)
	if err != nil {
		return 0, err
	}
	return binary.LittleEndian.Uint16(v), nil
}

func (r *reader) u32() (uint32, error) {
	v, err := r.bytes(4)
	if err != nil {
		return 0, err
	}
	return binary.LittleEndian.Uint32(v), nil
}

func (r *reader) i32() (int32, error) {
	v, err := r.u32()
	return int32(v), err
}

func (r *reader) str() (string, error) {
	n, err := r.u16()
	if err != nil {
		return "", err
	}
	v, err := r.bytes(int(n))
	if err != nil {
		return "", err
	}
	return string(v), nil
}

type writer struct {
	b []byte
}

func (w *writer) u8(v uint8) { w.b = append(w.b, v) }

func (w *writer) u16(v uint16) {
	var tmp [2]byte
	binary.LittleEndian.PutUint16(tmp[:], v)
	w.b = append(w.b, tmp[:]...)
}

func (w *writer) u32(v uint32) {
	var tmp [4]byte
	binary.LittleEndian.PutUint32(tmp[:], v)
	w.b = append(w.b, tmp[:]...)
}

func (w *writer) i32(v int32) { w.u32(uint32(v)) }

func (w *writer) str(s string) {
	w.u16(uint16(len(s)))
	w.b = append(w.b, s...)
}

// Encode serialises an object to the MKOB v1 wire format.
func Encode(o *Object) ([]byte, error) {
	if err := validate(o); err != nil {
		return nil, err
	}
	w := &writer{}
	w.b = append(w.b, Magic...)
	w.u16(Version)
	w.str(o.Name)

	secCount := make(map[*Section]int)
	w.u16(uint16(len(o.Sections)))
	for _, s := range o.Sections {
		secCount[s] = len(secCount)
		w.str(s.Name)
		if s.Keep {
			w.u8(1)
		} else {
			w.u8(0)
		}
		w.u32(uint32(len(s.Data)))
		w.b = append(w.b, s.Data...)
	}

	w.u16(uint16(len(o.Symbols)))
	for _, sy := range o.Symbols {
		w.str(sy.Name)
		w.u8(bindByte(sy.Bind))
		w.u32(sy.Off)
		flags := uint8(0)
		if sy.Def {
			flags |= 1
		}
		if sy.Export {
			flags |= 2
		}
		w.u8(flags)
		w.u16(sy.SecIdx)
	}

	for _, s := range o.Sections {
		idx := secCount[s]
		w.u16(uint16(idx))
		w.u16(uint16(len(s.Relocs)))
		for _, rl := range s.Relocs {
			w.u32(rl.Off)
			w.u32(rl.SymIdx)
			w.i32(rl.Addend)
			w.u8(rl.Kind)
		}
	}
	return w.b, nil
}

func bindByte(b int) uint8 {
	switch b {
	case BindLocal:
		return 0
	case BindStrong:
		return 1
	case BindWeak:
		return 2
	default:
		return 255
	}
}

func bindFromByte(b uint8) (int, error) {
	switch b {
	case 0:
		return BindLocal, nil
	case 1:
		return BindStrong, nil
	case 2:
		return BindWeak, nil
	default:
		return 0, fmt.Errorf("objfmt: invalid symbol bind %d", b)
	}
}

func validate(o *Object) error {
	if len(o.Name) > 65535 {
		return errors.New("objfmt: object name too long")
	}
	if len(o.Sections) > 65535 || len(o.Symbols) > 65535 {
		return errors.New("objfmt: too many sections or symbols")
	}
	secSet := make(map[*Section]bool, len(o.Sections))
	for _, s := range o.Sections {
		if s == nil {
			return errors.New("objfmt: nil section")
		}
		if len(s.Name) > 65535 {
			return errors.New("objfmt: section name too long")
		}
		if secSet[s] {
			return fmt.Errorf("objfmt: duplicate section pointer %q", s.Name)
		}
		secSet[s] = true
		for _, rl := range s.Relocs {
			if int(rl.SymIdx) >= len(o.Symbols) {
				return fmt.Errorf("objfmt: reloc in section %q references missing symbol index %d", s.Name, rl.SymIdx)
			}
			if uint64(rl.Off)+4 > uint64(len(s.Data)) {
				return fmt.Errorf("objfmt: reloc at %d out of bounds in section %q", rl.Off, s.Name)
			}
		}
		for _, sy := range s.Syms {
			if sy == nil {
				return fmt.Errorf("objfmt: nil symbol in section %q", s.Name)
			}
		}
	}
	for _, sy := range o.Symbols {
		if sy == nil {
			return errors.New("objfmt: nil symbol")
		}
		if len(sy.Name) > 65535 {
			return errors.New("objfmt: symbol name too long")
		}
		if sy.Def {
			if int(sy.SecIdx) >= len(o.Sections) {
				return fmt.Errorf("objfmt: defined symbol %q has invalid section index %d", sy.Name, sy.SecIdx)
			}
		}
	}
	return nil
}

// Decode parses one MKOB v1 object.
func Decode(b []byte) (*Object, error) {
	r := &reader{b: b}
	magic, err := r.bytes(4)
	if err != nil {
		return nil, err
	}
	if string(magic) != Magic {
		return nil, fmt.Errorf("objfmt: bad magic %q", string(magic))
	}
	ver, err := r.u16()
	if err != nil {
		return nil, err
	}
	if ver != Version {
		return nil, fmt.Errorf("objfmt: unsupported version %d (want %d)", ver, Version)
	}
	o := &Object{}
	if o.Name, err = r.str(); err != nil {
		return nil, err
	}

	nSec, err := r.u16()
	if err != nil {
		return nil, err
	}
	o.Sections = make([]*Section, nSec)
	for i := range o.Sections {
		s := &Section{}
		if s.Name, err = r.str(); err != nil {
			return nil, err
		}
		keep, err := r.u8()
		if err != nil {
			return nil, err
		}
		s.Keep = keep == 1
		nData, err := r.u32()
		if err != nil {
			return nil, err
		}
		if s.Data, err = r.bytes(int(nData)); err != nil {
			return nil, err
		}
		o.Sections[i] = s
	}

	nSym, err := r.u16()
	if err != nil {
		return nil, err
	}
	o.Symbols = make([]*Symbol, nSym)
	for i := range o.Symbols {
		sy := &Symbol{}
		if sy.Name, err = r.str(); err != nil {
			return nil, err
		}
		bindByte, err := r.u8()
		if err != nil {
			return nil, err
		}
		if sy.Bind, err = bindFromByte(bindByte); err != nil {
			return nil, err
		}
		if sy.Off, err = r.u32(); err != nil {
			return nil, err
		}
		flags, err := r.u8()
		if err != nil {
			return nil, err
		}
		sy.Def = flags&1 != 0
		sy.Export = flags&2 != 0
		if sy.SecIdx, err = r.u16(); err != nil {
			return nil, err
		}
		o.Symbols[i] = sy
	}
	// Rebuild per-section symbol navigation used by the IR layer.
	for _, sy := range o.Symbols {
		if sy.Def && int(sy.SecIdx) < len(o.Sections) {
			o.Sections[sy.SecIdx].Syms = append(o.Sections[sy.SecIdx].Syms, sy)
		}
	}

	for range o.Sections {
		secIdx, err := r.u16()
		if err != nil {
			return nil, err
		}
		if int(secIdx) >= len(o.Sections) {
			return nil, fmt.Errorf("objfmt: reloc table for invalid section index %d", secIdx)
		}
		nRel, err := r.u16()
		if err != nil {
			return nil, err
		}
		target := o.Sections[secIdx]
		target.Relocs = make([]Reloc, nRel)
		for j := range target.Relocs {
			rl := Reloc{}
			if rl.Off, err = r.u32(); err != nil {
				return nil, err
			}
			if rl.SymIdx, err = r.u32(); err != nil {
				return nil, err
			}
			if rl.Addend, err = r.i32(); err != nil {
				return nil, err
			}
			if rl.Kind, err = r.u8(); err != nil {
				return nil, err
			}
			if rl.Kind != KindAbs32 && rl.Kind != KindRel32 {
				return nil, fmt.Errorf("objfmt: invalid reloc kind %d", rl.Kind)
			}
			if int(rl.SymIdx) >= len(o.Symbols) {
				return nil, fmt.Errorf("objfmt: reloc references missing symbol index %d", rl.SymIdx)
			}
			if uint64(rl.Off)+4 > uint64(len(target.Data)) {
				return nil, fmt.Errorf("objfmt: reloc out of bounds in section %q", target.Name)
			}
			target.Relocs[j] = rl
		}
	}
	if r.p != len(b) {
		return nil, fmt.Errorf("objfmt: %d trailing bytes after object", len(b)-r.p)
	}
	return o, nil
}

// WriteTo is a small convenience used by the CLI.
func WriteTo(o *Object, w io.Writer) (int, error) {
	b, err := Encode(o)
	if err != nil {
		return 0, err
	}
	return w.Write(b)
}
