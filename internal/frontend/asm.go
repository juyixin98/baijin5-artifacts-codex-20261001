// Package frontend implements the "mkasm" assembly language and turns it
// into relocatable objects of the MKOB v1 format.
//
// Grammar (one directive/instruction per line, # comments):
//
//	.object <name>
//	.section <name> [keep]
//	.globl <sym>        ; upcoming/existing symbol is strong global
//	.weak <sym>         ; upcoming/existing symbol is weak global
//	.export <sym>       ; symbol participates as a GC root and is exported
//	.weakext <sym>      ; weak undefined external reference (may stay null)
//	<label>:            ; definition inside the current section
//	.word <value|label[+offset]>
//	.zero <n>
//	pushi <imm> | add | sub | mul | call <label> | ret | load <label>
//	callind | jz <label> | pop | halt
package frontend

import (
	"bufio"
	"fmt"
	"io"
	"strconv"
	"strings"

	"mylnk/internal/objfmt"
)

// Assemble parses mkasm source and builds one object.
func Assemble(r io.Reader, objectName string) (*objfmt.Object, error) {
	a := &asm{
		obj:      &objfmt.Object{Name: objectName},
		sym:      map[string]*objfmt.Symbol{},
		secOf:    map[*objfmt.Symbol]*objfmt.Section{},
		weakExt:  map[string]bool{},
		declared: map[string]bool{},
	}
	if err := a.scan(r); err != nil {
		return nil, err
	}
	if a.cur == nil {
		return nil, fmt.Errorf("asm: %s contains no .section", objectName)
	}
	if err := a.resolve(); err != nil {
		return nil, err
	}
	return a.obj, nil
}

type asm struct {
	obj      *objfmt.Object
	cur      *objfmt.Section
	sym      map[string]*objfmt.Symbol
	secOf    map[*objfmt.Symbol]*objfmt.Section
	weakExt  map[string]bool
	declared map[string]bool
	ln       int
}

func (a *asm) scan(r io.Reader) error {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)
	hasObject := false
	for sc.Scan() {
		a.ln++
		line := sc.Text()
		if i := strings.IndexByte(line, '#'); i >= 0 {
			line = line[:i]
		}
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		if strings.HasSuffix(fields[0], ":") {
			if err := a.defineLabel(strings.TrimSuffix(fields[0], ":")); err != nil {
				return a.err("%v", err)
			}
			if len(fields) > 1 {
				return a.err("unexpected tokens after label")
			}
			continue
		}
		switch fields[0] {
		case ".object":
			if len(fields) != 2 || hasObject {
				return a.err(".object expects one name")
			}
			a.obj.Name = fields[1]
			hasObject = true
		case ".section":
			if len(fields) < 2 || len(fields) > 3 {
				return a.err(".section <name> [keep]")
			}
			keep := len(fields) == 3
			if keep && fields[2] != "keep" {
				return a.err("unknown .section flag %q", fields[2])
			}
			a.cur = &objfmt.Section{Name: fields[1], Keep: keep}
			a.obj.Sections = append(a.obj.Sections, a.cur)
		case ".globl", ".weak", ".export", ".weakext":
			if len(fields) != 2 {
				return a.err("%s expects one symbol", fields[0])
			}
			if err := a.declare(fields[1], fields[0]); err != nil {
				return a.err("%v", err)
			}
		case ".word":
			if err := a.word(fields[1:]); err != nil {
				return err
			}
		case ".zero":
			if len(fields) != 2 {
				return a.err(".zero <n>")
			}
			n, err := strconv.Atoi(fields[1])
			if err != nil || n < 0 {
				return a.err(".zero expects non-negative integer")
			}
			a.emit(make([]byte, n))
		default:
			if err := a.instruction(fields); err != nil {
				return err
			}
		}
	}
	return sc.Err()
}

func (a *asm) err(format string, args ...any) error {
	return fmt.Errorf("asm line %d: %w", a.ln, fmt.Errorf(format, args...))
}

