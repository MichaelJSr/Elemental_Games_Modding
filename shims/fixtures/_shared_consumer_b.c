/* Fixture: second trampoline shim calling the SAME shared helper as
 * _shared_consumer_a.c.  Key assertion the E test builds on: both
 * consumer shims must resolve `_shared_double@4` to the SAME VA in
 * the final XBE, i.e. the shared lib is placed once.
 */

__attribute__((stdcall))
int shared_double(int x);

__attribute__((stdcall))
int c_consumer_b(int x) {
    return shared_double(x) + 2;
}
