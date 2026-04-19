/* Fixture: trampoline-style shim that calls `shared_double`.  The
 * extern's mangled name (`_shared_double@4`) appears as an undefined
 * COFF symbol in this file's .o; the session's shared-library map
 * resolves it to the placement from _shared_lib_test.c.
 */

__attribute__((stdcall))
int shared_double(int x);

__attribute__((stdcall))
int c_consumer_a(int x) {
    return shared_double(x) + 1;
}
