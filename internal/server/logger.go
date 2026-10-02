package server

import (
	"log/slog"

	"imaplite/internal/imapwire"
	"imaplite/internal/store"
)

// sessionLogger is the per-connection structured audit trail. It records the
// run number (command ordinal on the connection), the state at entry, key
// intermediate mailbox counters and the classified failure reason, so a
// reported problem can be replayed run-by-run.
type sessionLogger struct {
	base *slog.Logger
	conn int64
}

func newLogger(base *slog.Logger, connID int64) sessionLogger {
	if base == nil {
		base = slog.Default()
	}
	return sessionLogger{
		base: base.With("conn", connID, "component", "imap-session"),
		conn: connID,
	}
}

func (l sessionLogger) commandStart(tag, verb, state string) {
	l.base.Info("command", "tag", tag, "verb", verb, "state", state)
}

func (l sessionLogger) commandFail(tag string, status imapwire.Status, code, text, class string) {
	l.base.Warn("command-failed",
		"tag", tag, "status", string(status), "code", code,
		"class", class, "reason", text)
}

func (l sessionLogger) frameError(run int, we *imapwire.Error) {
	l.base.Warn("framing-error",
		"run", run, "class", string(we.Class), "code", we.Code, "reason", we.Msg)
}

func (l sessionLogger) internalError(err error) {
	l.base.Error("internal-error", "err", err.Error())
}

func (l sessionLogger) peerClosed() {
	l.base.Info("peer-closed")
}

func (l sessionLogger) authOK(user []byte) {
	l.base.Info("auth-ok", "user", string(user))
}

func (l sessionLogger) authFail(user []byte) {
	l.base.Warn("auth-failed", "user", string(user), "class", string(imapwire.ClassAuth))
}

func (l sessionLogger) selected(name string, info store.MailboxInfo) {
	l.base.Info("selected",
		"mailbox", name,
		"uidvalidity", info.UIDValidity,
		"uidnext", info.UIDNext,
		"exists", info.Exists,
		"first-unseen", info.FirstUnseen)
}

func (l sessionLogger) fetch(tag string, byUID bool, n int, items []string) {
	l.base.Info("fetch", "tag", tag, "uid", byUID, "matched", n, "items", items)
}

func (l sessionLogger) store(tag string, byUID, silent bool, n int) {
	l.base.Info("store", "tag", tag, "uid", byUID, "silent", silent, "changed", n)
}

func (l sessionLogger) expunge(seq int, uid uint32) {
	l.base.Info("expunge", "seq", seq, "uid", uid,
		"note", "seq is position at removal; identities renumber after this point")
}

func (l sessionLogger) externalExpunge(seq int, uid uint32) {
	l.base.Info("observed-expunge", "seq", seq, "uid", uid)
}

func (l sessionLogger) externalExists(n int) {
	l.base.Info("observed-exists", "exists", n)
}
