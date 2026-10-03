.object chain_a.o
.section .text.a
.globl a
a:
call b
pushi 1
add
ret
