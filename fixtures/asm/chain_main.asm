# main -> a -> b -> c across four objects
.object chain_main.o
.section .text.main
.globl main
.export main
main:
call a
halt
