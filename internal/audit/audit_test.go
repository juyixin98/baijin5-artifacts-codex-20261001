package audit_test

import (
	"testing"

	"localstun/internal/audit"
)

func TestNoopSink(t *testing.T) {
	if err := audit.Noop.Write(audit.Record{Event: "x"}); err != nil {
		t.Fatalf("noop write: %v", err)
	}
	if err := audit.Noop.Close(); err != nil {
		t.Fatalf("noop close: %v", err)
	}
}

func TestRecordFields(t *testing.T) {
	r := audit.Record{RunID: "r", Seq: 7, Component: "server", Event: "binding_success"}
	if r.RunID != "r" || r.Seq != 7 || r.Component != "server" {
		t.Fatalf("record fields not retained: %+v", r)
	}
}