func (a *asm) emit(b []byte) {
	a.cur.Data = append(a.cur.Data, b...)
}

func (a *asm) here() uint32 { return uint32(len(a.cur.Data)) }

func (a *asm) declare(name, dir string) error {
	a.declared[name] = true
	if a.cur == nil && dir != ".weakext" && dir != ".globl" && dir != ".weak" {
		return fmt.Errorf("%s before any .section", dir)
	}
	sy := a.lookup(name)
	switch dir {
	case ".globl":
		sy.Bind = objfmt.BindStrong
	case ".weak":
		sy.Bind = objfmt.BindWeak
	case ".export":
		sy.Bind = objfmt.BindStrong
		sy.Export = true
	case ".weakext":
		if sy.Def {
			return fmt.Errorf(".weakext %s conflicts with definition", name)
		}
		a.weakExt[name] = true
		sy.Bind = objfmt.BindWeak
	}
	return nil
}

func (a *asm) lookup(name string) *objfmt.Symbol {
	if sy, ok := a.sym[name]; ok {
		return sy
	}
	sy := &objfmt.Symbol{Name: name, Bind: objfmt.BindLocal, SecIdx: objfmt.UndefSec}
	a.sym[name] = sy
	a.obj.Symbols = append(a.obj.Symbols, sy)
	return sy
}

// lookupRef resolves a name used at a reference site. Symbols first seen
// as references are implicit strong globals; .weakext/.weak can downgrade.
func (a *asm) lookupRef(name string) *objfmt.Symbol {
	sy, ok := a.sym[name]
	if !ok {
		sy = &objfmt.Symbol{Name: name, Bind: objfmt.BindStrong, SecIdx: objfmt.UndefSec}
		a.sym[name] = sy
		a.obj.Symbols = append(a.obj.Symbols, sy)
	}
	return sy
}

func (a *asm) defineLabel(name string) error {
	if a.cur == nil {
		return fmt.Errorf("label %s before any .section", name)
	}
	sy := a.lookupRef(name)
	if sy.Def {
		return fmt.Errorf("duplicate label %s", name)
	}
	if !a.declared[name] {
		sy.Bind = objfmt.BindLocal
	}
	if sy.Bind == objfmt.BindWeak && !sy.Export {
		// A .weakext reference is explicitly an undefined weak external.
		// .weak can legitimately precede its own weak definition, so only
		// .weakext-marked names conflict; detect them via a marker map.
		if a.weakExt[name] {
			return fmt.Errorf(".weakext %s conflicts with definition", name)
		}
	}
	sy.Def = true
	sy.Off = a.here()
	sy.SecIdx = uint16(a.sectionIndex(a.cur))
	a.secOf[sy] = a.cur
	a.cur.Syms = append(a.cur.Syms, sy)
	return nil
}

// parseRef parses "name" or "name+12" / "name-4".
func parseRef(tok string) (string, int32, error) {
	for i := len(tok) - 1; i > 0; i-- {
		if tok[i] == '+' || tok[i] == '-' {
			name := tok[:i]
			n, err := strconv.Atoi(tok[i:])
			if err != nil {
				return "", 0, fmt.Errorf("bad reference %q", tok)
			}
			return name, int32(n), nil
		}
	}
	return tok, 0, nil
}

func (a *asm) word(args []string) error {
	if a.cur == nil {
		return a.err(".word before any .section")
	}
	if len(args) != 1 {
		return a.err(".word expects one value")
	}
	tok := args[0]
	if n, err := strconv.Atoi(tok); err == nil {
		var b [4]byte
		putI32(b[:], int32(n))
		a.emit(b[:])
		return nil
	}
	name, add, err := parseRef(tok)
	if err != nil {
		return a.err("%v", err)
	}
	sy := a.lookupRef(name)
	off := a.here()
	a.emit([]byte{0, 0, 0, 0})
	a.cur.Relocs = append(a.cur.Relocs, objfmt.Reloc{
		Off: off, SymIdx: a.symIdx(sy), Addend: add, Kind: objfmt.KindAbs32,
	})
	return nil
}

