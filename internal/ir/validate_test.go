package ir

import "testing"

func baseProgram() *Program {
	return &Program{
		Width: 4, EndName: "a", ReductionOrder: "LEFT_FOLD_ASCENDING_INDEX",
		NumRegs: 4, NumMasks: 2,
		Body: []Instr{
			{Op: OpMaskTail, MDst: 1, Imm: 4},
			{Op: OpConst, Dst: 0, Imm: 1},
			{Op: OpConst, Dst: 1, Imm: 2},
		},
	}
}

func TestValidateAcceptsMaskedProgram(t *testing.T) {
	p := baseProgram()
	p.Body = append(p.Body,
		Instr{Op: OpLoad, Dst: 2, A: 0, Name: "a", Mask: 1},
		Instr{Op: OpBin, Dst: 3, A: 2, B: 1, Bop: Quo, Mask: 1},
	)
	if err := p.Validate(); err != nil {
		t.Fatalf("valid masked program rejected: %v", err)
	}
}

func TestValidateRejectsUnmaskedGather(t *testing.T) {
	p := baseProgram()
	p.NumRegs = 3
	p.Body = append(p.Body, Instr{Op: OpLoad, Dst: 2, A: 0, Name: "a", Mask: 0})
	if err := p.Validate(); err == nil {
		t.Fatal("unmasked gather must be rejected")
	}
}

func TestValidateRejectsUnmaskedDivision(t *testing.T) {
	p := baseProgram()
	p.NumRegs = 4
	p.Body = append(p.Body, Instr{Op: OpBin, Dst: 3, A: 0, B: 1, Bop: Quo, Mask: 0})
	if err := p.Validate(); err == nil {
		t.Fatal("unmasked division must be rejected")
	}
}

func TestValidateRejectsMissingReductionOrder(t *testing.T) {
	p := baseProgram()
	p.ReductionOrder = ""
	if err := p.Validate(); err == nil {
		t.Fatal("missing reduction order declaration must be rejected")
	}
}
