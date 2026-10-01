package store_test

import (
	"context"
	"path/filepath"
	"testing"

	"socks5d.local/socks5d/internal/config"
	"socks5d.local/socks5d/internal/store"
)

func openStore(t *testing.T) *store.Store {
	t.Helper()
	ctx := context.Background()
	st, err := store.Open(ctx, filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })
	return st
}

func TestRules_ReplaceAndList(t *testing.T) {
	ctx := context.Background()
	st := openStore(t)

	first := []config.Rule{
		{Kind: "cidr", Value: "127.0.0.0/8", Note: "loopback"},
		{Kind: "domain", Value: "echo.local", Mode: "exact"},
	}
	if err := st.ReplaceRules(ctx, first); err != nil {
		t.Fatalf("replace first: %v", err)
	}
	got, err := st.ListRules(ctx)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("rules = %d want 2", len(got))
	}

	// Replace must clear old rows, not append.
	second := []config.Rule{{Kind: "cidr", Value: "::1/128"}}
	if err := st.ReplaceRules(ctx, second); err != nil {
		t.Fatalf("replace second: %v", err)
	}
	got, err = st.ListRules(ctx)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if len(got) != 1 || got[0].Value != "::1/128" {
		t.Fatalf("rules not replaced atomically: %+v", got)
	}
}

func TestAudit_InsertAndQueryByReqID(t *testing.T) {
	ctx := context.Background()
	st := openStore(t)

	rows := []store.AuditRow{
		{ReqID: "r-1", TS: "2026-10-01T00:00:00Z", Client: "127.0.0.1:9", Target: "127.0.0.1:80",
			Stage: "connect", Result: "ok", BytesUp: 5, BytesDown: 7},
		{ReqID: "r-1", TS: "2026-10-01T00:00:01Z", Client: "127.0.0.1:9", Stage: "relay",
			Result: "fail", Reason: "byte_budget_exceeded"},
		{ReqID: "r-2", TS: "2026-10-01T00:00:02Z", Stage: "handshake",
			Result: "fail", Reason: "ip_not_whitelisted"},
	}
	for _, r := range rows {
		if err := st.InsertAudit(ctx, r); err != nil {
			t.Fatalf("insert: %v", err)
		}
	}

	all, err := st.AuditTrail(ctx, "")
	if err != nil {
		t.Fatalf("query all: %v", err)
	}
	if len(all) != 3 {
		t.Fatalf("all rows = %d want 3", len(all))
	}

	one, err := st.AuditTrail(ctx, "r-1")
	if err != nil {
		t.Fatalf("query one: %v", err)
	}
	if len(one) != 2 {
		t.Fatalf("r-1 rows = %d want 2", len(one))
	}
	if one[0].BytesUp != 5 || one[0].BytesDown != 7 {
		t.Fatalf("byte counters not persisted: %+v", one[0])
	}
	if one[1].Reason != "byte_budget_exceeded" {
		t.Fatalf("reason not persisted: %+v", one[1])
	}
}

func TestOpen_CreatesSchemaIdempotently(t *testing.T) {
	path := filepath.Join(t.TempDir(), "x.db")
	ctx := context.Background()
	st1, err := store.Open(ctx, path)
	if err != nil {
		t.Fatalf("first open: %v", err)
	}
	_ = st1.ReplaceRules(ctx, []config.Rule{{Kind: "cidr", Value: "127.0.0.0/8"}})
	if err := st1.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}
	// Reopening an existing database must migrate without error and keep data.
	st2, err := store.Open(ctx, path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer st2.Close()
	rules, err := st2.ListRules(ctx)
	if err != nil {
		t.Fatalf("list after reopen: %v", err)
	}
	if len(rules) != 1 {
		t.Fatalf("data lost across reopen: %+v", rules)
	}
}
