package store_test

import (
	"bytes"
	"errors"
	"testing"

	"coaplab/internal/etag"
	"coaplab/internal/store"
)

func TestPutGet_VersionBumpsAndETagBound(t *testing.T) {
	st := openStore(t)

	r1, err := st.Put("doc", 0, []byte("v1-body"))
	if err != nil {
		t.Fatal(err)
	}
	if r1.Version != 1 {
		t.Fatalf("first version = %d, want 1", r1.Version)
	}
	if !bytes.Equal(r1.ETag, etag.Compute(0, []byte("v1-body"))) {
		t.Fatal("etag not bound to bytes")
	}

	r2, err := st.Put("doc", 0, []byte("v2-body-longer"))
	if err != nil {
		t.Fatal(err)
	}
	if r2.Version != 2 {
		t.Fatalf("second version = %d, want 2", r2.Version)
	}
	if bytes.Equal(r1.ETag, r2.ETag) {
		t.Fatal("update must change etag")
	}

	got, err := st.Get("doc")
	if err != nil {
		t.Fatal(err)
	}
	if got.Version != 2 || !bytes.Equal(got.Body, []byte("v2-body-longer")) ||
		!bytes.Equal(got.ETag, r2.ETag) {
		t.Fatalf("stale/incorrect read: %+v", got)
	}
}

func TestGet_NotFound(t *testing.T) {
	st := openStore(t)
	if _, err := st.Get("missing"); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}

func TestDelete(t *testing.T) {
	st := openStore(t)
	if _, err := st.Put("d", 0, []byte("x")); err != nil {
		t.Fatal(err)
	}
	ok, err := st.Delete("d")
	if err != nil || !ok {
		t.Fatalf("delete existing ok=%v err=%v", ok, err)
	}
	ok2, _ := st.Delete("d")
	if ok2 {
		t.Fatal("second delete must report absent")
	}
}

// Two in-memory stores opened in parallel are isolated (unique DSN).
func TestMemoryStores_AreIsolated(t *testing.T) {
	a := openStore(t)
	b := openStore(t)
	if _, err := a.Put("only-in-a", 0, []byte("a")); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Get("only-in-a"); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("parallel memory stores leaked: %v", err)
	}
}

// The stored body cannot be mutated through the returned slice.
func TestPut_DefensiveCopy(t *testing.T) {
	st := openStore(t)
	body := []byte("original")
	if _, err := st.Put("m", 0, body); err != nil {
		t.Fatal(err)
	}
	body[0] = 'Z'
	got, _ := st.Get("m")
	if got.Body[0] != 'o' {
		t.Fatal("store retained caller's mutable slice")
	}
}

func TestPaths_ListsSorted(t *testing.T) {
	st := openStore(t)
	for _, p := range []string{"c", "a", "b"} {
		if _, err := st.Put(p, 0, []byte(p)); err != nil {
			t.Fatal(err)
		}
	}
	got, err := st.Paths()
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 || got[0] != "a" || got[1] != "b" || got[2] != "c" {
		t.Fatalf("paths not sorted: %v", got)
	}
}

func openStore(t *testing.T) *store.Store {
	t.Helper()
	st, err := store.Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })
	return st
}
