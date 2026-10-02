package ber

// Limits bounds the resources a single decode/encode operation may consume.
// The zero value of every field means "use the default".
type Limits struct {
	// MaxTagBytes caps the number of octets used by a long-form (tag >= 31)
	// tag number, excluding the leading identifier octet.
	MaxTagBytes int `json:"max_tag_bytes"`
	// MaxLengthBytes caps the length-of-length octets in long-form lengths.
	MaxLengthBytes int `json:"max_length_bytes"`
	// MaxDepth caps constructed nesting depth (top-level value = depth 1).
	MaxDepth int `json:"max_depth"`
	// MaxNodes caps the total number of TLV nodes in one input.
	MaxNodes int `json:"max_nodes"`
	// MaxInputBytes caps the accepted input size and any single content length.
	MaxInputBytes int `json:"max_input_bytes"`
	// MaxIntegerBytes caps INTEGER content octets.
	MaxIntegerBytes int `json:"max_integer_bytes"`
	// MaxBitStringBytes caps BIT STRING payload octets (excluding the
	// unused-bits octet).
	MaxBitStringBytes int `json:"max_bitstring_bytes"`
	// AllowIndefinite permits BER indefinite-length constructed values.
	// DER output never uses indefinite lengths regardless of this flag.
	AllowIndefinite bool `json:"allow_indefinite"`
}

// DefaultLimits returns the limits applied when the caller does not override
// them. Values are deliberately generous for protocol work but bounded.
func DefaultLimits() Limits {
	return Limits{
		MaxTagBytes:       4,
		MaxLengthBytes:    8,
		MaxDepth:          32,
		MaxNodes:          10000,
		MaxInputBytes:     1 << 20,
		MaxIntegerBytes:   1024,
		MaxBitStringBytes: 1 << 20,
		AllowIndefinite:   true,
	}
}

// WithDefaults returns a copy of l in which every zero field is replaced by
// its default. AllowIndefinite defaults to true and therefore cannot be
// expressed as a zero value; use DisableIndefinite to turn it off.
func (l Limits) WithDefaults() Limits {
	d := DefaultLimits()
	if l.MaxTagBytes == 0 {
		l.MaxTagBytes = d.MaxTagBytes
	}
	if l.MaxLengthBytes == 0 {
		l.MaxLengthBytes = d.MaxLengthBytes
	}
	if l.MaxDepth == 0 {
		l.MaxDepth = d.MaxDepth
	}
	if l.MaxNodes == 0 {
		l.MaxNodes = d.MaxNodes
	}
	if l.MaxInputBytes == 0 {
		l.MaxInputBytes = d.MaxInputBytes
	}
	if l.MaxIntegerBytes == 0 {
		l.MaxIntegerBytes = d.MaxIntegerBytes
	}
	if l.MaxBitStringBytes == 0 {
		l.MaxBitStringBytes = d.MaxBitStringBytes
	}
	return l
}

// DisableIndefinite returns a copy of l with indefinite-length support off.
func (l Limits) DisableIndefinite() Limits {
	l.AllowIndefinite = false
	return l
}
