package ir

import (
	"fmt"

	"pmd/frontend"
)

// Compilation follows the classic pattern-matrix scheme:
//
//   - Rows are the source branches in their original order; columns are
//     value paths still to be tested.
//   - If the first row is all wildcards/variables, it is the
//     highest-priority candidate for every value reaching this point, so we
//     emit a Leaf (or a Guard whose Else continues with the remaining rows).
//   - Otherwise we switch on the leftmost column that carries a constructor
//     or literal test. Rows with a variable/wildcard in that column are
//     copied into every arm and into the default, which preserves branch
//     order priority while letting all arms share the single test.
//
// Guards are only emitted once their row's structural patterns have all
// been discharged, so a guard is never evaluated before its pattern
// matched, and along any single route each guard appears at most once.
type row struct {
	pats   []frontend.Pattern
	binds  []Binding
	branch int
}

type compiler struct {
	prog      *frontend.Program
	nextID    int
	reachable map[int]bool
}

// Compile lowers a validated program into a decision tree. Branches that
// can never match are reported as warnings.
func Compile(prog *frontend.Program) *Tree {
	c := &compiler{prog: prog, reachable: map[int]bool{}}
	cols := []Path{{}}
	rows := make([]row, len(prog.Branches))
	for i, b := range prog.Branches {
		rows[i] = row{pats: []frontend.Pattern{b.Pat}, branch: i}
	}
	root := c.compile(cols, rows)
	t := &Tree{Root: root, Ctors: prog.Ctors}
	for i := range prog.Branches {
		if !c.reachable[i] {
			t.Warnings = append(t.Warnings, fmt.Sprintf(
				"branch %d (%q) is unreachable: shadowed by earlier branches",
				i, prog.Branches[i].Label))
		}
	}
	t.Stats = computeStats(root)
	return t
}

func (c *compiler) id() int {
	c.nextID++
	return c.nextID
}

func (c *compiler) compile(cols []Path, rows []row) *Node {
	if len(rows) == 0 {
		return &Node{Kind: Fail, ID: c.id()}
	}
	if allWild(rows[0].pats) {
		r := rows[0]
		binds := leafBindings(cols, r)
		leaf := &Node{
			Kind:     Leaf,
			ID:       c.id(),
			Branch:   r.branch,
			Label:    c.prog.Branches[r.branch].Label,
			Bindings: binds,
		}
		c.reachable[r.branch] = true
		if g := c.prog.Branches[r.branch].Guard; g != nil {
			return &Node{
				Kind:     GuardN,
				ID:       c.id(),
				Expr:     g,
				ExprText: frontend.ExprString(g),
				Bindings: binds,
				Then:     leaf,
				Else:     c.compile(cols, rows[1:]),
			}
		}
		return leaf
	}
	col := selectColumn(rows)
	if col < 0 {
		// Unreachable in a validated program: a non-wildcard first row
		// always yields a testable column. Fail closed rather than loop.
		return &Node{Kind: Fail, ID: c.id()}
	}
	sw := &Node{Kind: Switch, ID: c.id(), Path: append(Path{}, cols[col]...)}
	for _, test := range collectTests(rows, col) {
		subCols, subRows := c.specialize(cols, rows, col, test)
		sw.Cases = append(sw.Cases, Case{Test: test, Node: c.compile(subCols, subRows)})
	}
	defCols, defRows := defaultMatrix(cols, rows, col)
	sw.Default = c.compile(defCols, defRows)
	return sw
}

func allWild(pats []frontend.Pattern) bool {
	for _, p := range pats {
		switch p.(type) {
		case frontend.PWildcard, frontend.PVar:
		default:
			return false
		}
	}
	return true
}

func leafBindings(cols []Path, r row) []Binding {
	binds := append([]Binding{}, r.binds...)
	for i, p := range r.pats {
		if v, ok := p.(frontend.PVar); ok {
			binds = append(binds, Binding{Name: v.Name, Path: append(Path{}, cols[i]...)})
		}
	}
	return binds
}

// selectColumn returns the leftmost column in which some row carries a
// constructor or literal test, or -1 if there is none.
func selectColumn(rows []row) int {
	width := len(rows[0].pats)
	for col := 0; col < width; col++ {
		for _, r := range rows {
			switch r.pats[col].(type) {
			case frontend.PCtor, frontend.PLit:
				return col
			}
		}
	}
	return -1
}

