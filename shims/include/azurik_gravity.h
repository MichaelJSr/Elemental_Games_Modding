/* Gravity / velocity integration wrapper for shims.
 *
 * Exposes a clean C-level API around vanilla Azurik's
 * ``FUN_00085700`` — the tick-by-tick gravity integrator the game
 * uses to update entity velocities under gravity.
 *
 * Why the wrapper exists:
 *   The vanilla function has an MSVC-style fastcall + RVO ABI that
 *   no clang calling-convention attribute expresses natively
 *   (ECX + EDX + EAX + ESI + stack float, callee pops).  A plain
 *   extern declaration would generate wrong code at every call
 *   site.  The wrapper in ``shims/shared/gravity_integrate.c``
 *   uses one atomic inline-asm block to set up every register
 *   correctly before emitting the CALL.
 *
 * Shim authors should always call ``azurik_gravity_integrate()``
 * from this header — the raw vanilla symbol
 * (``gravity_integrate_raw`` in ``vanilla_symbols.py``) is not
 * safe to invoke directly from C.
 *
 * Usage:
 *
 *     #include "azurik_gravity.h"
 *
 *     __attribute__((stdcall))
 *     void c_my_jump_mod(void) {
 *         // Hook site has ESI = player entity, EDI = config,
 *         // EBP = velocity accumulator (typical vanilla call shape).
 *         char result_buffer[0x38];   // sized to vanilla's RVO output
 *         azurik_gravity_integrate(
 *             (void *)0,           // or the player-entity ptr
 *             my_config,           // attack-transition entry etc.
 *             my_velocity_ptr,     // current velocity (read+write)
 *             my_gravity * dt,     // usually 9.8f * dt or dilated
 *             result_buffer);
 *         // result_buffer now holds the integrated velocity + flags.
 *     }
 *
 * The layout pipeline auto-places the wrapper shim (via the E
 * shared-library mechanism) the first time any feature references
 * ``azurik_gravity_integrate``.  One placement per XBE build; all
 * consumers share the single wrapper.
 */
#ifndef AZURIK_GRAVITY_H
#define AZURIK_GRAVITY_H

#include "azurik.h"

#ifdef __cplusplus
extern "C" {
#endif


/* ==========================================================================
 * Internal — raw vanilla function. NOT FOR DIRECT USE.
 * ==========================================================================
 *
 * This declaration exists only so the vanilla-registry drift guard
 * (see tests/test_vanilla_thunks.py) can match the registered
 * symbol ``gravity_integrate_raw`` against a C extern.
 *
 * The declared signature (fastcall + 2 void-ptr args) is a
 * DELIBERATE LIE: the real ABI also uses EAX for RVO output and
 * ESI for a caller-provided entity context.  No clang calling-
 * convention attribute expresses that combination, so the
 * wrapper at ``shims/shared/gravity_integrate.c`` uses inline
 * asm to set up the extra registers before emitting the CALL.
 *
 * DO NOT call this from shim C code — it will generate a call
 * with missing register state and crash the game.  Use
 * ``azurik_gravity_integrate()`` below instead.
 *
 * Vanilla VA: 0x00085700  (mangled: @gravity_integrate_raw@8)
 */
__attribute__((fastcall))
void gravity_integrate_raw(void *config, void *vel_accumulator);


/* ==========================================================================
 * Public — safe C-level wrapper
 * ==========================================================================
 */

/* Integrate gravity one tick.
 *
 * Parameters (all 4-byte, stdcall pushes 20 bytes, callee pops):
 *   player_esi:
 *     Pointer the vanilla function reads via ``[ESI+...]`` — reads
 *     position at +0x24 / +0x28 / +0x2C and a few more slots.  In
 *     the vanilla game this is the player-entity or critter state
 *     struct (the same pointer that's in ESI at the top of
 *     ``FUN_00085F50``).  Pass NULL only if you've verified the
 *     vanilla doesn't dereference it along your call path.
 *
 *   config:
 *     Pointer to an attack-transition-style config struct.  Read
 *     at offsets +0 (int field) and +4 (float field used as
 *     velocity scale); also accessed at +0x3C+ with FLD offsets.
 *
 *   vel_accumulator:
 *     Velocity (or position-integrating) pointer.  Written to
 *     during the integration.  Must point at writable memory.
 *
 *   gravity_dt_product:
 *     Typically ``9.8f * dt`` (from ``AZURIK_GRAVITY_VA``) but
 *     can be any float the caller wants to apply as the
 *     gravitational step.  Vanilla uses a globally-shared
 *     gravity; shims that want per-entity gravity can inject
 *     their own product here.
 *
 *   result_out:
 *     Pointer to a caller-allocated struct that receives the
 *     integrated result.  MUST be at least 0x38 bytes — the
 *     vanilla function writes fields at offsets 0x00, 0x04, 0x08,
 *     0x10, 0x14, 0x18, 0x1C, 0x34 (byte), and others in the
 *     0x20..0x34 range.  Stack-allocated buffers work fine
 *     (``char buf[0x38];``).
 *
 * Thread safety: same as vanilla — NOT re-entrant, must be called
 * from the main simulation thread.
 */
__attribute__((stdcall))
void azurik_gravity_integrate(
    void *player_esi,
    void *config,
    void *vel_accumulator,
    float gravity_dt_product,
    void *result_out);


#ifdef __cplusplus
}
#endif

#endif /* AZURIK_GRAVITY_H */
