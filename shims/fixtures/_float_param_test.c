/* Test fixture ONLY — not a shipping shim.
 *
 * Exercises the per-apply float-parameter injection pipeline
 * (:class:`spec.FloatParam` on Python side, ``AZURIK_FLOAT_PARAM``
 * macro on C side).  Two late-bound constants live in ``.rdata``;
 * the shim multiplies them together and returns the result.  The
 * apply pipeline overwrites the two slots with caller-supplied
 * values, so a single compiled ``.o`` can be re-used across every
 * slider setting without recompiling.
 *
 * Expected shape after compile:
 *
 *   ``.text``    small body: load two floats, multiply, return.
 *                Two IMAGE_REL_I386_DIR32 relocations pointing at
 *                ``_gravity_scale`` / ``_walk_scale`` in ``.rdata``.
 *   ``.rdata``   two 4-byte floats (8 bytes total).  Exact layout
 *                is compiler-dependent; tests match by symbol
 *                lookup, not fixed offsets.
 *
 * The entry symbol ``_c_float_param_test`` is the target for the
 * trampoline's ``CALL rel32``.  Tests read back the ``.rdata``
 * bytes after apply to verify the user's slider values landed.
 */

#include "azurik.h"

AZURIK_FLOAT_PARAM(gravity_scale, 1.0f);
AZURIK_FLOAT_PARAM(walk_scale, 2.5f);

__attribute__((noinline, used))
float c_float_param_test(void)
{
    /* Read both params and combine.  The multiplication prevents
     * the compiler from folding either to a constant — one of the
     * failure modes the ``volatile`` in AZURIK_FLOAT_PARAM is
     * meant to defend against. */
    return gravity_scale * walk_scale;
}
