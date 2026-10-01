package ir

import (
	"genstatemachine/internal/frontend"
	"genstatemachine/internal/gerr"
)

type compiler struct {
	g      *GenIR
	blocks []*Block
	// cur is the block currently receiving ops. It is always a real,
	// appended block; successor blocks are created *before* they are needed
	// and linked via terminators, so block creation order follows a simple
	// depth-first layout with no dangling/duplicated continuations.
	cur *Block
	reg int
}

// Compile lowers a validated frontend program into explicit CFG form.
func Compile(prog *frontend.Program) (*Program, error) {
	out := &Program{Gens: map[string]*GenIR{}}
	for _, g := range prog.Gens {
		cg, err := compileGen(g)
		if err != nil {
			return nil, err
		}
		if _, dup := out.Gens[cg.Name]; dup {
			return nil, gerr.New(gerr.EDupGen, "duplicate generator %q", cg.Name)
		}
		out.Gens[cg.Name] = cg
		out.Order = append(out.Order, cg.Name)
	}
	return out, nil
}

func compileGen(g *frontend.GenDecl) (*GenIR, error) {
	c := &compiler{g: &GenIR{Name: g.Name, SrcGen: g}}

	entry := c.newBlock("entry")
	c.g.Entry = c.blockIndex(entry)
	end, err := c.stmts(g.Body, entry)
	if err != nil {
		return nil, err
	}
	// Implicit halt if the last filled block still has no terminator.

	if c.isLive(end) {
		end.Term = Term{Kind: TermHalt}
	}
	c.g.Blocks = c.blocks
	return c.g, nil
}

func (c *compiler) newBlock(name string) *Block {
	b := &Block{Name: name, Term: Term{Kind: -1, Target: -1, Other: -1}}
	c.blocks = append(c.blocks, b)
	return b
}

// selectBlock makes b the target of subsequent emit() calls.
func (c *compiler) selectBlock(b *Block) { c.cur = b }

func (c *compiler) emit(o Op) { c.cur.Ops = append(c.cur.Ops, o) }

func (c *compiler) alloc() int {
	c.reg++
	return c.reg - 1
}

func (c *compiler) isLive(b *Block) bool { return b != nil && b.Term.Kind == -1 }
