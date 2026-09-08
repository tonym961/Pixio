# Pixio - modello della configurazione nginx.
#
# Non è un file di nginx valido: l'helper (pixio-helper apply nginx) legge i frammenti qui
# sotto, li monta secondo le impostazioni `web` di /etc/pixio/config.json (https_enabled,
# redirect_http) e sostituisce i segnaposto @NOME@. Il risultato finisce in
# /etc/nginx/sites-available/pixio, che si riscrive a ogni "apply nginx": non modificarlo a mano.
#
# Ogni frammento comincia con una riga "#@ nome" e finisce dove comincia il successivo.
# I segnaposto da soli su una riga vengono sostituiti con un blocco intero.

#@ base
# Pixio - generato da pixio-helper apply nginx. Non modificare a mano.
log_format pixio '$remote_addr [$time_iso8601] "$request" $status $body_bytes_sent "$http_user_agent"';

@HTTP@

@HTTPS@

#@ http
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    access_log /var/log/nginx/pixio-access.log pixio;
    error_log  /var/log/nginx/pixio-error.log warn;
    client_max_body_size 0;

@PXE@

@APP@
}

#@ https
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    http2 on;
    server_name _;
    ssl_certificate @CERT@;
    ssl_certificate_key @KEY@;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:pixio:10m;
    ssl_session_timeout 1d;
    access_log /var/log/nginx/pixio-access.log pixio;
    error_log  /var/log/nginx/pixio-error.log warn;
    client_max_body_size 0;

@PXE@

@APP@
}

#@ pxe
    # File per i client PXE: contenuto delle ISO montate, file ISO interi, wimboot/memdisk, file iniettati
    location /pxe/ {
        alias /srv/pixio/http/;
        autoindex on;
        autoindex_exact_size off;
        sendfile on;
        tcp_nopush on;
        aio threads;
        output_buffers 2 1m;
        types { }
        default_type application/octet-stream;
        add_header Cache-Control "no-cache";
        access_log /var/log/nginx/pixio-access.log pixio;
    }

#@ app
    # GUI + API + menu di boot (Flask via gunicorn)
    location / {
@PROXY@
    }

#@ app_redirect
    # I client PXE non parlano HTTPS: menu, script di avvio e risposte automatiche restano in chiaro
    location = /boot.ipxe {
@PROXY@
    }

    location /boot/ {
@PROXY@
    }

    location /answers/ {
@PROXY@
    }

    # Tutto il resto (GUI e API) passa a HTTPS
    location / {
        return 308 https://$host$request_uri;
    }

#@ proxy
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        proxy_request_buffering off;
        proxy_buffering off;
