#!/usr/bin/env bash
# Install an already-built Rust binary. Compilation is performed as your user.
set -euo pipefail
source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install_root=/usr/local/lib/trackpoint-control
exec_path=/usr/local/bin/trackpoint-control
unit_dir=/etc/systemd/system
config_path=/etc/trackpoint-control.toml
marker=$install_root/.trackpoint-control-install
action=install
binary=$source_dir/target/release/trackpoint-control
if [[ -x "$source_dir/bin/trackpoint-control" ]]; then binary=$source_dir/bin/trackpoint-control; fi
while (($#)); do
    case "$1" in
        --uninstall) action=uninstall; shift ;;
        --binary) [[ $# -ge 2 ]] || { echo '--binary requires a path' >&2; exit 2; }; binary=$2; shift 2 ;;
        --help|-h) echo "Usage: sudo bash install.sh [--binary PATH | --uninstall]"; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
[[ $(id -u) == 0 ]] || { echo 'Use sudo bash install.sh' >&2; exit 1; }
if [[ -L "$install_root" || ( -e "$install_root" && ! -f "$marker" ) ]]; then
    echo "Refusing to replace an unrecognized installation: $install_root" >&2
    exit 1
fi
if [[ -f "$marker" ]]; then
    [[ -x "$exec_path" && ! -L "$exec_path" ]] || { echo 'Existing executable is missing or is a symlink' >&2; exit 1; }
else
    for owned_path in "$exec_path" "$unit_dir/trackpoint-control.service" "$unit_dir/trackpoint-control-recover.service"; do
        [[ ! -e "$owned_path" && ! -L "$owned_path" ]] || { echo "Refusing to replace unrelated file: $owned_path" >&2; exit 1; }
    done
fi

if [[ "$action" == uninstall ]]; then
    [[ -f "$marker" ]] || { echo 'No owned installation found' >&2; exit 1; }
    systemctl disable --now trackpoint-control.service
    "$exec_path" restore
    rm -f -- "$exec_path" "$unit_dir/trackpoint-control.service" "$unit_dir/trackpoint-control-recover.service"
    rm -rf -- "$install_root"
    systemctl daemon-reload
    echo "Uninstalled. Kept your settings in $config_path."
    exit 0
fi

# Validate the replacement and all source files before stopping working input.
[[ -x "$binary" ]] || { echo 'Build first as your user: cargo build --release --locked' >&2; exit 1; }
version=$("$binary" --version)
[[ "$version" == 'trackpoint-control 0.2.0' ]] || { echo "Unexpected binary: $version" >&2; exit 1; }
for src_file in config.example.toml packaging/trackpoint-control.service packaging/trackpoint-control-recover.service; do
    [[ -f "$source_dir/$src_file" ]] || { echo "Missing source file: $src_file" >&2; exit 1; }
done
if [[ -f "$marker" ]]; then
    systemctl stop trackpoint-control.service
    "$exec_path" restore
fi
install -d -m 0755 -- "$install_root" /usr/local/bin
replacement=$(mktemp /usr/local/bin/.trackpoint-control.XXXXXX)
trap 'rm -f -- "$replacement"' EXIT
install -m 0755 -- "$binary" "$replacement"
mv -fT -- "$replacement" "$exec_path"
printf '%s\n' "$version (Rust)" > "$marker"
chmod 0644 "$marker"
install -m 0644 -- "$source_dir/packaging/trackpoint-control.service" "$unit_dir/trackpoint-control.service"
install -m 0644 -- "$source_dir/packaging/trackpoint-control-recover.service" "$unit_dir/trackpoint-control-recover.service"
if [[ ! -e "$config_path" && ! -L "$config_path" ]]; then
    install -m 0644 -- "$source_dir/config.example.toml" "$config_path"
fi
# The recognized 0.1 installation stored only its Python package here.
rm -rf -- "$install_root/trackpoint_control"
systemctl daemon-reload
echo 'Installed Rust driver; service is stopped and boot enablement is preserved.'
echo 'Trial: sudo trackpoint-control trial --seconds 120 --enable-pointer --config /etc/trackpoint-control.toml'
echo 'Start: sudo systemctl start trackpoint-control'
