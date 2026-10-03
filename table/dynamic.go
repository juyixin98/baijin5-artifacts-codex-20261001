package table

// Dynamic is the per-connection dynamic table of RFC 7541 Section 2.3.
// Entries are stored newest-first; eviction removes the oldest entries.
// The byte cost of an entry is Entry.Size() (name + value + 32) and the
// table size is the sum of entry costs, per RFC 7541 Section 4.1.
//
// Dynamic is not safe for concurrent use; each HTTP/2 connection owns its
// own encoder-side and decoder-side instance.
type Dynamic struct {
	entries []Entry // newest first
	size    int
	maxSize int

	// Evictions counts entries removed since construction, for tests
	// and diagnostics.
	Evictions int
}

// NewDynamic returns an empty dynamic table with the given maximum size.
func NewDynamic(maxSize int) *Dynamic {
	return &Dynamic{maxSize: maxSize}
}

// Size returns the current table size in bytes (sum of entry costs).
func (d *Dynamic) Size() int { return d.size }

// MaxSize returns the current maximum table size.
func (d *Dynamic) MaxSize() int { return d.maxSize }

// Len returns the number of entries.
func (d *Dynamic) Len() int { return len(d.entries) }

// SetMaxSize updates the maximum size, evicting oldest entries until the
// table fits (RFC 7541 Section 4.2).
func (d *Dynamic) SetMaxSize(max int) {
	d.maxSize = max
	d.evictTo(d.maxSize)
}

// Add inserts e as the newest entry, evicting oldest entries until the
// table fits within the maximum size. If the entry alone exceeds the
// maximum, the table is emptied and the entry is not added
// (RFC 7541 Section 4.4).
func (d *Dynamic) Add(e Entry) {
	cost := e.Size()
	if cost > d.maxSize {
		d.entries = nil
		d.size = 0
		return
	}
	d.evictTo(d.maxSize - cost)
	d.entries = append([]Entry{e}, d.entries...)
	d.size += cost
}

// evictTo removes oldest entries until size <= target.
func (d *Dynamic) evictTo(target int) {
	for d.size > target && len(d.entries) > 0 {
		last := len(d.entries) - 1
		d.size -= d.entries[last].Size()
		d.entries[last] = Entry{} // release strings
		d.entries = d.entries[:last]
		d.Evictions++
	}
}

// Get returns the entry at 1-based dynamic-table index i (1 = newest).
func (d *Dynamic) Get(i int) (Entry, bool) {
	if i < 1 || i > len(d.entries) {
		return Entry{}, false
	}
	return d.entries[i-1], true
}

// Entries returns a copy of the entries, newest first, for inspection.
func (d *Dynamic) Entries() []Entry {
	out := make([]Entry, len(d.entries))
	copy(out, d.entries)
	return out
}

// Table combines the static table with one dynamic table and resolves
// HPACK indexes: 1..StaticLen are static, StaticLen+1.. are dynamic
// (StaticLen+1 is the newest dynamic entry).
type Table struct {
	Dyn *Dynamic
}

// NewTable returns a Table with a dynamic table of the given max size.
func NewTable(maxDynamicSize int) *Table {
	return &Table{Dyn: NewDynamic(maxDynamicSize)}
}

// MaxIndex returns the largest currently valid index.
func (t *Table) MaxIndex() int {
	return StaticLen + t.Dyn.Len()
}

// Lookup resolves a 1-based HPACK index to an entry.
//
// Failures: ErrIndexZero for index 0, ErrIndexOutOfRange for an index
// beyond the current combined table length.
func (t *Table) Lookup(idx uint64) (Entry, error) {
	if idx == 0 {
		return Entry{}, ErrIndexZero
	}
	if idx <= uint64(StaticLen) {
		return Static[idx-1], nil
	}
	// Compare in uint64 before narrowing to int, so huge indexes cannot
	// wrap or truncate into a valid dynamic index on any platform.
	if idx > uint64(StaticLen+t.Dyn.Len()) {
		return Entry{}, ErrIndexOutOfRange
	}
	e, ok := t.Dyn.Get(int(idx) - StaticLen)
	if !ok {
		return Entry{}, ErrIndexOutOfRange
	}
	return e, nil
}

// FindEntry returns the index of an exact name+value match, or 0.
func (t *Table) FindEntry(name, value string) uint64 {
	for i, e := range Static {
		if e.Name == name && e.Value == value {
			return uint64(i + 1)
		}
	}
	for i, e := range t.Dyn.entries {
		if e.Name == name && e.Value == value {
			return uint64(StaticLen + i + 1)
		}
	}
	return 0
}

// FindName returns the index of any entry with the given name, or 0.
func (t *Table) FindName(name string) uint64 {
	for i, e := range Static {
		if e.Name == name {
			return uint64(i + 1)
		}
	}
	for i, e := range t.Dyn.entries {
		if e.Name == name {
			return uint64(StaticLen + i + 1)
		}
	}
	return 0
}
