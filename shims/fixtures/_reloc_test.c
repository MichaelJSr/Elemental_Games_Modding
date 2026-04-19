/* Test fixture ONLY — not a shipping shim.
 *
 * Exercises the Phase-2 relocation pipeline by forcing the compiler
 * to emit both a PC-relative (IMAGE_REL_I386_REL32) call and an
 * absolute-pointer (IMAGE_REL_I386_DIR32) reference to a global.
 *
 * Layout hints for the tests that consume this:
 *
 *   Expected relocations in `.text`:
 *     REL32 at <off>  ->  _helper         (internal call)
 *     DIR32 at <off>  ->  _g_counter      (global read)
 *     DIR32 at <off>  ->  _g_counter      (global write from helper)
 *
 * The exact offsets are compiler-dependent; tests just pin
 * the *shape* (one REL32, two DIR32 to _g_counter) and verify
 * each field has been rewritten to the final XBE VA the layout
 * pass chose for _helper / _g_counter.
 */
unsigned int g_counter = 0;

__attribute__((noinline))
static unsigned int helper(unsigned int x)
{
    g_counter = x;
    return x + 1;
}

__attribute__((noinline))
unsigned int c_reloc_test(unsigned int n)
{
    unsigned int t = helper(n * 3);
    return t + g_counter;
}
