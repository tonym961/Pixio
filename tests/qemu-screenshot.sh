#!/bin/bash
# Screenshot del menu iPXE in QEMU: qemu-screenshot.sh bios|uefi <out.png> [secondi di attesa]
set -uo pipefail
MODE="${1:-bios}"; OUT="${2:-/tmp/pixio-menu-$MODE.png}"; WAIT="${3:-75}"
SOCK=$(mktemp -u /tmp/qemu-mon.XXXX); TFTP=/srv/pixio/tftp
if [ "$MODE" = "uefi" ]; then
  VARS=$(mktemp /tmp/ovmf-vars.XXXX); cp /usr/share/OVMF/OVMF_VARS_4M.fd "$VARS"
  qemu-system-x86_64 -m 1024 -smp 2 -display none -vga std -monitor unix:$SOCK,server,nowait -device virtio-rng-pci \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd -drive if=pflash,format=raw,file="$VARS" \
    -netdev user,id=n0,tftp=$TFTP,bootfile=ipxe.efi -device virtio-net-pci,netdev=n0,romfile=,bootindex=1 -no-reboot >/dev/null 2>&1 &
else
  qemu-system-x86_64 -m 1024 -smp 2 -display none -vga std -monitor unix:$SOCK,server,nowait \
    -netdev user,id=n0,tftp=$TFTP,bootfile=undionly.kpxe -device e1000,netdev=n0 -boot n -no-reboot >/dev/null 2>&1 &
fi
QPID=$!
sleep "$WAIT"
PPM="${OUT%.png}.ppm"
echo "screendump $PPM" | timeout 10 socat - UNIX-CONNECT:$SOCK >/dev/null 2>&1 || printf 'screendump %s\n' "$PPM" | timeout 10 python3 -c "
import socket,sys; s=socket.socket(socket.AF_UNIX); s.connect('$SOCK'); s.recv(4096); s.sendall(sys.stdin.read().encode()); import time; time.sleep(2); s.close()"
sleep 2; kill $QPID 2>/dev/null; wait $QPID 2>/dev/null; rm -f "$SOCK" "${VARS:-}"
python3 -c "from PIL import Image; Image.open('$PPM').save('$OUT'); print('screenshot: $OUT')" && rm -f "$PPM"
