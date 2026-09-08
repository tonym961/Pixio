#!/bin/bash
# Pixio - installazione/aggiornamento idempotente su Debian 12/13. Eseguire come root.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
CODE=/opt/pixio
SRV=/srv/pixio
say(){ echo -e "\e[1;36m[pixio]\e[0m $*"; }

[ "$(id -u)" = 0 ] || { echo "Eseguire come root"; exit 1; }
[ -d "$CODE" ] || { echo "Codice non trovato in $CODE"; exit 1; }

say "Pacchetti"
apt-get update -qq
apt-get install -y -qq dnsmasq nginx samba smbclient cifs-utils python3 python3-flask python3-werkzeug python3-jinja2 gunicorn \
  python3-psutil wimtools syslinux-common memtest86+ ipxe curl git jq rsync sudo \
  build-essential liblzma-dev zlib1g-dev perl mtools >/dev/null

say "Utente di servizio"
getent group pixio >/dev/null || groupadd --system pixio
id pixio >/dev/null 2>&1 || useradd --system -g pixio -G systemd-journal,adm -d /var/lib/pixio -s /usr/sbin/nologin pixio
usermod -a -G systemd-journal,adm pixio
# nginx deve leggere i file: la libreria e la cache sono world-readable, i loop mount pure

say "Directory"
mkdir -p $SRV/{sources,library,cache,tftp,detect,http/iso,http/isofile,http/boot,http/inject,http/drivers} /etc/pixio/sources /var/lib/pixio/{jobs,uploads} /var/log/pixio
chown -R pixio:pixio /var/lib/pixio /var/log/pixio $SRV/library $SRV/cache $SRV/http/inject $SRV/http/drivers
chmod 2775 $SRV/library $SRV/cache $SRV/http/drivers
chmod 755 $SRV $SRV/http $SRV/http/iso $SRV/http/isofile $SRV/http/boot $SRV/tftp
chmod 700 /etc/pixio/sources
chown pixio:pixio /etc/pixio; chmod 750 /etc/pixio
touch /etc/pixio/config.json; chown pixio:pixio /etc/pixio/config.json; chmod 640 /etc/pixio/config.json
[ -s /etc/pixio/config.json ] || echo '{}' > /etc/pixio/config.json

say "Helper privilegiato e sudoers"
install -m 755 $CODE/helper/pixio-helper /usr/local/sbin/pixio-helper
install -m 440 $CODE/etc/sudoers-pixio /etc/sudoers.d/pixio
visudo -cf /etc/sudoers.d/pixio >/dev/null
install -m 755 $CODE/bin/pixio-admin /usr/local/sbin/pixio-admin

say "File di boot (wimboot, memdisk, memtest, iPXE)"
cd $SRV/http/boot
[ -s wimboot ] || curl -fsSL -o wimboot https://github.com/ipxe/wimboot/releases/latest/download/wimboot || echo "ATTENZIONE: wimboot non scaricato (serve internet)"
cp -f /usr/lib/syslinux/memdisk memdisk 2>/dev/null || true
cp -f /boot/memtest86+x64.bin /boot/memtest86+x64.efi . 2>/dev/null || true
chmod 644 * 2>/dev/null || true
if [ ! -s $SRV/tftp/.ipxe-build ]; then
  IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
  say "Compilo iPXE con script embedded (server ${IP:-?}), 2-4 minuti..."
  if ! $CODE/ipxe/build.sh "${IP:-127.0.0.1}" $SRV/tftp >/var/log/pixio/ipxe-build.log 2>&1; then
    say "Build iPXE fallita: uso i binari del pacchetto Debian (senza script embedded)"
    cp -f /usr/lib/ipxe/undionly.kpxe /usr/lib/ipxe/ipxe.pxe /usr/lib/ipxe/ipxe.efi /usr/lib/ipxe/snponly.efi $SRV/tftp/ 2>/dev/null || true
  fi
fi
chmod 644 $SRV/tftp/* 2>/dev/null || true
# UEFI HTTP Boot: il firmware scarica iPXE via HTTP da /pxe/tftp/
[ -L $SRV/http/tftp ] || ln -s $SRV/tftp $SRV/http/tftp

say "Servizi systemd"
install -m 644 $CODE/systemd/pixio.service /etc/systemd/system/pixio.service
install -m 644 $CODE/systemd/pixio-mounts.service /etc/systemd/system/pixio-mounts.service
systemctl daemon-reload
systemctl enable pixio-mounts.service pixio.service >/dev/null 2>&1

say "Configurazione iniziale (dnsmasq/nginx/samba) dalle impostazioni"
sudo -u pixio python3 - <<'PY'
import sys; sys.path.insert(0, '/opt/pixio')
from pixio import settings as S
cfg = S.load(); S.save(cfg)
print("  interfaccia", cfg['network']['interface'], "ip", cfg['network']['server_ip'], "modalità", cfg['network']['dhcp_mode'])
PY
# dnsmasq di Debian: la config di default fa solo DNS; noi usiamo /etc/dnsmasq.d/pixio.conf con port=0
sed -i 's|^#\?conf-dir=/etc/dnsmasq.d/,\*.conf|conf-dir=/etc/dnsmasq.d/,*.conf|' /etc/dnsmasq.conf
/usr/local/sbin/pixio-helper apply all || true
systemctl disable --now nmbd >/dev/null 2>&1 || true

say "Avvio Pixio"
systemctl restart pixio-mounts.service || true
systemctl restart pixio.service
sleep 2
IP=$(sudo -u pixio python3 -c "import sys;sys.path.insert(0,'/opt/pixio');from pixio import settings;print(settings.load()['network']['server_ip'])")
say "Fatto. GUI: http://$IP/   (al primo accesso imposta la password amministratore)"
