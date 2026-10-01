package runtime

import (
	"fmt"

	"scopelang/internal/diag"
)

func loadErr(path string, err error) *diag.Error {
	return diag.New(diag.Input, "PROGRAM_READ",
		fmt.Sprintf("cannot read program %s: %v", path, err)).
		With(diag.PhaseConfig, "", 0)
}