// collectTests lists the distinct tests of a column in first-appearance
// (i.e. branch priority) order.
func collectTests(rows []row, col int) []TestKey {
	var tests []TestKey
	seen := map[string]bool{}
	for _, r := range rows {
		var key TestKey
		var id string
		switch p := r.pats[col].(type) {
		case frontend.PCtor:
			key = TestKey{Ctor: p.Name}
			id = "c:" + p.Name
		case frontend.PLit:
			v := p.Val
			key = TestKey{Lit: &v}
			id = "l:" + v.String()
		default:
			continue
		}
		if !seen[id] {
			seen[id] = true
			tests = append(tests, key)
		}
	}
	return tests
}

func dropCol(pats []frontend.Pattern, col int) []frontend.Pattern {
	out := make([]frontend.Pattern, 0, len(pats)-1)
	out = append(out, pats[:col]...)
	out = append(out, pats[col+1:]...)
	return out
}

func wilds(n int) []frontend.Pattern {
	out := make([]frontend.Pattern, n)
	for i := range out {
		out[i] = frontend.PWildcard{}
	}
	return out
}

// specialize builds the sub-matrix for one switch arm. Constructor tests
// replace the column by its argument columns; literal tests drop it.
// Variable/wildcard rows are kept (variables record their binding).
func (c *compiler) specialize(cols []Path, rows []row, col int, test TestKey) ([]Path, []row) {
	path := cols[col]
	if test.Ctor != "" {
		arity := c.prog.Ctors[test.Ctor]
		var newCols []Path
		newCols = append(newCols, cols[:col]...)
		for i := 0; i < arity; i++ {
			np := append(append(Path{}, path...), i)
			newCols = append(newCols, np)
		}
		newCols = append(newCols, cols[col+1:]...)
		var out []row
		for _, r := range rows {
			switch p := r.pats[col].(type) {
			case frontend.PCtor:
				if p.Name != test.Ctor {
					continue
				}
				np := make([]frontend.Pattern, 0, len(r.pats)-1+arity)
				np = append(np, r.pats[:col]...)
				np = append(np, p.Args...)
				np = append(np, r.pats[col+1:]...)
				out = append(out, row{pats: np, binds: r.binds, branch: r.branch})
			case frontend.PVar:
				np := make([]frontend.Pattern, 0, len(r.pats)-1+arity)
				np = append(np, r.pats[:col]...)
				np = append(np, wilds(arity)...)
				np = append(np, r.pats[col+1:]...)
				nb := append(append([]Binding{}, r.binds...),
					Binding{Name: p.Name, Path: append(Path{}, path...)})
				out = append(out, row{pats: np, binds: nb, branch: r.branch})
			case frontend.PWildcard:
				np := make([]frontend.Pattern, 0, len(r.pats)-1+arity)
				np = append(np, r.pats[:col]...)
				np = append(np, wilds(arity)...)
				np = append(np, r.pats[col+1:]...)
				out = append(out, row{pats: np, binds: r.binds, branch: r.branch})
			}
		}
		return newCols, out
	}
	// Literal test: the column is consumed.
	newCols := append(append([]Path{}, cols[:col]...), cols[col+1:]...)
	var out []row
	for _, r := range rows {
		switch p := r.pats[col].(type) {
		case frontend.PLit:
			if p.Val != *test.Lit {
				continue
			}
			out = append(out, row{pats: dropCol(r.pats, col), binds: r.binds, branch: r.branch})
		case frontend.PVar:
			nb := append(append([]Binding{}, r.binds...),
				Binding{Name: p.Name, Path: append(Path{}, path...)})
			out = append(out, row{pats: dropCol(r.pats, col), binds: nb, branch: r.branch})
		case frontend.PWildcard:
			out = append(out, row{pats: dropCol(r.pats, col), binds: r.binds, branch: r.branch})
		}
	}
	return newCols, out
}

// defaultMatrix keeps only the rows that impose no test on the column
// (wildcard/variable); the column itself is dropped because the value
// there matched none of the switch's tests.
func defaultMatrix(cols []Path, rows []row, col int) ([]Path, []row) {
	newCols := append(append([]Path{}, cols[:col]...), cols[col+1:]...)
	var out []row
	for _, r := range rows {
		switch p := r.pats[col].(type) {
		case frontend.PVar:
			nb := append(append([]Binding{}, r.binds...),
				Binding{Name: p.Name, Path: append(Path{}, cols[col]...)})
			out = append(out, row{pats: dropCol(r.pats, col), binds: nb, branch: r.branch})
		case frontend.PWildcard:
			out = append(out, row{pats: dropCol(r.pats, col), binds: r.binds, branch: r.branch})
		}
	}
	return newCols, out
}
