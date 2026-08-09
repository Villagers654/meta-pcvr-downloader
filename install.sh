#!/usr/bin/env bash
set -euo pipefail

command -v python3 >/dev/null || {
  echo "Python 3 is required." >&2
  exit 1
}

install_root="${XDG_DATA_HOME:-$HOME/.local/share}/meta-pcvr-downloader"
bin_root="${XDG_BIN_HOME:-$HOME/.local/bin}"
python3 -m venv "$install_root"
"$install_root/bin/python" -m pip install --upgrade pip
"$install_root/bin/pip" install .
mkdir -p "$bin_root"
ln -sfn "$install_root/bin/meta-pcvr" "$bin_root/meta-pcvr"

echo "Installed meta-pcvr to $bin_root/meta-pcvr"
case ":$PATH:" in
  *":$bin_root:"*) ;;
  *) echo "Add $bin_root to PATH to run meta-pcvr from any directory." ;;
esac
