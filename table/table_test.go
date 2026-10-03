package table

import (
	"errors"
	"testing"
)

func TestEntrySize(t *testing.T) {
	e := Entry{Name: "custom-key", Value: "custom-header"}
	if got, want := e.Size(), 10+13+32; got != want {
		t.Fatalf("Size()=%d want %d", got, want)
	}
}

func TestStaticTableLength(t *testing.T) {
	if len(Static) != StaticLen || StaticLen != 61 {
		t.Fatalf("static table: len=%d StaticLen=%d, RFC 7541 requires 61", len(Static), StaticLen)
	}
	// Spot-check normative entries (RFC 7541 Appendix A).
	if Static[0] != (Entry{Name: ":authority"}) {
		t.Fatalf("index 1: %+v", Static[0])
	}
	if Static[7] != (Entry{Name: ":status", Value: "200"}) {
		t.Fatalf("index 8: %+v", Static[7])
	}
	if Static[60] != (Entry{Name: "www-authenticate"}) {
		t.Fatalf("index 61: %+v", Static[60])
	}
}

func TestLookupBoundaries(t *testing.T) {
	tab := NewTable(128)
	if _, err := tab.Lookup(0); !errors.Is(err, ErrIndexZero) {
		t.Fatalf("index 0: got %v, want ErrIndexZero", err)
	}
	if _, err := tab.Lookup(62); !errors.Is(err, ErrIndexOutOfRange) {
		t.Fatalf("index 62 on empty dynamic table: got %v, want ErrIndexOutOfRange", err)
	}
	tab.Dyn.Add(Entry{Name: "a", Value: "b"})
	if _, err := tab.Lookup(62); err != nil {
		t.Fatalf("index 62 after insert: %v", err)
	}
	if _, err := tab.Lookup(63); !errors.Is(err, ErrIndexOutOfRange) {
		t.Fatalf("index 63: got %v, want ErrIndexOutOfRange", err)
	}
	// Huge indexes must not wrap or panic.
	if _, err := tab.Lookup(1 << 62); !errors.Is(err, ErrIndexOutOfRange) {
		t.Fatalf("huge index: got %v, want ErrIndexOutOfRange", err)
	}
}

func TestEvictionByByteCost(t *testing.T) {
	// Capacity holds exactly two 37-byte entries ("a"="b": 1+1+32=34...
	// use names of known cost).
	d := NewDynamic(74)                  // two entries of cost 37
	e := Entry{Name: "abc", Value: "de"} // cost 3+2+32 = 37
	d.Add(e)
	d.Add(Entry{Name: "fgh", Value: "ij"}) // cost 37, total 74
	if d.Len() != 2 || d.Size() != 74 {
		t.Fatalf("after two adds: len=%d size=%d", d.Len(), d.Size())
	}
	d.Add(Entry{Name: "klm", Value: "no"}) // cost 37 -> evict oldest
	if d.Len() != 2 || d.Size() != 74 || d.Evictions != 1 {
		t.Fatalf("after third add: len=%d size=%d evictions=%d", d.Len(), d.Size(), d.Evictions)
	}
	// Oldest entry ("abc") must be gone; newest is index 1.
	if got, _ := d.Get(2); got.Name != "fgh" {
		t.Fatalf("index 2 = %+v, want fgh (oldest evicted)", got)
	}
	if got, _ := d.Get(1); got.Name != "klm" {
		t.Fatalf("index 1 = %+v, want klm", got)
	}
}

func TestOversizeEntryEmptiesTable(t *testing.T) {
	d := NewDynamic(40)
	d.Add(Entry{Name: "a", Value: "b"})                   // cost 34, fits
	d.Add(Entry{Name: "0123456789", Value: "0123456789"}) // cost 52 > 40
	if d.Len() != 0 || d.Size() != 0 {
		t.Fatalf("oversize entry must empty the table: len=%d size=%d", d.Len(), d.Size())
	}
}

func TestShrinkEvicts(t *testing.T) {
	d := NewDynamic(128)
	d.Add(Entry{Name: "one", Value: "1"})   // cost 36
	d.Add(Entry{Name: "two", Value: "2"})   // cost 36
	d.Add(Entry{Name: "three", Value: "3"}) // cost 38
	d.SetMaxSize(40)
	// Only the newest entry (cost 38) survives.
	if d.Len() != 1 || d.Size() != 38 {
		t.Fatalf("after shrink: len=%d size=%d", d.Len(), d.Size())
	}
	if got, _ := d.Get(1); got.Name != "three" {
		t.Fatalf("survivor = %+v, want three", got)
	}
	d.SetMaxSize(0)
	if d.Len() != 0 || d.Size() != 0 {
		t.Fatalf("shrink to 0: len=%d size=%d", d.Len(), d.Size())
	}
}

func TestFindEntryAndName(t *testing.T) {
	tab := NewTable(128)
	if idx := tab.FindEntry(":method", "GET"); idx != 2 {
		t.Fatalf("static exact: idx=%d want 2", idx)
	}
	if idx := tab.FindName("user-agent"); idx != 58 {
		t.Fatalf("static name: idx=%d want 58", idx)
	}
	tab.Dyn.Add(Entry{Name: "x-a", Value: "1"})
	tab.Dyn.Add(Entry{Name: "x-b", Value: "2"})
	if idx := tab.FindEntry("x-a", "1"); idx != 63 {
		t.Fatalf("dynamic exact: idx=%d want 63", idx)
	}
	if idx := tab.FindEntry("x-a", "nope"); idx != 0 {
		t.Fatalf("value mismatch must not match: idx=%d", idx)
	}
	if idx := tab.FindName("x-b"); idx != 62 {
		t.Fatalf("dynamic name: idx=%d want 62", idx)
	}
}
