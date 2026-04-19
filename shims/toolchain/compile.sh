#!/usr/bin/env bash
# Compile a single C source into an i386 PE-COFF .o usable as a shim.
#
# Usage:
#   toolchain/compile.sh <source.c> [output.o]
#
# If <output.o> is omitted, the output lands at shims/build/<stem>.o.
#
# Flags explained:
#   -target i386-pc-win32        emit PE-COFF for i386 (Xbox-compatible)
#   -ffreestanding               no hosted libc assumptions
#   -nostdlib                    do not link crt / libc
#   -fno-pic                     position-dependent code (no GOT/PLT)
#   -fno-stack-protector         no __security_cookie stub required
#   -fno-asynchronous-unwind-tables  no .eh_frame the XBE can't resolve
#   -Os                          small code by default
#   -c                           produce a .o, not a linked image

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <source.c> [output.o]" >&2
    exit 64
fi

src="$1"
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
if [[ $# -ge 2 ]]; then
    out="$2"
else
    stem=$(basename "$src" .c)
    out="$repo_root/shims/build/$stem.o"
fi

mkdir -p "$(dirname "$out")"

# Detect clang BEFORE `exec` so we can emit a friendly install hint
# instead of the shell's default "clang: command not found".  The
# shim toolchain requires clang (GCC doesn't emit PE-COFF out of the
# box); we don't try to install it automatically because the right
# package manager + version vary by platform.
if ! command -v clang >/dev/null 2>&1; then
    cat >&2 <<'EOF'
ERROR: `clang` not found on PATH.

The shim toolchain needs clang with PE-COFF-i386 support (built-in
since LLVM 7, so almost any modern clang works).  Install one of:

  macOS:     xcode-select --install          (Apple clang)
             brew install llvm               (Homebrew LLVM)
  Debian/Ubuntu: sudo apt install clang
  Fedora:    sudo dnf install clang
  Arch:      sudo pacman -S clang
  Windows:   https://releases.llvm.org/download.html
             (or install via winget / chocolatey / scoop)

After installing, re-run this command.  If you have clang under a
non-standard path, add it to $PATH or symlink into /usr/local/bin.
EOF
    exit 127
fi

exec clang \
    -target i386-pc-win32 \
    -ffreestanding \
    -nostdlib \
    -fno-pic \
    -fno-stack-protector \
    -fno-asynchronous-unwind-tables \
    -I "$repo_root/shims/include" \
    -Os \
    -c \
    -o "$out" \
    "$src"