func (a *asm) sectionIndex(s *objfmt.Section) int {
	for i, c := range a.obj.Sections {
		if c == s {
			return i
		}
	}
	return -1
}

func (a *asm) symIdx(sy *objfmt.Symbol) uint32 {
	for i, s := range a.obj.Symbols {
		if s == sy {
			return uint32(i)
		}
	}
	panic("symbol not in table")
}

func putI32(b []byte, v int32) {
	b[0] = byte(v)
	b[1] = byte(v >> 8)
	b[2] = byte(v >> 16)
	b[3] = byte(v >> 24)
}

func putU32(b []byte, v uint32) { putI32(b, int32(v)) }

func (a *asm) instruction(f []string) error {
	if a.cur == nil {
		return a.err("instruction before any .section")
	}
	op := f[0]
	need := func(n int) error {
		if len(f)-1 != n {
			return a.err("%s expects %d operand(s)", op, n)
		}
		return nil
	}
	emitRel := func(tok string) error {
		name, add, err := parseRef(tok)
		if err != nil {
			return a.err("%v", err)
		}
		sy := a.lookupRef(name)
		idx := a.symIdx(sy)
		// Operand (rel32) starts one byte after the opcode just emitted.
		operandOff := a.here() - 4
		a.cur.Relocs = append(a.cur.Relocs, objfmt.Reloc{
			Off: operandOff, SymIdx: idx, Addend: add, Kind: objfmt.KindRel32,
		})
		return nil
	}
	switch op {
	case "pushi":
		if err := need(1); err != nil {
			return err
		}
		n, err := strconv.Atoi(f[1])
		if err != nil {
			return a.err("pushi expects integer, got %q", f[1])
		}
		a.emit([]byte{OpPush})
		var b [4]byte
		putI32(b[:], int32(n))
		a.emit(b[:])
	case "add", "sub", "mul", "ret", "callind", "pop", "halt":
		if err := need(0); err != nil {
			return err
		}
		switch op {
		case "add":
			a.emit([]byte{OpAdd})
		case "sub":
			a.emit([]byte{OpSub})
		case "mul":
			a.emit([]byte{OpMul})
		case "ret":
			a.emit([]byte{OpRet})
		case "callind":
			a.emit([]byte{OpCallInd})
		case "pop":
			a.emit([]byte{OpPop})
		case "halt":
			a.emit([]byte{OpHalt})
		}
	case "call", "jz", "load":
		if err := need(1); err != nil {
			return err
		}
		var opcode byte
		switch op {
		case "call":
			opcode = OpCall
		case "jz":
			opcode = OpJz
		case "load":
			opcode = OpLoad
		}
		a.emit([]byte{opcode, 0, 0, 0, 0})
		if err := emitRel(f[1]); err != nil {
			return err
		}
	default:
		return a.err("unknown mnemonic %q", op)
	}
	return nil
}

// resolve patches intra-section rel32 references; cross-section/undefined
// references stay as relocations for the linker.
func (a *asm) resolve() error {
	for _, sec := range a.obj.Sections {
		keep := make([]objfmt.Reloc, 0, len(sec.Relocs))
		for _, rl := range sec.Relocs {
			sy := a.obj.Symbols[rl.SymIdx]
			if a.secOf[sy] == sec {
				if rl.Kind != objfmt.KindRel32 {
					// intra-section absolute reference: patch directly too.
					putI32(sec.Data[rl.Off:rl.Off+4], int32(sy.Off)+rl.Addend)
					continue
				}
				target := int64(sy.Off) + int64(rl.Addend)
				pc := int64(rl.Off) + 4
				putI32(sec.Data[rl.Off:rl.Off+4], int32(target-pc))
				continue
			}
			keep = append(keep, rl)
		}
		sec.Relocs = keep
	}
	return nil
}
