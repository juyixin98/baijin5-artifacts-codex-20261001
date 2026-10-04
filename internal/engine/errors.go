package engine

import (
	"genfsm/internal/semerr"
)

func errUnknownEngine(e Engine) error {
	return semerr.Input(semerr.CodeBadRequest, "unknown engine %q (want fsm|ref)", string(e))
}
