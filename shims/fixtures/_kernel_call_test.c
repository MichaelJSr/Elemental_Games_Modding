/* Fixture: shim that calls a kernel import.  Exercises D1's
 * stub-generation + REL32 resolution path via layout_coff.
 */
#include "azurik_kernel.h"

void c_kernel_test(void) {
    DbgPrint("hello from shim");
}
