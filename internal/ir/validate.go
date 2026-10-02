package ir

import "fmt"

// Validate structurally enforces the mask-safety discipline:
//   - positive width, positive register counts;
//   - VLOAD/VSTORE/VBIN(/,% masked with an explicit predicate;
//   - masks are reachable by derivation from VMASKTAIL;
//   - reduce operators and targets are present.
//
// Validation failures are REJECT-class: the lowering produced an IR that
// violates the restricted machine contract.
func (p *Program) Validate() error {
	if p.Width <= 0 {
		return fmt.Errorf("ir: width must be positive, got %d", p.Width)
	}
	if p.NumRegs < 0 || p.NumMasks < 0 {
		return fmt.Errorf("ir: negative register count")
	}
	if p.ReductionOrder == "" {
		return fmt.Errorf("ir: reduction order rule is not declared")
	}
	definedMasks := map[Mask]bool{}
	definedRegs := map[Reg]bool{}
	checkReg := func(r Reg, where string) error {
		if int(r) >= p.NumRegs {
			return fmt.Errorf("ir: %s references undefined value reg r%d", where, r)
		}
		definedRegs[r] = true
		return nil
	}
	for i, in := range p.Body {
		if in.MDst != 0 || in.Dst != 0 {
			// destinations validated per op
		}
		switch in.Op {
		case OpMaskTail:
			if int(in.MDst) >= p.NumMasks {
				return fmt.Errorf("ir: instr %d mask dst out of range", i)
			}
			definedMasks[in.MDst] = true
		case OpConst, OpIVar:
			if err := checkReg(in.Dst, "dst"); err != nil {
				return err
			}
		case OpLoadS, OpLoad:
			if in.Name == "" {
				return fmt.Errorf("ir: instr %d load without array name", i)
			}
			if in.Op == OpLoad {
				if in.Mask == 0 || !definedMasks[in.Mask] {
					return fmt.Errorf("ir: gather instr %d lacks a defined predicate", i)
				}
				if err := checkReg(in.A, "index"); err != nil {
					return err
				}
			}
			if err := checkReg(in.Dst, "dst"); err != nil {
				return err
			}
		case OpBin:
			if in.Bop == Sel {
				if in.Mask == 0 || !definedMasks[in.Mask] {
					return fmt.Errorf("ir: select instr %d lacks predicate", i)
				}
				if err := checkReg(in.A, "a"); err != nil {
					return err
				}
				if err := checkReg(in.B, "b"); err != nil {
					return err
				}
				if err := checkReg(in.Dst, "dst"); err != nil {
					return err
				}
				break
			}
			if in.Bop == Quo || in.Bop == Rem {
				if in.Mask == 0 || !definedMasks[in.Mask] {
					return fmt.Errorf("ir: trapping arithmetic instr %d lacks predicate", i)
				}
			}
			if in.Mask != 0 && !definedMasks[in.Mask] {
				return fmt.Errorf("ir: instr %d references undefined mask", i)
			}
			if err := checkReg(in.A, "a"); err != nil {
				return err
			}
			if err := checkReg(in.B, "b"); err != nil {
				return err
			}
			if err := checkReg(in.Dst, "dst"); err != nil {
				return err
			}
		case OpCmp:
			if in.Mask == 0 || !definedMasks[in.Mask] {
				return fmt.Errorf("ir: compare instr %d lacks predicate", i)
			}
			if int(in.MDst) >= p.NumMasks {
				return fmt.Errorf("ir: compare mask dst out of range")
			}
			if err := checkReg(in.A, "a"); err != nil {
				return err
			}
			if err := checkReg(in.B, "b"); err != nil {
				return err
			}
			definedMasks[in.MDst] = true
		case OpAnd:
			if !definedMasks[in.Mask] {
				return fmt.Errorf("ir: and instr %d operand mask undefined", i)
			}
			// second operand encoded in Imm as raw mask id
			if !definedMasks[Mask(in.Imm)] {
				return fmt.Errorf("ir: and instr %d second mask undefined", i)
			}
			if int(in.MDst) >= p.NumMasks {
				return fmt.Errorf("ir: and mask dst out of range")
			}
			definedMasks[in.MDst] = true
		case OpNot:
			if in.Mask == 0 || !definedMasks[in.Mask] {
				return fmt.Errorf("ir: not instr %d operand mask undefined", i)
			}
			if int(in.MDst) >= p.NumMasks {
				return fmt.Errorf("ir: not mask dst out of range")
			}
			definedMasks[in.MDst] = true
		case OpStore:
			if in.Name == "" {
				return fmt.Errorf("ir: store instr %d without array name", i)
			}
			if in.Mask == 0 || !definedMasks[in.Mask] {
				return fmt.Errorf("ir: store instr %d lacks predicate", i)
			}
			if err := checkReg(in.A, "index"); err != nil {
				return err
			}
			if err := checkReg(in.B, "value"); err != nil {
				return err
			}
		case OpReduce:
			// reduced in a separate pass; validated with program metadata
		default:
			return fmt.Errorf("ir: unknown opcode %q", in.Op)
		}
	}
	for _, r := range p.Reduces {
		if r.Rop != "+" && r.Rop != "*" && r.Rop != "concat" {
			return fmt.Errorf("ir: invalid reduce op %q", r.Rop)
		}
		if r.Target == "" || r.Source == "" {
			return fmt.Errorf("ir: reduce missing target/source")
		}
	}
	return nil
}

func fmtProgram(p *Program) string {
	return fmt.Sprintf("ir.Program{width:%d end:%s body:%d reduces:%d}", p.Width, p.EndName, len(p.Body), len(p.Reduces))
}
