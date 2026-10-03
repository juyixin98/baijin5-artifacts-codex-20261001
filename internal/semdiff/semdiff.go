// Package semdiff compares two linked RL builds, classifies every change and
// computes the minimal invalidation set.
//
// It distinguishes:
//
//   - private_body_only : a non-exported function body moved. Name-linked, so
//     no caller is invalidated; only that unit is rebuilt/relinked.
//   - public_body       : an exported function body moved without changing its
//     interface. Name-linked; callers are unaffected, owner is relinked.
//   - public_signature  : exported function signature or type surface changed.
//   - inline_const      : a folded const value changed; use sites embedded it.
//   - generic_body      : a monomorphized generic body changed; callers embed
//     that exact instantiation.
//   - added / removed   : structural interface changes.
package semdiff

import (
	"fmt"
	"sort"
	"strings"

	"rlmod/internal/fingerprint"
	"rlmod/internal/ir"
)

type ChangeClass string

const (
	ClassAdded           ChangeClass = "added"
	ClassRemoved         ChangeClass = "removed"
	ClassPrivateBodyOnly ChangeClass = "private_body_only"
	ClassPublicBody      ChangeClass = "public_body"
	ClassPublicSignature ChangeClass = "public_signature"
	ClassInlineConst     ChangeClass = "inline_const"
	ClassGenericBody     ChangeClass = "generic_body"
	ClassUnchanged       ChangeClass = "unchanged"
)

type Change struct {
	Module string
	Name   string
	Kind   string
	Class  ChangeClass
	From   string
	To     string
	Reason string
}

type edge struct{ from, to string }

type Report struct {
	OldSchema       string
	NewSchema       string
	OldSemVer       string
	NewSemVer       string
	Changes         []Change
	Invalidated     []string
	ReuseAllowed    []string
	VersionRejected bool
	VersionReason   string
	ChangedModules  []string
}

func nodeID(module, name string) string { return module + "." + name }

func Diff(oldP, newP *ir.Program, oldFP, newFP *fingerprint.Report) *Report {
	r := &Report{OldSchema: oldFP.Schema, NewSchema: newFP.Schema, OldSemVer: oldFP.SemVer, NewSemVer: newFP.SemVer}

	if oldFP.Schema != newFP.Schema {
		r.VersionRejected = true
		r.VersionReason = fmt.Sprintf("fingerprint schema changed %s -> %s", oldFP.Schema, newFP.Schema)
	} else if major(oldFP.SemVer) != major(newFP.SemVer) {
		r.VersionRejected = true
		r.VersionReason = fmt.Sprintf("compile semantic version major changed %s -> %s", oldFP.SemVer, newFP.SemVer)
	}

	oldSym := flatten(oldFP)
	newSym := flatten(newFP)
	keySet := map[string]bool{}
	for k := range oldSym {
		keySet[k] = true
	}
	for k := range newSym {
		keySet[k] = true
	}
	changedMod := map[string]bool{}
	var propagatingSeeds []string
	for _, k := range sortedSet(keySet) {
		o, ook := oldSym[k]
		n, nok := newSym[k]
		switch {
		case !ook && nok:
			r.Changes = append(r.Changes, Change{n.Module, n.Name, n.Kind, ClassAdded, "", n.Short, "new public surface symbol"})
			propagatingSeeds = append(propagatingSeeds, k)
			changedMod[n.Module] = true
		case ook && !nok:
			r.Changes = append(r.Changes, Change{o.Module, o.Name, o.Kind, ClassRemoved, o.Short, "", "symbol removed"})
			propagatingSeeds = append(propagatingSeeds, k)
			changedMod[o.Module] = true
		case ook && nok:
			if o.Hash == n.Hash && o.InterfaceHash == n.InterfaceHash && o.BodyHash == n.BodyHash {
				continue
			}
			ch, from, to := classify(o, n)
			r.Changes = append(r.Changes, Change{n.Module, n.Name, n.Kind, ch, from, to, reasonFor(ch)})
			changedMod[n.Module] = true
			if propagates(ch) {
				propagatingSeeds = append(propagatingSeeds, k)
			}
		}
	}

	edges := buildEdges(newP)
	consumersOf := map[string][]string{}
	for _, e := range edges {
		consumersOf[e.from] = append(consumersOf[e.from], e.to)
	}
	invalid := map[string]bool{}
	if r.VersionRejected {
		for k := range newSym {
			invalid[k] = true
		}
	} else {
		// Seed A: every changed symbol recompiles itself.
		changed := map[string]bool{}
		for _, ch := range r.Changes {
			id := nodeID(ch.Module, ch.Name)
			invalid[id] = true
			changed[id] = true
		}
		// Seed B: interface-level changes are propagation roots. BFS follows
		// consumers transitively. Visited-nodes are tracked separately from
		// invalidation so a changed symbol that is also a consumer (e.g. a
		// function which inlined the changed const) is still traversed.
		visited := map[string]bool{}
		work := append([]string(nil), propagatingSeeds...)
		for len(work) > 0 {
			cur := work[0]
			work = work[1:]
			if visited[cur] {
				continue
			}
			visited[cur] = true
			invalid[cur] = true
			for _, consumer := range consumersOf[cur] {
				if !visited[consumer] {
					work = append(work, consumer)
				}
			}
		}
		_ = changed
	}

	for k := range invalid {
		r.Invalidated = append(r.Invalidated, k)
	}
	sort.Strings(r.Invalidated)
	for k := range newSym {
		if !invalid[k] {
			r.ReuseAllowed = append(r.ReuseAllowed, k)
		}
	}
	sort.Strings(r.ReuseAllowed)
	for m := range changedMod {
		r.ChangedModules = append(r.ChangedModules, m)
	}
	sort.Strings(r.ChangedModules)
	return r
}

