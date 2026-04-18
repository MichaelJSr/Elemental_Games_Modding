/* Phase 1 proof-of-concept shim — replaces the vanilla boot-time
 * PUSH "AdreniumLogo.bik"; CALL play_movie pair at VA 0x05F6E0.
 *
 * The intent matches the existing byte-level `qol_skip_logo` patch
 * (NOP x10): the movie never plays.  The difference here is HOW we
 * achieve it — instead of blanking the call site with NOPs, we divert
 * control flow into this empty C function and return straight back
 * to the caller.  The observable behaviour is identical, but the
 * machinery is now C-level, which is the foundation for every future
 * shim that will do real work.
 *
 * Compiles to exactly:
 *   55 89 e5 5d c3   (PUSH EBP; MOV EBP,ESP; POP EBP; RET)
 *
 * The symbol comes out as `_c_skip_logo` in the PE-COFF object (the
 * Windows/MSVC leading-underscore convention); the TrampolinePatch
 * declaration refers to it by that mangled name.
 */

#include "azurik.h"

void c_skip_logo(void)
{
    /* Deliberately empty.  The vanilla call site passes an argument
     * on the stack (PUSH 0x19E150 = &"AdreniumLogo.bik") before
     * CALLing us; we ignore it and return.  Since the caller cleans
     * up its own stack argument via CALL/RET convention mismatch —
     * no, wait: vanilla uses __cdecl, so the CALLER cleans up the
     * pushed arg after the CALL returns.  We leave ESP untouched. */
    return;
}
