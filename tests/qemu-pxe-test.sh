#!/bin/bash
# Test di boot PXE con QEMU (TCG, senza KVM) e rete user-mode: verifica che iPXE parta, chiami il menu Pixio e (opzionale) avvii un kernel.
# -cpu max non e' un dettaglio: la CPU predefinita di QEMU (qemu64) non ha SSE4.2 ne' POPCNT, che Windows 11 24H2
# pretende, e il guest si riavvia subito dopo "Loading files...". Sembra un guasto di Pixio e non lo e'.
# Uso: qemu-pxe-test.sh bios|uefi [secondi] [--expect "testo"]
# Esito: 0 se sul seriale compare il menu Pixio (o il testo atteso), 1 altrimenti. Log in /var/log/pixio/qemu-<modo>.log
# Si usano di proposito le varianti *-debug: quelle di produzione sono compilate senza console seriale
# (serve a rendere il menu reattivo sui PC veri) e qui lascerebbero un log vuoto.
set -uo pipefail
MODE="${1:-bios}"; SECS="${2:-150}"; EXPECT="${4:-PIXIO - Avvio da rete}"
[ "${3:-}" = "--expect" ] || EXPECT="PIXIO - Avvio da rete"
LOG=/var/log/pixio/qemu-$MODE.log; mkdir -p /var/log/pixio; rm -f "$LOG"
TFTP=/srv/pixio/tftp
if [ "$MODE" = "uefi" ]; then
  VARS=$(mktemp /tmp/ovmf-vars.XXXX); cp /usr/share/OVMF/OVMF_VARS_4M.fd "$VARS"
  timeout "$SECS" qemu-system-x86_64 -cpu max -m 1024 -smp 2 -display none -monitor none -serial file:"$LOG" \
    -device virtio-rng-pci -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd -drive if=pflash,format=raw,file="$VARS" \
    -netdev user,id=n0,tftp=$TFTP,bootfile=ipxe-debug.efi -device virtio-net-pci,netdev=n0,romfile=,bootindex=1 -no-reboot >/dev/null 2>&1
  rm -f "$VARS"
else
  timeout "$SECS" qemu-system-x86_64 -cpu max -m 1024 -smp 2 -display none -monitor none -serial file:"$LOG" \
    -netdev user,id=n0,tftp=$TFTP,bootfile=undionly-debug.kpxe -device e1000,netdev=n0 -boot n -no-reboot >/dev/null 2>&1
fi
CLEAN=$(sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g; s/\x1b\[=3h//g' "$LOG" | tr -d '\r')
if grep -q "$EXPECT" <<<"$CLEAN"; then echo "OK ($MODE): trovato \"$EXPECT\""; exit 0; fi
echo "FALLITO ($MODE): \"$EXPECT\" non trovato. Ultime righe:"; grep -v '^\s*$' <<<"$CLEAN" | tail -15; exit 1
