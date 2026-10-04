package vm

import (
	"genfsm/internal/ir"
	"genfsm/internal/value"
)

// unwind propagates a signal through f's dynamic handlers. It returns a
// frameResult with kind sigNormal when the signal was consumed (a catch
// handled an exception and execution continues), otherwise the terminal
// result that runWith should return.
func (m *Machine) unwind(f *frame, sig signal) frameResult {
	for len(f.handlers) > 0 {
		h := f.handlers[len(f.handlers)-1]
		if h.kind == ir.HandlerIter {
			// for-loop: close the child generator (idempotent). A cleanup
			// failure becomes the propagating exception.
			closeErr := f.closeIterChild(h)
			f.popHandler()
			if closeErr.Tag == value.Exception {
				sig = signal{kind: sigThrow, ex: closeErr}
			}
			continue
		}
		// try handler
		if h.phase == 0 {
			if sig.kind == sigThrow && h.jCatch != -1 {
				// Exceptions thrown from the try body enter the catch entry.
				// Replace the body-phase handler by a cleanup-phase one so the
				// catch body is itself covered by the same finally.
				f.popHandler()
				nh := &hEntry{
					kind:      ir.HandlerTry,
					jCatch:    -1,
					jFinally:  h.jFinally,
					phase:     1,
					paramSlot: h.paramSlot,
					genSlot:   h.genSlot,
				}
				f.pushHandler(nh)
				f.push(sig.ex)
				f.pc = h.jCatch
				return frameResult{kind: sigNormal}
			}
			if h.jFinally != -1 {
				h.phase = 1
				h.pending = sig
				f.pc = h.jFinally
				return frameResult{kind: sigNormal}
			}
			// Handler neither catches this signal nor has a finally: keep
			// looking outward.
			f.popHandler()
			continue
		}
		// phase 1: the cleanup (finally body) itself raised/returned. Drop the
		// stored continuation and propagate the new signal outward.
		f.popHandler()
	}
	switch sig.kind {
	case sigReturn:
		return frameResult{kind: sigReturn, val: sig.val}
	case sigThrow:
		return frameResult{kind: sigThrow, ex: sig.ex}
	case sigClose:
		return frameResult{kind: sigClose}
	}
	return frameResult{kind: sigThrow, ex: value.ExV("InternalError", "unwind: bad signal")}
}

// closeIterChild closes a for-loop child generator while unwinding. Closing
// a finished/closed child is a no-op. A failed cleanup returns the exception.
func (f *frame) closeIterChild(h *hEntry) value.Value {
	gv := f.locals[h.genSlot]
	if gv.Tag != value.Generator || gv.Gen == nil {
		return value.Value{}
	}
	res := gv.Gen.Close()
	if res.State == value.StateError {
		return value.ExV(res.ExName, res.ExMessage)
	}
	return value.Value{}
}
