.text
.global mio_rpu_write
.type mio_rpu_write,%function
mio_rpu_write:
    // Original writeOrPostError(this, fd, ptr, size); preserve x0..x3 and LR.
    cmp x3, #12
    b.lo done
    mov x4, #0x2000000
    cmp x3, x4
    b.hi done
    mov x4, x2
    mov x5, x3
    mov w10, wzr
next:
    cmp x5, #6
    b.lo done
    ldr w6, [x4]
    rev w6, w6
    ldrb w7, [x4, #4]
    ldrb w8, [x4, #5]
    cmp w6, #1
    b.ne regular
    cmp w7, #124
    b.ne done
    cmp w8, #1
    b.ne done
    cbz w10, done
    cmp x5, #8
    b.lo done
    cmp x5, #16, lsl #12
    b.hi done
    sub w6, w5, #4
    rev w6, w6
    str w6, [x4]
    b done
regular:
    cmp w6, #2
    b.lo done
    sub x9, x5, #4
    cmp x6, x9
    b.hi done
    tbnz w7, #7, done
    tst w8, #7
    b.eq done
    ubfx w9, w7, #1, #6
    cmp w9, #31
    b.hi advance
    mov w10, #1
advance:
    add x6, x6, #4
    add x4, x4, x6
    sub x5, x5, x6
    b next
done:
    b origin_write
.size mio_rpu_write, .-mio_rpu_write
