#!/usr/bin/env bash
# Launcher for the Azurik Modding Toolkit GUI on macOS and Linux.
#
# When a .command file is double-clicked from Finder, macOS spawns bash
# with a minimal PATH (`/usr/bin:/bin:/usr/sbin:/sbin`), so Python
# installs under /opt/homebrew/bin, /usr/local/bin, pyenv shims, or the
# official Python.org framework don't appear in `command -v python3`.
# This script extends PATH with every common install location before
# doing the lookup so the GUI launches out-of-the-box for anyone with
# a working Python 3.10+.
#
# If every probe fails the script prints an install hint and pauses so
# the user sees the message before the Terminal window closes.

set -e
cd "$(dirname "$0")"

# Pick up user-installed Python from common spots that Finder skips.
# (Order: homebrew arm64, homebrew x86_64, pyenv shims, Python.org
# framework, then the default PATH.)
export PATH="\
$HOME/.pyenv/shims:\
/opt/homebrew/bin:\
/usr/local/bin:\
/Library/Frameworks/Python.framework/Versions/Current/bin:\
$PATH"

# Also source the user's zsh profile (default shell on modern macOS) so
# things like pyenv/asdf that rely on shell init still work.
for profile in "$HOME/.zprofile" "$HOME/.zshenv" "$HOME/.bash_profile"; do
    if [[ -r "$profile" ]]; then
        # shellcheck disable=SC1090
        source "$profile" >/dev/null 2>&1 || true
    fi
done

# Try the installed console script first (fastest, single import).
if command -v azurik-gui >/dev/null 2>&1; then
    exec azurik-gui
fi

# Then fall back to `python3 -m gui` from the repo root.  This works
# even without `pip install -e .` as long as sv_ttk / platformdirs are
# importable for the chosen interpreter.  If the import fails we
# surface an install hint before the Terminal window closes.
for py in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$py" >/dev/null 2>&1; then
        if "$py" -c "import gui" 2>/dev/null; then
            exec "$py" -m gui
        fi
        echo "Found $py but could not import the \`gui\` package."
        echo "This usually means the project hasn't been installed yet."
        echo
        echo "From this directory, run:"
        echo "    $py -m pip install -e ."
        echo
        echo "Then retry the launcher."
        echo
        read -n 1 -s -r -p "Press any key to close..."
        echo
        exit 1
    fi
done

echo "Could not find Python 3.10 or later."
echo
echo "Install it from https://www.python.org/downloads/ or with Homebrew:"
echo "    brew install python"
echo
echo "If you already have Python installed, make sure it is available in"
echo "your shell's PATH (test: \`python3 --version\` in a new Terminal)."
echo
read -n 1 -s -r -p "Press any key to close..."
echo
exit 1
