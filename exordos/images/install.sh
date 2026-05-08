#!/usr/bin/env bash

# Copyright 2026 Genesis Corporation.
#
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

set -eu
set -x
set -o pipefail


GC_PATH="/opt/exordos_notification"
GC_CFG_DIR=/etc/exordos_notification
VENV_PATH="$GC_PATH/.venv"

GC_PG_USER="exordos_notification"
GC_PG_PASS="pass"
GC_PG_DB="exordos_notification"

SYSTEMD_SERVICE_DIR=/etc/systemd/system/

# Install packages
sudo apt update
sudo apt dist-upgrade -y
sudo apt install -y \
    postgresql \
    libev-dev
curl -LsSf https://releases.astral.sh/github/uv/releases/download/0.10.12/uv-installer.sh | sh
source "$HOME"/.local/bin/env

# Default creds for genesis notification services
sudo -u postgres psql -c "CREATE ROLE $GC_PG_USER WITH LOGIN PASSWORD '$GC_PG_PASS';"
sudo -u postgres psql -c "CREATE DATABASE $GC_PG_USER OWNER $GC_PG_DB;"

# Install genesis core
sudo mkdir -p $GC_CFG_DIR
sudo cp "$GC_PATH/etc/exordos_notification/exordos_notification.conf" $GC_CFG_DIR/
sudo cp "$GC_PATH/etc/exordos_notification/logging.yaml" $GC_CFG_DIR/

cd "$GC_PATH"
uv sync
source "$GC_PATH"/.venv/bin/activate

# Apply migrations
ra-apply-migration --config-dir "$GC_PATH/etc/exordos_notification/" --path "$GC_PATH/migrations"
deactivate

# Create links to venv
sudo ln -sf "$VENV_PATH/bin/exordos-notification-mail-agent" "/usr/bin/exordos-notification-mail-agent"
sudo ln -sf "$VENV_PATH/bin/exordos-notification-builder-agent" "/usr/bin/exordos-notification-builder-agent"
sudo ln -sf "$VENV_PATH/bin/exordos-notification-user-api" "/usr/bin/exordos-notification-user-api"

# Install Systemd service files
sudo cp "$GC_PATH/etc/systemd/exordos-notification-mail-agent.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-notification-builder-agent.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-notification-user-api.service" $SYSTEMD_SERVICE_DIR

# Enable genesis notification services
sudo systemctl enable \
    exordos-notification-mail-agent \
    exordos-notification-builder-agent \
    exordos-notification-user-api


cat <<EOT | sudo tee /etc/motd
▄▖       ▌      ▄▖
▙▖▚▘▛▌▛▘▛▌▛▌▛▘  ▌ ▛▌▛▘█▌
▙▖▞▖▙▌▌ ▙▌▙▌▄▌  ▙▖▙▌▌ ▙▖


Welcome to Exordos virtual machine!

All materials can be found here:
https://github.com/exordos

EOT
