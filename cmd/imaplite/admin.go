package main

import (
	"context"

	"imaplite/internal/ctl"
	"imaplite/internal/fixture"
	"imaplite/internal/store"
)

// adminAdapter bridges the control plane to the store and fixture loader.
type adminAdapter struct {
	store *store.Store
	load  *fixture.Loaded
}

func (a adminAdapter) RotateUIDValidity(ctx context.Context, mailbox string) (uint32, error) {
	return a.store.RotateUIDValidity(ctx, mailbox)
}

func (a adminAdapter) MailboxInfo(ctx context.Context, mailbox string) (ctl.MailboxInfo, error) {
	info, err := a.store.MailboxInfo(ctx, mailbox)
	if err != nil {
		return ctl.MailboxInfo{}, err
	}
	return ctl.MailboxInfo{
		Name: info.Name, Exists: info.Exists,
		UIDNext: info.UIDNext, UIDValidity: info.UIDValidity,
	}, nil
}

func (a adminAdapter) Reseed(ctx context.Context) error {
	return a.load.Provision(a.store)
}

var _ ctl.Admin = adminAdapter{}
