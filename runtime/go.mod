module pmd/runtime

go 1.22

require (
	pmd/frontend v0.0.0
	pmd/ir v0.0.0
)

replace pmd/frontend => ../frontend

replace pmd/ir => ../ir
