package core

import (
	"encoding/binary"
)

// HandleResult carries a processed PDU plus audit metadata. It is the
// state machine's only output: the transport layer turns ResponsePDU into
// an MBAP frame, while the audit fields go to logs/SQLite without polluting
// the protocol output.
type HandleResult struct {
	Function    byte
	UnitID      byte
	Address     uint16
	Quantity    uint16
	ResponsePDU []byte
	// Exception is zero for a normal response; otherwise it is the Modbus
	// exception code carried in ResponsePDU.
	Exception byte
}

// IsException reports whether the request was rejected with an exception.
func (r HandleResult) IsException() bool { return r.Exception != 0 }

// Engine is the stateless protocol machine over a unit-id Router. One
// Engine is safe to share across all connections and goroutines; per-bank
// locking lives in RegisterBank.
type Engine struct {
	router *Router
}

// NewEngine wires a protocol engine to a unit-id router.
func NewEngine(router *Router) *Engine {
	if router == nil {
		panic("core: NewEngine requires a non-nil router")
	}
	return &Engine{router: router}
}

// Handle processes exactly one PDU for one unit id. It never returns an
// error: every rejection is encoded as a Modbus exception PDU so that an
// unsupported or malformed request can never be mistaken for ordinary
// data. Truly unexpected internal failures map to exception 0x04.
func (e *Engine) Handle(unitID byte, pdu []byte) HandleResult {
	res := HandleResult{UnitID: unitID}
	if len(pdu) == 0 {
		// No function code at all: report against FC00 as required by the
		// spec framing discussion; treat as illegal value.
		res.Exception = ExcIllegalValue
		res.ResponsePDU = []byte{0x00 | ExceptionBit, ExcIllegalValue}
		return res
	}
	fc := pdu[0]
	res.Function = fc

	bank, ok := e.router.bank(unitID)
	if !ok {
		return res.fail(fc,
			protoError(CatGatewayFailure, ExcGatewayTarget,
				"unit id 0x%02X is not bound to any register bank", unitID))
	}

	switch fc {
	case FCReadHolding:
		return e.handleRead(bank, fc, pdu, res)
	case FCWriteMultiple:
		return e.handleWrite(bank, fc, pdu, res)
	default:
		return res.fail(fc,
			protoError(CatBadFunction, ExcIllegalFunction,
				"function code 0x%02X is not supported", fc))
	}
}

func (r HandleResult) fail(fc byte, err error) HandleResult {
	pe := err.(*ProtocolError)
	r.Exception = pe.Exception
	r.ResponsePDU = ExceptionPDU(fc, err)
	return r
}

// handleRead parses and executes FC03.
//
// PDU request:  FC(1) 03 | start addr (2) | quantity (2)         [5 bytes]
// PDU response: FC 03 | byte count (1) | register values (2*qty)
func (e *Engine) handleRead(bank *RegisterBank, fc byte, pdu []byte, res HandleResult) HandleResult {
	if len(pdu) != 5 {
		return res.fail(fc, protoError(CatBadValue, ExcIllegalValue,
			"FC03 PDU length is %d bytes, want exactly 5", len(pdu)))
	}
	addr := binary.BigEndian.Uint16(pdu[1:3])
	qty := binary.BigEndian.Uint16(pdu[3:5])
	res.Address, res.Quantity = addr, qty

	if qty < 1 {
		return res.fail(fc, protoError(CatBadValue, ExcIllegalValue,
			"FC03 quantity %d is below the minimum of 1", qty))
	}
	if qty > MaxReadQuantity {
		return res.fail(fc, protoError(CatBadValue, ExcIllegalValue,
			"FC03 quantity %d exceeds the maximum of %d", qty, MaxReadQuantity))
	}
	if !spanInBank(bank.Size(), addr, qty) {
		return res.fail(fc, protoError(CatBadAddress, ExcIllegalAddress,
			"FC03 span [0x%04X, +%d) exceeds bank size %d",
			addr, qty, bank.Size()))
	}

	values := bank.readSpan(int(addr), int(qty))
	out := make([]byte, 0, 2+2*len(values))
	out = append(out, fc, byte(2*len(values)))
	for _, v := range values {
		out = append(out, byte(v>>8), byte(v))
	}
	res.ResponsePDU = out
	return res
}

// handleWrite parses and executes FC16 atomically.
//
// PDU request:  FC 10 | start addr (2) | quantity (2) | byte count (1) |
//
//	register values (2*quantity)
//
// PDU response: FC 10 | start addr (2) | quantity (2)   [6 bytes]
func (e *Engine) handleWrite(bank *RegisterBank, fc byte, pdu []byte, res HandleResult) HandleResult {
	if len(pdu) < 6 {
		return res.fail(fc, protoError(CatBadValue, ExcIllegalValue,
			"FC16 PDU length is %d bytes, want at least 6", len(pdu)))
	}
	addr := binary.BigEndian.Uint16(pdu[1:3])
	qty := binary.BigEndian.Uint16(pdu[3:5])
	byteCount := int(pdu[5])
	res.Address, res.Quantity = addr, qty

	if qty < 1 {
		return res.fail(fc, protoError(CatBadValue, ExcIllegalValue,
			"FC16 quantity %d is below the minimum of 1", qty))
	}
	if qty > MaxWriteQuantity {
		return res.fail(fc, protoError(CatBadValue, ExcIllegalValue,
			"FC16 quantity %d exceeds the maximum of %d", qty, MaxWriteQuantity))
	}
	if byteCount != 2*int(qty) {
		return res.fail(fc, protoError(CatBadValue, ExcIllegalValue,
			"FC16 byte count %d does not equal 2*quantity (%d)",
			byteCount, 2*int(qty)))
	}
	// PDU must contain exactly header(6) + data(2*qty) bytes.
	if len(pdu) != 6+2*int(qty) {
		return res.fail(fc, protoError(CatBadValue, ExcIllegalValue,
			"FC16 PDU length %d does not match 6 + byte count %d",
			len(pdu), byteCount))
	}
	if !spanInBank(bank.Size(), addr, qty) {
		return res.fail(fc, protoError(CatBadAddress, ExcIllegalAddress,
			"FC16 span [0x%04X, +%d) exceeds bank size %d",
			addr, qty, bank.Size()))
	}

	values := make([]uint16, qty)
	for i := 0; i < int(qty); i++ {
		values[i] = binary.BigEndian.Uint16(pdu[6+2*i : 8+2*i])
	}
	// Single locked span replacement: either every register changes or
	// (on panic recovery below) none does.
	bank.writeSpan(int(addr), values)

	out := make([]byte, 5)
	out[0] = fc
	binary.BigEndian.PutUint16(out[1:3], addr)
	binary.BigEndian.PutUint16(out[3:5], qty)
	res.ResponsePDU = out
	return res
}

// spanInBank reports whether [addr, addr+qty) fits a bank of size registers
// and cannot overflow the uint16 address arithmetic. qty is already bounded
// to <=125, so addr+qty cannot overflow an int on any platform.
func spanInBank(size int, addr, qty uint16) bool {
	end := int(addr) + int(qty)
	return end <= size
}
