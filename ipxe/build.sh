#!/bin/bash
# Compila iPXE con lo script embedded Pixio. Uso: build.sh <server_ip> [outdir]
set -euo pipefail
SERVER_IP="${1:?server ip}"; OUT="${2:-/srv/pixio/tftp}"
SRC=/opt/pixio/ipxe/src
[ -d "$SRC/.git" ] || git clone --depth 1 https://github.com/ipxe/ipxe.git "$SRC"
cd "$SRC/src"
sed "s/__PIXIO_SERVER_IP__/$SERVER_IP/g" /opt/pixio/ipxe/embed.ipxe > /opt/pixio/ipxe/embed.built.ipxe
mkdir -p config/local
cat > config/local/general.h <<'EOH'
#define DOWNLOAD_PROTO_HTTPS
#define NET_PROTO_IPV6
#define PING_CMD
#define NSLOOKUP_CMD
#define REBOOT_CMD
#define POWEROFF_CMD
#define CONSOLE_CMD
#define IMAGE_PNG
#define NTP_CMD
#define VLAN_CMD
#define PARAM_CMD
EOH
# Console: niente seriale nella build di produzione. Su molti PC la porta seriale non esiste e ogni
# carattere scritto costa attese lunghissime: il menu diventa lento a rispondere ai tasti.
# La variante con seriale serve solo alle prove in QEMU e viene creata a parte (vedi in fondo).
cat > config/local/console.h <<'EOH'
#define CONSOLE_FRAMEBUFFER
EOH
# Tastiera USB: nelle build BIOS iPXE dipende dal supporto legacy del firmware, che su molti PC recenti
# e' disattivato e lascia il menu senza tastiera. Con i driver USB nativi la tastiera funziona comunque.
# In UEFI resta quella del firmware, che funziona gia' bene.
cat > config/local/usb.h <<'EOH'
#define USB_HCD_XHCI
#define USB_HCD_EHCI
#define USB_HCD_UHCI
#define USB_KEYBOARD
EOH
NPROC=$(nproc)
make -j"$NPROC" NO_WERROR=1 EMBED=/opt/pixio/ipxe/embed.built.ipxe \
  bin/undionly.kpxe bin/ipxe.pxe bin-x86_64-efi/ipxe.efi bin-x86_64-efi/snponly.efi bin-i386-efi/ipxe.efi bin/ipxe.lkrn 2>&1 | tail -5 || true
mkdir -p "$OUT"
cp bin/undionly.kpxe "$OUT/undionly.kpxe"
cp bin/ipxe.pxe "$OUT/ipxe-bios.pxe"      # build completa: ha i driver USB, quindi la tastiera funziona
cp bin/ipxe.pxe "$OUT/ipxe.pxe"
cp bin-x86_64-efi/ipxe.efi "$OUT/ipxe.efi"
cp bin-x86_64-efi/snponly.efi "$OUT/snponly.efi"
cp bin-i386-efi/ipxe.efi "$OUT/ipxe32.efi"
cp bin/ipxe.lkrn "$OUT/ipxe.lkrn"
echo "BUILD_OK $(date -Is)" > "$OUT/.ipxe-build"
ls -la "$OUT"

# --- variante per le prove in QEMU: identica ma con l'output anche su seriale
cat > config/local/console.h <<'EOH'
#define CONSOLE_FRAMEBUFFER
#define CONSOLE_SERIAL
EOH
make -j"$NPROC" NO_WERROR=1 EMBED=/opt/pixio/ipxe/embed.built.ipxe bin/undionly.kpxe bin-x86_64-efi/ipxe.efi 2>&1 | tail -3 || true
cp bin/undionly.kpxe "$OUT/undionly-debug.kpxe" 2>/dev/null || true
cp bin-x86_64-efi/ipxe.efi "$OUT/ipxe-debug.efi" 2>/dev/null || true
# ripristina la configurazione di produzione per la prossima build
cat > config/local/console.h <<'EOH'
#define CONSOLE_FRAMEBUFFER
EOH
echo "varianti di prova: $OUT/undionly-debug.kpxe, $OUT/ipxe-debug.efi"

# dnsmasq tiene in cache i file gia' inviati via TFTP: senza riavvio continuerebbe a servire i binari vecchi
systemctl restart dnsmasq 2>/dev/null || true
echo "dnsmasq riavviato: i client riceveranno i binari appena compilati"
