// Package fixture loads the local synthetic mail data: a JSON manifest plus
// RFC 5322 message files, and deterministically generates one binary message
// (NUL / 0xFF / embedded CRLF) used to prove byte-exact literals. Nothing here
// comes from a real account or a live mailbox.
package fixture

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"time"

	"imaplite/internal/auth"
	"imaplite/internal/store"
)

// Manifest is the on-disk fixture description.
type Manifest struct {
	Accounts  []auth.Account `json:"accounts"`
	Mailboxes []MailboxSeed  `json:"mailboxes"`
}

// MailboxSeed describes one mailbox and its ordered messages.
type MailboxSeed struct {
	Name        string        `json:"name"`
	UIDValidity uint32        `json:"uidvalidity"`
	Messages    []MessageSeed `json:"messages"`
}

// MessageSeed references one message file and its metadata.
type MessageSeed struct {
	// File names the RFC 5322 fixture; empty/ignored when Binary is set.
	File         string   `json:"file,omitempty"`
	Flags        []string `json:"flags"`
	InternalDate string   `json:"internaldate"` // RFC 3339
	// Binary selects a code-generated binary body so literal byte fidelity
	// can be asserted.
	Binary bool `json:"binary,omitempty"`
}

// Loaded is a fully resolved fixture set.
type Loaded struct {
	Accounts  []auth.Account
	Mailboxes []ProvisionedMailbox
}

// ProvisionedMailbox is ready to hand to store.SeedMailbox.
type ProvisionedMailbox struct {
	Name        string
	UIDValidity uint32
	Messages    []store.SeedMessage
}

// DefaultDataDir returns the bundled data directory unless IMAPLITE_DATA
// overrides it. Resolution is relative to this source file so tests run from
// any working directory.
func DefaultDataDir() string {
	if dir := os.Getenv("IMAPLITE_DATA"); dir != "" {
		return dir
	}
	_, thisFile, _, _ := runtime.Caller(0)
	root := filepath.Dir(filepath.Dir(filepath.Dir(thisFile)))
	return filepath.Join(root, "data")
}

// Load reads the manifest and message files from dir.
func Load(dir string) (*Loaded, error) {
	manifestPath := filepath.Join(dir, "manifest.json")
	raw, err := os.ReadFile(manifestPath)
	if err != nil {
		return nil, fmt.Errorf("read manifest %s: %w", manifestPath, err)
	}
	var man Manifest
	if err := json.Unmarshal(raw, &man); err != nil {
		return nil, fmt.Errorf("parse manifest: %w", err)
	}
	out := &Loaded{Accounts: man.Accounts}
	for _, mb := range man.Mailboxes {
		pm := ProvisionedMailbox{Name: mb.Name, UIDValidity: mb.UIDValidity}
		for i, ms := range mb.Messages {
			var body []byte
			if ms.Binary {
				body = BinaryMessage(i + 1)
			} else {
				p := filepath.Join(dir, "mail", ms.File)
				body, err = os.ReadFile(p)
				if err != nil {
					return nil, fmt.Errorf("read message %s: %w", p, err)
				}
			}
			when, err := time.Parse(time.RFC3339, ms.InternalDate)
			if err != nil {
				return nil, fmt.Errorf("mailbox %s message %d: bad internaldate %q: %w",
					mb.Name, i+1, ms.InternalDate, err)
			}
			pm.Messages = append(pm.Messages, store.SeedMessage{
				Raw: body, Flags: ms.Flags, InternalDate: when.UTC(),
			})
		}
		out.Mailboxes = append(out.Mailboxes, pm)
	}
	return out, nil
}

// Provision seeds every fixture mailbox into st.
func (l *Loaded) Provision(st *store.Store) error {
	ctx := context.Background()
	for _, mb := range l.Mailboxes {
		if err := st.SeedMailbox(ctx, mb.Name, mb.UIDValidity, mb.Messages); err != nil {
			return fmt.Errorf("seed mailbox %s: %w", mb.Name, err)
		}
	}
	return nil
}

// ProvisionAccounts loads accounts into the user directory.
func (l *Loaded) ProvisionAccounts(users *auth.UserStore) error {
	for _, a := range l.Accounts {
		if err := users.Provision(a); err != nil {
			return err
		}
	}
	return nil
}
