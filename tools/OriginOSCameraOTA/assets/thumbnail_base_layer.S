.text
.global mio_thumbnail_configure
mio_thumbnail_configure:
    // Entry after original PACIASP. Preserve all original arguments and signed LR.
    sub sp, sp, #0xc0
    stp x0, x1, [sp]
    stp x2, x3, [sp, #0x10]
    stp x4, x5, [sp, #0x20]
    stp x6, x7, [sp, #0x30]
    stp x19, x20, [sp, #0x40]
    stp x29, x30, [sp, #0x50]
    cbnz w5, done
    ldr x8, [x3]
    cbnz x8, done
    ldr x8, [x4]
    cbnz x8, done
    ldr x19, [x1]
    cbz x19, done
    mov x0, x19
    adr x1, thumbnail_key
    add x2, sp, #0x88
    bl find_int
    cbz w0, done
    ldr w8, [sp, #0x88]
    cmp w8, #1
    b.ne done
    mov x0, x19
    adr x1, profile_key
    add x2, sp, #0x88
    bl find_int
    cbz w0, done
    ldr w8, [sp, #0x88]
    cmp w8, #256
    b.ne done
    mov x0, x19
    adr x1, compat_key
    add x2, sp, #0x88
    bl find_int
    cbz w0, done
    ldr w8, [sp, #0x88]
    cmp w8, #4
    b.ne done
    add x0, sp, #0x60
    bl string_ctor
    ldr x0, [sp]
    add x1, sp, #0x60
    bl codec_name
    cbnz w0, destroy
    add x0, sp, #0x60
    bl string_cstr
    adr x1, decoder_name
    bl string_compare
    cbnz w0, destroy
    mov x0, x19
    adr x1, mime_key
    add x2, sp, #0x60
    bl find_string
    cbz w0, destroy
    add x0, sp, #0x60
    bl string_cstr
    adr x1, dolby_mime
    bl string_compare
    cbnz w0, destroy
    mov x0, x19
    adr x1, mime_key
    adr x2, hevc_mime
    mov x3, #-1
    bl set_string
    mov x0, x19
    adr x1, profile_key
    mov w2, #2
    bl set_int
    mov x0, x19
    adr x1, transfer_key
    mov w2, #3
    bl set_int
destroy:
    add x0, sp, #0x60
    bl string_dtor
done:
    ldp x0, x1, [sp]
    ldp x2, x3, [sp, #0x10]
    ldp x4, x5, [sp, #0x20]
    ldp x6, x7, [sp, #0x30]
    ldp x19, x20, [sp, #0x40]
    ldp x29, x30, [sp, #0x50]
    add sp, sp, #0xc0
    sub sp, sp, #MIO_STACK // target configure's displaced stack allocation
    b configure_continue
thumbnail_key: .asciz "vivo.vdec.thumbnail.mode.value"
profile_key: .asciz "profile"
compat_key: .asciz "bl_compatibility_id"
mime_key: .asciz "mime"
decoder_name: .asciz "c2.qti.dv.decoder"
dolby_mime: .asciz "video/dolby-vision"
hevc_mime: .asciz "video/hevc"
transfer_key: .asciz "color-transfer-request"
.balign 4
