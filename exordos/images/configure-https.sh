#!/usr/bin/env bash
# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.

set -eu

site=/etc/nginx/sites-available/exordos-notification.conf
enabled_site=/etc/nginx/sites-enabled/exordos-notification.conf
tls_bundle=/etc/nginx/notification-tls/api.pem

disable_https() {
    if [[ -L $enabled_site ]]; then
        systemctl stop nginx.service
        unlink "$enabled_site"
    fi
}

if [[ ! -s $tls_bundle ]]; then
    disable_https
    exit 0
fi

if [[ ! -L $enabled_site ]]; then
    ln -s "$site" "$enabled_site"
fi
if ! nginx -t -q; then
    disable_https
    exit 1
fi
systemctl reload-or-restart nginx.service
