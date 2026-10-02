package harness

import (
	"context"
	"testing"
	"time"

	"coaplab/internal/config"
	"coaplab/internal/diag"
	"coaplab/internal/fixtures"
	"coaplab/internal/service"
)

// StartTestApp loads the test config and the synthetic fixture document,
// seeds the service and starts serving. The returned cleanup closes it.
// The diagnostic recorder is returned so tests can assert on verdicts.
func StartTestApp(t *testing.T, cfgPath, fixturesPath string, opts ...service.Option) (*service.App, *diag.Recorder, func()) {
	t.Helper()
	cfg, err := config.Load(cfgPath)
	if err != nil {
		t.Fatalf("load test config: %v", err)
	}
	if err := cfg.Validate(); err != nil {
		t.Fatalf("invalid test config: %v", err)
	}
	seeds, err := loadSeeds(fixturesPath)
	if err != nil {
		t.Fatalf("load fixtures: %v", err)
	}
	rec := diag.NewRecorder(nil).Quiet()
	app, err := service.New(cfg, seeds, rec, opts...)
	if err != nil {
		t.Fatalf("start app: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	go func() { _ = app.Serve(ctx) }()

	// Give the read loop a moment; Serve errors surface immediately anyway.
	time.Sleep(20 * time.Millisecond)

	cleanup := func() {
		cancel()
		_ = app.Close()
	}
	return app, rec, cleanup
}

func loadSeeds(path string) ([]config.SeedResource, error) {
	doc, err := fixtures.Load(path)
	if err != nil {
		return nil, err
	}
	out := make([]config.SeedResource, 0, len(doc.Resources))
	for _, r := range doc.Resources {
		body, err := r.Body()
		if err != nil {
			return nil, err
		}
		out = append(out, config.SeedResource{Path: r.Path, ContentFormat: r.ContentFormat, Body: body})
	}
	return out, nil
}
