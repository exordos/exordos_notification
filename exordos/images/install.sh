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
BOOTSTRAP_PATH="/var/lib/exordos/bootstrap/scripts"

SYSTEMD_SERVICE_DIR=/etc/systemd/system/

# Install packages
sudo apt update
sudo apt dist-upgrade -y
sudo apt install -y \
    libev-dev \
    j2cli \
    postgresql-client-18 \
    yq

curl -LsSf https://releases.astral.sh/github/uv/releases/download/0.10.12/uv-installer.sh | sh
source "$HOME"/.local/bin/env

# Install exordos notification
sudo mkdir -p $GC_CFG_DIR
sudo cp "$GC_PATH/etc/exordos_notification/exordos_notification.conf.j2" $GC_CFG_DIR/
sudo cp "$GC_PATH/etc/exordos_notification/logging.yaml" $GC_CFG_DIR/
sudo cp "$GC_PATH/exordos/images/bootstrap.sh" $BOOTSTRAP_PATH/0100-notification-bootstrap.sh

cd "$GC_PATH"
uv sync

# Create links to venv
sudo ln -sf "$VENV_PATH/bin/exordos-notification-mail-agent" "/usr/bin/exordos-notification-mail-agent"
sudo ln -sf "$VENV_PATH/bin/exordos-notification-builder-agent" "/usr/bin/exordos-notification-builder-agent"
sudo ln -sf "$VENV_PATH/bin/exordos-notification-user-api" "/usr/bin/exordos-notification-user-api"

# Install Systemd service files
sudo cp "$GC_PATH/etc/systemd/exordos-notification-mail-agent.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-notification-builder-agent.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-notification-user-api.service" $SYSTEMD_SERVICE_DIR


cat <<EOT | sudo tee /etc/motd
▄▖       ▌      ▄▖
▙▖▚▘▛▌▛▘▛▌▛▌▛▘  ▌ ▛▌▛▘█▌
▙▖▞▖▙▌▌ ▙▌▙▌▄▌  ▙▖▙▌▌ ▙▖


Welcome to Exordos virtual machine!

All materials can be found here:
https://github.com/exordos

EOT