func propagates(c ChangeClass) bool {
	switch c {
	case ClassPublicSignature, ClassInlineConst, ClassGenericBody, ClassAdded, ClassRemoved:
		return true
	}
	return false
}

// classify distinguishes interface changes from body-only changes using the
// two-part fingerprint.
func classify(o, n *fingerprint.TypeFP) (ChangeClass, string, string) {
	switch n.Kind {
	case "const":
		if o.InterfaceHash != n.InterfaceHash {
			return ClassInlineConst, o.Short, n.Short
		}
		return ClassUnchanged, o.Short, n.Short
	case "type":
		if o.InterfaceHash != n.InterfaceHash {
			return ClassPublicSignature, o.Short, n.Short
		}
		return ClassUnchanged, o.Short, n.Short
	case "generic":
		if o.InterfaceHash != n.InterfaceHash {
			return ClassGenericBody, o.Short, n.Short
		}
		if o.BodyHash != n.BodyHash {
			return ClassGenericBody, o.Short, n.Short
		}
		return ClassUnchanged, o.Short, n.Short
	default:
		if o.InterfaceHash != n.InterfaceHash {
			if n.Public {
				return ClassPublicSignature, o.Short, n.Short
			}
			return ClassPrivateBodyOnly, o.Short, n.Short
		}
		if o.BodyHash != n.BodyHash {
			if n.Public {
				return ClassPublicBody, o.Short, n.Short
			}
			return ClassPrivateBodyOnly, o.Short, n.Short
		}
		return ClassUnchanged, o.Short, n.Short
	}
}

func reasonFor(c ChangeClass) string {
	switch c {
	case ClassPrivateBodyOnly:
		return "private body changed; name-linked callers unaffected"
	case ClassPublicBody:
		return "exported body changed; relink owner, caller interface unchanged"
	case ClassPublicSignature:
		return "exported signature/type changed; callers must recompile"
	case ClassInlineConst:
		return "folded inline constant changed; use sites embedded the old value"
	case ClassGenericBody:
		return "generic instantiation changed; monomorphized callers embed it"
	default:
		return string(c)
	}
}

func flatten(rep *fingerprint.Report) map[string]*fingerprint.TypeFP {
	out := map[string]*fingerprint.TypeFP{}
	for m, mfp := range rep.Modules {
		for name, sf := range mfp.Symbols {
			out[nodeID(m, name)] = sf
		}
	}
	return out
}

func sortedSet(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func major(semver string) string {
	if i := strings.Index(semver, "."); i >= 0 {
		return semver[:i]
	}
	return semver
}
