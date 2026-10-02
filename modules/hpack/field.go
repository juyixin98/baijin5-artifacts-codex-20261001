package hpack

// HeaderField is one decoded or to-be-encoded header field.
type HeaderField struct {
	Name      string
	Value     string
	Sensitive bool
}

// Size is the entry's dynamic-table cost in octets:
// len(name) + len(value) + 32 (RFC 7541 section 4.1).
func (f HeaderField) Size() uint32 {
	return uint32(len(f.Name)) + uint32(len(f.Value)) + 32
}

// IsPseudo reports whether the field is an HTTP/2 pseudo-header (":method").
func (f HeaderField) IsPseudo() bool {
	return len(f.Name) > 0 && f.Name[0] == ':'
}
