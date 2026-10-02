package mbproto

import (
	"strings"
	"testing"
)

func TestExceptionErrorMessage(t *testing.T) {
	err := &ExceptionError{Func: 0x03, Code: ExcIllegalDataAddress}
	msg := err.Error()
	if !strings.Contains(msg, "0x03") || !strings.Contains(msg, "ILLEGAL_DATA_ADDRESS") {
		t.Fatalf("unhelpful message: %q", msg)
	}
}

func TestNames(t *testing.T) {
	if FuncName(FuncReadHoldingRegisters) != "READ_HOLDING_REGISTERS" {
		t.Fatal("fc03 name")
	}
	if FuncName(FuncWriteMultipleRegisters) != "WRITE_MULTIPLE_REGISTERS" {
		t.Fatal("fc16 name")
	}
	if !strings.Contains(FuncName(0x2B), "0x2B") {
		t.Fatal("unknown func name should carry the code")
	}
	if ExceptionCodeName(0x7F) != "UNKNOWN" {
		t.Fatal("unknown exception code")
	}
	for _, c := range []uint8{ExcIllegalFunction, ExcIllegalDataAddress,
		ExcIllegalDataValue, ExcServerDeviceFailure, ExcGatewayTargetNotResponding} {
		if ExceptionCodeName(c) == "UNKNOWN" {
			t.Fatalf("code 0x%02X unnamed", c)
		}
	}
}
