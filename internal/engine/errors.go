package engine

import (
	"fmt"

	"coaplab/internal/diag"
	"coaplab/internal/wire"
)

// TransferError is a classified block-wise transfer failure. Tests assert
// on Category (stable) rather than message text.
type TransferError struct {
	Code     wire.Code
	Category diag.Category
	Detail   string
	Body     []byte
}

func (e *TransferError) Error() string {
	if e.Category != "" {
		return fmt.Sprintf("engine: transfer failed: %s [%s] %s", e.Code, e.Category, e.Detail)
	}
	return fmt.Sprintf("engine: transfer failed: %s %s", e.Code, e.Detail)
}
