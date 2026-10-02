// Package mbproto defines the Modbus protocol constants, exception codes and
// shared error types used by both the master (client) and slave (server)
// halves of the fixture. It contains no I/O; it is the vocabulary layer.
package mbproto

import "fmt"

// Supported function codes.
const (
	FuncReadHoldingRegisters   uint8 = 0x03
	FuncWriteMultipleRegisters uint8 = 0x10
)

// Exception codes returned in exception responses (function | 0x80).
const (
	ExcIllegalFunction            uint8 = 0x01
	ExcIllegalDataAddress         uint8 = 0x02
	ExcIllegalDataValue           uint8 = 0x03
	ExcServerDeviceFailure        uint8 = 0x04
	ExcGatewayTargetNotResponding uint8 = 0x0B
)

// Protocol limits (Modbus Application Protocol Specification V1.1b3).
const (
	MaxReadQuantity  = 125            // FC03 quantity upper bound
	MaxWriteQuantity = 123            // FC16 quantity upper bound
	MaxPDUSize       = 253            // serial-line PDU ceiling, reused for TCP
	MaxMBAPLength    = 1 + MaxPDUSize // MBAP Length field = UnitID + PDU
	MinMBAPLength    = 2              // UnitID + at least a function byte
	ProtocolIDModbus = 0              // MBAP Protocol ID must be zero
	MaxRegisterValue = 0xFFFF         // registers are 16-bit, big-endian
)

// ExceptionError is returned to a master when the slave answers with an
// exception response instead of a normal data response.
type ExceptionError struct {
	Func uint8 // original function code (without the 0x80 flag)
	Code uint8 // exception code
}

func (e *ExceptionError) Error() string {
	return fmt.Sprintf("modbus exception: func=0x%02X code=0x%02X (%s)",
		e.Func, e.Code, ExceptionCodeName(e.Code))
}

// ExceptionCodeName renders a human-readable name for logs and CLI output.
func ExceptionCodeName(code uint8) string {
	switch code {
	case ExcIllegalFunction:
		return "ILLEGAL_FUNCTION"
	case ExcIllegalDataAddress:
		return "ILLEGAL_DATA_ADDRESS"
	case ExcIllegalDataValue:
		return "ILLEGAL_DATA_VALUE"
	case ExcServerDeviceFailure:
		return "SERVER_DEVICE_FAILURE"
	case ExcGatewayTargetNotResponding:
		return "GATEWAY_TARGET_NOT_RESPONDING"
	default:
		return "UNKNOWN"
	}
}

// FuncName renders a human-readable function name for logs.
func FuncName(f uint8) string {
	switch f {
	case FuncReadHoldingRegisters:
		return "READ_HOLDING_REGISTERS"
	case FuncWriteMultipleRegisters:
		return "WRITE_MULTIPLE_REGISTERS"
	default:
		return fmt.Sprintf("FUNC_0x%02X", f)
	}
}
