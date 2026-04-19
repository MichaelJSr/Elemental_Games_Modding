/* Fixture: a "shared library" shim exporting a helper that multiple
 * trampoline shims can call.  Phase 2 E (shared-library layout) places
 * this once per session and resolves external references from other
 * shims against the single placement.  Without E, each trampoline
 * that calls `shared_double` would install its own private copy.
 *
 * The helper is deliberately trivial — the point of this file is to
 * exercise the layout-session plumbing, not to prove the arithmetic.
 */

/* Two exported helpers; the consumer shims below reference them by
 * name.  `int` return + `__attribute__((stdcall))` matches the
 * default mangling clang produces on i386-pc-win32. */
__attribute__((stdcall))
int shared_double(int x) {
    return x + x;
}

__attribute__((stdcall))
int shared_triple(int x) {
    return shared_double(x) + x;
}
