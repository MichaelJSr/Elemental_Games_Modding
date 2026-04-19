/* Wrapper around vanilla Azurik's gravity-integration routine
 * (``FUN_00085700``).
 *
 * The vanilla function uses an MSVC-style fastcall + RVO ABI that
 * no clang calling-convention attribute can express natively:
 *
 *     ECX     = config struct pointer       (fastcall arg 1)
 *     EDX     = velocity / position pointer (fastcall arg 2)
 *     EAX     = RESULT struct pointer       (RVO, implicit output)
 *     ESI     = caller-provided "context"   (entity / player state)
 *     [ESP+4] = float gravity_dt_product    (caller-pushed; callee pops via RET 4)
 *
 * ``ESI`` is callee-saved by cdecl/stdcall/fastcall convention on
 * i386-pe-win32, so the vanilla function relies on its CALLER having
 * already set ESI to the current player/entity struct.  In every
 * observed vanilla call site this is the case — ESI gets established
 * at the top of the player-update routine and stays live throughout.
 *
 * We can't express "ECX + EDX + EAX + ESI + 1 stack float" through
 * any clang calling convention, so the wrapper drops down to inline
 * asm.  The trick: a SINGLE asm volatile block that sets up every
 * register (including EAX for RVO and ESI for context) AND emits
 * the CALL — clang can't reorder anything between EAX setup and
 * the CALL because they live in one basic block of inline asm.
 *
 * The external symbol ``@gravity_integrate_raw@8`` is a lie to
 * clang: the vanilla function isn't actually a two-arg fastcall,
 * but declaring it that way makes clang emit ``call @...@8`` which
 * layout_coff resolves to the real vanilla VA 0x00085700 via
 * ``vanilla_symbols.py``.  The extra ESI / EAX setup happens in
 * asm, invisible to clang.
 *
 * Shim authors should call ``azurik_gravity_integrate`` from
 * ``azurik_gravity.h`` — never invoke ``gravity_integrate_raw``
 * directly.  The wrapper provides a normal C calling convention
 * that the layout pipeline treats like any other shim function.
 *
 * See ``docs/LEARNINGS.md`` for the full ABI investigation that
 * led to this wrapper, and ``tests/test_gravity_wrapper.py`` for
 * drift guards on the emitted byte shape.
 */

/* Clang emits a reference to ``__fltused`` whenever a freestanding
 * compilation touches a float.  It's a linker marker — MSVC's
 * convention for "this TU uses the FP unit, pull in the FP library".
 * We stub it out as a local BSS slot so it doesn't leak into the
 * undefined-externs list ``layout_coff`` examines.  The ``__asm__``
 * label overrides clang's default ``_fltused`` decoration to match
 * the double-underscore name clang actually references.
 */
int __fltused __asm__("__fltused") = 0;

/* Vanilla target, lied to as a 2-arg fastcall.  Mangled name is
 * ``@gravity_integrate_raw@8`` — matches the entry registered in
 * ``azurik_mod/patching/vanilla_symbols.py`` that resolves to VA
 * 0x00085700. */
extern __attribute__((fastcall))
void gravity_integrate_raw(void *config, void *vel_accumulator);


/* Clean C-level wrapper.  Stdcall(20) — five 4-byte args: the
 * caller pushes all five, the wrapper pops its own 20 bytes via
 * ``RET 14`` at its tail.  Inside, the inline-asm block sets up
 * ECX / EDX / EAX / ESI / stack exactly as the vanilla function
 * expects.
 *
 * Arg layout on entry (after the CALL pushes return addr):
 *     [ESP+ 0] = return address
 *     [ESP+ 4] = player_esi    (becomes ESI before the inner call)
 *     [ESP+ 8] = config        (becomes ECX)
 *     [ESP+12] = vel_accum     (becomes EDX)
 *     [ESP+16] = gravity_dt    (becomes stack arg to vanilla)
 *     [ESP+20] = result_out    (becomes EAX — RVO pointer)
 */
__attribute__((stdcall))
void azurik_gravity_integrate(
    void *player_esi,
    void *config,
    void *vel_accumulator,
    float gravity_dt_product,
    void *result_out)
{
    __asm__ volatile (
        /* Order matters: set EAX LAST (immediately before CALL)
         * so no intervening clang-generated instruction clobbers
         * it.  ESI goes first because we'll clobber clang's
         * register allocator's use of ESI as soon as we overwrite
         * it; clang's clobber list ("esi") tells the allocator
         * not to rely on ESI across the block. */
        "pushl %[grav]       \n\t"  /* push float gravity as 4-byte DWORD */
        "movl  %[esi], %%esi \n\t"
        "movl  %[cfg], %%ecx \n\t"
        "movl  %[vel], %%edx \n\t"
        "movl  %[res], %%eax \n\t"  /* RVO ptr goes last */
        "calll @gravity_integrate_raw@8 \n\t"
        /* Vanilla does RET 4 — pops the gravity we pushed.
         * ESI is nominally callee-saved, but the vanilla function
         * modifies it internally (observable in FUN_00085700's
         * body).  Clang's "esi" clobber handles save/restore. */
        :
        : [grav]"g"(gravity_dt_product),
          [cfg]"g"(config),
          [vel]"g"(vel_accumulator),
          [res]"g"(result_out),
          [esi]"r"(player_esi)
        : "eax", "ecx", "edx", "esi", "memory"
    );
}
