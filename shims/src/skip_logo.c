/* Phase 1 skip_logo shim — __stdcall replacement for `play_movie_fn`
 * at the single call site that loads "AdreniumLogo.bik".
 *
 * The vanilla call in FUN_0005f620 (the boot state machine) looks like:
 *
 *   0x05f6df: PUSH EBP            ; EBP = 0 (scratch zero)
 *   0x05f6e0: PUSH 0x0019e150     ; &"AdreniumLogo.bik"
 *   0x05f6e5: CALL play_movie_fn  ; __stdcall, returns AL = 0|1
 *   0x05f6ea: NEG AL              ; AL != 0 → state = 2 (POLL movie)
 *             SBB EAX, EAX        ; AL == 0 → state = 3 (skip to prophecy)
 *             ADD EAX, 3
 *             MOV [state], EAX
 *
 * We replace only the 5-byte CALL at 0x05f6e5 with a CALL into this
 * shim.  The two original PUSHes still execute, so at shim entry the
 * stack holds:
 *
 *   [ESP + 0]  = our own return address (pushed by the trampoline CALL)
 *   [ESP + 4]  = &"AdreniumLogo.bik"    (pushed by 0x05f6e0)
 *   [ESP + 8]  = 0                      (pushed by 0x05f6df, EBP)
 *
 * To match the original __stdcall contract we must:
 *
 *   1. Clear AL so the state machine writes state = 3 (skip to
 *      prophecy state) instead of state = 2 (polling a movie that
 *      never started, which is what caused the black-screen hang
 *      in the earlier NOP-only patch).
 *   2. Pop 8 bytes of caller-pushed args via `ret 8`, exactly what
 *      the real `play_movie_fn` would do.
 *
 * Result: the Adrenium logo is never loaded, and control flows
 * smoothly into prophecy.bik on the next tick of the boot state
 * machine.  Prophecy is deliberately left alone.
 */

#include "azurik.h"

__attribute__((naked))
void c_skip_logo(void)
{
    __asm__ volatile (
        "xorb %%al, %%al  \n\t"   /* AL = 0   → state machine chooses case 3 */
        "ret  $8          \n\t"   /* __stdcall: pop the 2 caller-pushed args */
        : /* no outputs */
        : /* no inputs  */
        : "memory"
    );
}
