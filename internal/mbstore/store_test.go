package mbstore

import (
	"errors"
	"sync"
	"testing"
)

func openTest(t *testing.T, size uint16) *Store {
	t.Helper()
	s, err := Open(":memory:", size)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	if err := s.EnsureUnit(1); err != nil {
		t.Fatalf("ensure unit: %v", err)
	}
	return s
}

func TestReadWriteRoundTrip(t *testing.T) {
	s := openTest(t, 16)
	if err := s.WriteMultiple(1, 2, []uint16{10, 258, 65535}); err != nil {
		t.Fatalf("write: %v", err)
	}
	got, err := s.Read(1, 2, 3)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	want := []uint16{10, 258, 65535}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("reg %d: got %d want %d", i, got[i], want[i])
		}
	}
}

func TestAddressRangeChecked(t *testing.T) {
	s := openTest(t, 8)
	if _, err := s.Read(1, 7, 2); !errors.Is(err, ErrAddressRange) {
		t.Fatalf("read past end: got %v", err)
	}
	if err := s.WriteMultiple(1, 8, []uint16{1}); !errors.Is(err, ErrAddressRange) {
		t.Fatalf("write at end: got %v", err)
	}
	// Boundary: the last register alone is valid.
	if err := s.WriteMultiple(1, 7, []uint16{1}); err != nil {
		t.Fatalf("write last register: %v", err)
	}
}

// TestWriteMultipleAtomicOnFailure writes a range that ends out of bounds:
// the whole write must be rejected and no register may change.
func TestWriteMultipleAtomicOnFailure(t *testing.T) {
	s := openTest(t, 8)
	if err := s.WriteMultiple(1, 0, []uint16{1, 1, 1, 1}); err != nil {
		t.Fatalf("seed: %v", err)
	}
	err := s.WriteMultiple(1, 6, []uint16{9, 9, 9}) // 6,7,8 -> 8 out of range
	if !errors.Is(err, ErrAddressRange) {
		t.Fatalf("got %v, want ErrAddressRange", err)
	}
	got, _ := s.Read(1, 0, 8)
	want := []uint16{1, 1, 1, 1, 0, 0, 0, 0}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("reg %d changed to %d; write was not atomic (want %v)", i, got[i], want)
		}
	}
}

// TestConcurrentReadWriteConsistency hammers one 4-register window with
// atomic writes of uniform values while readers check they never observe a
// mixture of two generations.
func TestConcurrentReadWriteConsistency(t *testing.T) {
	s := openTest(t, 8)
	var writers, readers sync.WaitGroup
	stop := make(chan struct{})
	for w := 0; w < 2; w++ {
		writers.Add(1)
		go func(gen uint16) {
			defer writers.Done()
			v := gen*100 + 1
			for {
				select {
				case <-stop:
					return
				default:
				}
				// All four registers carry the same value per generation,
				// so any mixed read proves a torn write.
				if err := s.WriteMultiple(1, 0, []uint16{v, v, v, v}); err != nil {
					t.Errorf("write: %v", err)
					return
				}
			}
		}(uint16(w))
	}
	for r := 0; r < 4; r++ {
		readers.Add(1)
		go func() {
			defer readers.Done()
			for i := 0; i < 200; i++ {
				vals, err := s.Read(1, 0, 4)
				if err != nil {
					t.Errorf("read: %v", err)
					return
				}
				for _, v := range vals[1:] {
					if v != vals[0] {
						t.Errorf("torn read: %v", vals)
						return
					}
				}
			}
		}()
	}
	readers.Wait() // readers run a fixed number of iterations
	close(stop)    // then release the writers
	writers.Wait()
}
