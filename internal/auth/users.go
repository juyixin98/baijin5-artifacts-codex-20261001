package auth

import (
	"encoding/json"
	"fmt"
	"os"
)

// Account is one synthetic local account. Password appears only in fixture
// manifests for seeding; the store never persists the plaintext itself.
type Account struct {
	Username string `json:"username"`
	Password string `json:"password"`
	// Mailboxes this account may SELECT; empty means the fixture's defaults.
	Mailboxes []string `json:"mailboxes,omitempty"`
}

// User is a provisioned account: salted PBKDF2 record only.
type User struct {
	Salt      []byte
	Hash      []byte
	Mailboxes []string
}

// UserStore is the in-process account directory. Writes happen only at
// provisioning (startup), reads serve LOGIN; a RWMutex documents that shape.
type UserStore struct {
	users map[string]User
}

// NewUserStore returns an empty directory.
func NewUserStore() *UserStore { return &UserStore{users: map[string]User{}} }

// Provision inserts one account with a fresh random salt.
func (s *UserStore) Provision(a Account) error {
	salt, err := GenerateSalt()
	if err != nil {
		return err
	}
	s.users[a.Username] = User{
		Salt:      salt,
		Hash:      Hash([]byte(a.Password), salt),
		Mailboxes: append([]string(nil), a.Mailboxes...),
	}
	return nil
}

// Login authenticates credentials, returning the user on success. It returns
// ErrCredential for both unknown user and wrong password, and performs a
// dummy hash on unknown users to reduce user-enumeration timing differences.
func (s *UserStore) Login(username, password []byte) (User, error) {
	u, ok := s.users[string(username)]
	if !ok {
		dummySalt := make([]byte, SaltLen)
		_ = Verify(password, dummySalt, make([]byte, KeyLen))
		return User{}, ErrCredential
	}
	if !Verify(password, u.Salt, u.Hash) {
		return User{}, ErrCredential
	}
	return u, nil
}

// CanSelect reports whether the account may open mailbox name.
func (s *UserStore) CanSelect(u User, name string) bool {
	if len(u.Mailboxes) == 0 {
		return true
	}
	for _, m := range u.Mailboxes {
		if m == name {
			return true
		}
	}
	return false
}

// LoadAccounts provisions accounts from a JSON fixture file.
func (s *UserStore) LoadAccounts(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read accounts fixture %s: %w", path, err)
	}
	var accounts []Account
	if err := json.Unmarshal(data, &accounts); err != nil {
		return fmt.Errorf("parse accounts fixture: %w", err)
	}
	for _, a := range accounts {
		if a.Username == "" || a.Password == "" {
			return fmt.Errorf("accounts fixture entry missing username/password")
		}
		if err := s.Provision(a); err != nil {
			return err
		}
	}
	return nil
}
