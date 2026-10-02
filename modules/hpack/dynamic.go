package hpack

// dynamicTable is the per-connection HPACK dynamic table (RFC 7541
// sections 2.3.2 and 4.1..4.4).
//
// ents[0] is the oldest entry. Protocol indices run newest-first, so
// dynamic index d maps to ents[len(ents)-d]. Each entry costs
// len(name)+len(value)+32 octets of size budget; shrinking the maximum or
// adding a new entry evicts oldest entries until the table fits. An entry
// larger than the maximum evicts itself too, leaving the table empty, which
// is legal per section 4.4.
type dynamicTable struct {
	ents    []HeaderField
	size    uint32
	maxSize uint32
	// allowedMaxSize is the ceiling negotiated by SETTINGS_HEADER_TABLE_SIZE.
	allowedMaxSize uint32
}

func newDynamicTable(maxSize uint32) *dynamicTable {
	return &dynamicTable{ents: make([]HeaderField, 0, 8), maxSize: maxSize, allowedMaxSize: maxSize}
}

// len returns the number of live entries.
func (t *dynamicTable) len() int { return len(t.ents) }

// at returns the entry at 1-based dynamic index d.
func (t *dynamicTable) at(d int) (HeaderField, bool) {
	if d < 1 || d > len(t.ents) {
		return HeaderField{}, false
	}
	return t.ents[len(t.ents)-d], true
}

// find looks up a field newest-first. It returns the 1-based dynamic index
// and whether the value matched; a name-only match reports valueMatch=false.
func (t *dynamicTable) find(f HeaderField) (index int, valueMatch bool) {
	for i := range t.ents {
		e := &t.ents[len(t.ents)-1-i]
		if e.Name != f.Name {
			continue
		}
		if e.Value == f.Value {
			return i + 1, true
		}
		if index == 0 {
			index = i + 1
		}
	}
	return index, false
}

// setMaxSize applies a new maximum (already checked against the allowed
// ceiling) and evicts until the table fits. It returns the number of
// evicted entries.
func (t *dynamicTable) setMaxSize(v uint32) int {
	t.maxSize = v
	return t.evict()
}

// add inserts f as the newest entry and then evicts oldest entries until
// the size budget is met. It returns the number of entries evicted; when
// that includes f itself (f larger than the maximum) callers must not
// reference f by index.
func (t *dynamicTable) add(f HeaderField) int {
	t.ents = append(t.ents, f)
	t.size += f.Size()
	return t.evict()
}

func (t *dynamicTable) evict() int {
	n := 0
	for t.size > t.maxSize && n < len(t.ents) {
		t.size -= t.ents[n].Size()
		n++
	}
	if n > 0 {
		remaining := copy(t.ents, t.ents[n:])
		t.ents = t.ents[:remaining]
	}
	return n
}
