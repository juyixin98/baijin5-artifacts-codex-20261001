// Package runtime executes linked images on a small stack machine.
package runtime

import (
	"encoding/binary"
	"fmt"

	"mylnk/internal/ir"
	"mylnk/internal/linker"
)

// DefaultMaxSteps bounds execution so non-terminating programs fail loudly.
const DefaultMaxSteps = 1_000_000

// TraceStep records one executed instruction for progress logs.
type TraceStep struct {
	PC     uint32
	Op     byte
	StackN int
}

// Result is the VM run outcome.
type Result struct {
	Return int32
	Halted bool
	Steps  int
	Trace  []TraceStep
}

// Run executes image starting at its entry PC.
func Run(img *linker.Image, maxSteps int, recordTrace bool) (*Result, error) {
	if maxSteps <= 0 {
		maxSteps = DefaultMaxSteps
	}
	var data []int32
	calls := []uint32{}
	pc := img.EntryPC
	res := &Result{}
	if pc == 0 {
		return nil, &ir.LinkError{Kind: ir.KindRuntime, Msg: "entry point resolves to null (weak undefined?)"}
	}

	pop := func(what string) (int32, error) {
		if len(data) == 0 {
			return 0, &ir.LinkError{Kind: ir.KindRuntime,
				Msg: fmt.Sprintf("data stack underflow at pc=%#x (%s)", pc, what)}
		}
		v := data[len(data)-1]
		data = data[:len(data)-1]
		return v, nil
	}
	read32 := func(at uint32) (int32, error) {
		idx := int64(at) - int64(img.Base)
		if idx < 0 || idx+4 > int64(len(img.Memory)) {
			return 0, &ir.LinkError{Kind: ir.KindRuntime, Msg: fmt.Sprintf("read out of bounds at %#x", at)}
		}
		return int32(binary.LittleEndian.Uint32(img.Memory[idx : idx+4])), nil
	}

	for res.Steps < maxSteps {
		pci := int64(pc) - int64(img.Base)
		if pci < 0 || pci >= int64(len(img.Memory)) {
			return nil, &ir.LinkError{Kind: ir.KindRuntime, Msg: fmt.Sprintf("pc %#x out of image", pc)}
		}
		op := img.Memory[pci]
		if recordTrace {
			res.Trace = append(res.Trace, TraceStep{PC: pc, Op: op, StackN: len(data)})
		}
		res.Steps++
		next := pc + 1
		switch op {
		case 0x01: // pushi
			v, err := read32(pc + 1)
			if err != nil {
				return nil, err
			}
			data = append(data, v)
			next = pc + 5
		case 0x02, 0x03, 0x04: // add/sub/mul
			b, err := pop("rhs")
			if err != nil {
				return nil, err
			}
			a, err := pop("lhs")
			if err != nil {
				return nil, err
			}
			switch op {
			case 0x02:
				data = append(data, a+b)
			case 0x03:
				data = append(data, a-b)
			default:
				data = append(data, a*b)
			}
		case 0x05: // call rel32
			rel, err := read32(pc + 1)
			if err != nil {
				return nil, err
			}
			calls = append(calls, pc+5)
			pc = pc + 5 + uint32(rel)
			continue
		case 0x06: // ret
			v, err := pop("ret")
			if err != nil {
				return nil, err
			}
			if len(calls) == 0 {
				// ret from entry: program finishes with return value.
				res.Return = v
				res.Halted = true
				return res, nil
			}
			pc = calls[len(calls)-1]
			calls = calls[:len(calls)-1]
			data = append(data, v)
			continue
		case 0x07: // load rel32 -> push word at pc+5+rel
			rel, err := read32(pc + 1)
			if err != nil {
				return nil, err
			}
			at := pc + 5 + uint32(rel)
			v, err := read32(at)
			if err != nil {
				return nil, err
			}
			data = append(data, v)
			next = pc + 5
		case 0x08: // callind
			addr, err := pop("callind target")
			if err != nil {
				return nil, err
			}
			if addr == 0 {
				return nil, &ir.LinkError{Kind: ir.KindRuntime, Msg: fmt.Sprintf("indirect call through null at pc=%#x", pc)}
			}
			calls = append(calls, pc+1)
			pc = uint32(addr)
			continue
		case 0x09: // jz rel32 (peek: condition stays on stack)
			if len(data) == 0 {
				return nil, &ir.LinkError{Kind: ir.KindRuntime,
					Msg: fmt.Sprintf("jz on empty stack at pc=%#x", pc)}
			}
			cond := data[len(data)-1]
			rel, err := read32(pc + 1)
			if err != nil {
				return nil, err
			}
			next = pc + 5
			if cond == 0 {
				pc = pc + 5 + uint32(rel)
				continue
			}
		case 0x0A: // pop
			if _, err := pop("pop"); err != nil {
				return nil, err
			}
		case 0x0F: // halt
			v, err := pop("halt")
			if err != nil {
				return nil, err
			}
			res.Return = v
			res.Halted = true
			return res, nil
		default:
			return nil, &ir.LinkError{Kind: ir.KindRuntime,
				Msg: fmt.Sprintf("illegal opcode %#x at pc=%#x", op, pc)}
		}
		pc = next
	}
	return nil, &ir.LinkError{Kind: ir.KindRuntime,
		Msg: fmt.Sprintf("step limit %d exceeded", maxSteps)}
}
