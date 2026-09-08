# Pixio - generato da pixio-helper apply nginx. Non modificare a mano.
log_format pixio '$remote_addr [$time_iso8601] "$request" $status $body_bytes_sent "$http_user_agent"';

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    access_log /var/log/nginx/pixio-access.log pixio;
    error_log  /var/log/nginx/pixio-error.log warn;
    client_max_body_size 0;

    # File per i client PXE: contenuto delle ISO montate, file ISO interi, wimboot/memdisk, file iniettati
    location /pxe/ {
        alias /srv/pixio/http/;
        autoindex on;
        autoindex_exact_size off;
        sendfile on;
        tcp_nopush on;
        aio threads;
        directio 8m;
        output_buffers 2 1m;
        types { }
        default_type application/octet-stream;
        add_header Accept-Ranges bytes;
        add_header Cache-Control "no-cache";
        access_log /var/log/nginx/pixio-access.log pixio;
    }

    # GUI + API + menu di boot (Flask via gunicorn)
    location / {
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
    }
}
