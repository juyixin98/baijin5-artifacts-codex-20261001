package core

import (
	"fmt"
	"sync"
)

// ErrCategory is the machine-readable failure class of a protocol error.
// Independent tests assert on the exact category.
type ErrCategory string

const (
	CatGatewayFailure ErrCategory = "gateway_target_failed"
	CatBadFunction    ErrCategory = "illegal_function"
	CatBadValue       ErrCategory = "illegal_data_value"
	CatBadAddress     ErrCategory = "illegal_data_address"
	CatServerFailure  ErrCategory = "server_device_failure"
)

// ProtocolError is a deterministic PDU-level rejection. Exception is the
// Modbus exception code to put in the response; Category classifies the
// failure for logs and tests.
type ProtocolError struct {
	Exception byte
	Category  ErrCategory
	Detail    string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("core: %s (exception 0x%02X): %s",
		e.Category, e.Exception, e.Detail)
}

func protoError(cat ErrCategory, exception byte, format string, args ...any) error {
	return &ProtocolError{
		Exception: exception,
		Category:  cat,
		Detail:    fmt.Sprintf(format, args...),
	}
}

// ExceptionPDU encodes an exception response PDU for the request function
// code: function code with bit 7 set followed by the exception code.
func ExceptionPDU(requestFC byte, err error) []byte {
	pe, ok := err.(*ProtocolError)
	if !ok {
		return []byte{requestFC | ExceptionBit, ExcServerFailure}
	}
	return []byte{requestFC | ExceptionBit, pe.Exception}
}

// RegisterBank is one holding-register address space. All access is
// serialized by an RWMutex: reads return a copied span under RLock and
// writes replace the whole targeted span under Lock, so a concurrent
// reader can never observe a half-applied FC16 write (atomicity).
type RegisterBank struct {
	mu   sync.RWMutex
	regs []uint16
}

// NewRegisterBank creates a bank of size zero-initialized registers.
func NewRegisterBank(size int) *RegisterBank {
	if size < 0 || size > 0x10000 {
		panic(fmt.Sprintf("core: bank size %d outside [0,65536]", size))
	}
	return &RegisterBank{regs: make([]uint16, size)}
}

// Size is the number of registers (legal addresses 0..Size-1).
func (b *RegisterBank) Size() int {
	return len(b.regs)
}

// Preset replaces the bank contents for controlled-fixture setup. It takes
// values from the beginning; extra bank registers stay zero. Preset is a
// setup path, not part of the Modbus request path.
func (b *RegisterBank) Preset(values []uint16) {
	b.mu.Lock()
	defer b.mu.Unlock()
	n := copy(b.regs, values)
	// Registers beyond the preset list are explicitly zeroed.
	for i := n; i < len(b.regs); i++ {
		b.regs[i] = 0
	}
}

// Snapshot returns a copy of the whole bank, used by the control plane and
// tests. The copy can be read without holding the bank lock.
func (b *RegisterBank) Snapshot() []uint16 {
	b.mu.RLock()
	defer b.mu.RUnlock()
	out := make([]uint16, len(b.regs))
	copy(out, b.regs)
	return out
}

// readSpan copies registers [addr, addr+qty). The caller guarantees the
// span is in range.
func (b *RegisterBank) readSpan(addr, qty int) []uint16 {
	b.mu.RLock()
	defer b.mu.RUnlock()
	out := make([]uint16, qty)
	copy(out, b.regs[addr:addr+qty])
	return out
}

// writeSpan applies all values at addr or none at all. The single Lock
// acquisition across the full span is what makes FC16 atomic against
// concurrent requests; the caller has already validated the span.
func (b *RegisterBank) writeSpan(addr int, values []uint16) {
	b.mu.Lock()
	defer b.mu.Unlock()
	copy(b.regs[addr:], values)
}

// Router maps unit ids to register banks. It is safe for concurrent use:
// the binding table is configured at startup and read on every request.
type Router struct {
	mu    sync.RWMutex
	units map[byte]*RegisterBank
}

// NewRouter creates an empty unit-id router.
func NewRouter() *Router {
	return &Router{units: make(map[byte]*RegisterBank)}
}

// Bind attaches a bank to a unit id. Rebinding the same unit id replaces
// the previous bank.
func (r *Router) Bind(unitID byte, bank *RegisterBank) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.units[unitID] = bank
}

func (r *Router) bank(unitID byte) (*RegisterBank, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	b, ok := r.units[unitID]
	return b, ok
}

// Lookup returns the register bank bound to unitID. It is used by the
// control plane to serve register snapshots; request processing uses the
// internal lookup.
func (r *Router) Lookup(unitID byte) (*RegisterBank, bool) {
	return r.bank(unitID)
}
