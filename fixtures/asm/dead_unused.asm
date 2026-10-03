# Unreferenced section; its undefined reference must not be diagnosed
# and the section must be reclaimed without a dangling relocation.
.object dead_unused.o
.section .text.unused
.globl deadfn
deadfn:
call ghost_absent
halt
