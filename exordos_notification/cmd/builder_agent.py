#    Copyright 2025 Genesis Corporation.
#
#    All Rights Reserved.
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

import logging
import sys

from oslo_config import cfg
from restalchemy.common import config_opts as ra_config_opts
from restalchemy.storage.sql import engines

from exordos_notification.clients import iam
from exordos_notification.common import config
from exordos_notification.common import log as infra_log
from exordos_notification.services.builders import agents

DOMAIN = "builder_agent"
DOMAIN_IAM_CLIENT = "iam_client"


iam_client_cli_opts = [
    cfg.StrOpt(
        "username",
        required=True,
        help="IAM username",
    ),
    cfg.StrOpt(
        "password",
        required=True,
        help="IAM password",
        secret=True,
    ),
    cfg.URIOpt(
        "endpoint",
        default="http://core.local.genesis-core.tech:11010",
        help="IAM endpoint",
    ),
    cfg.IntOpt(
        "timeout",
        default=5,
        min=0,
        help="IAM timeout",
    ),
]


CONF = cfg.CONF
ra_config_opts.register_posgresql_db_opts(CONF)
CONF.register_cli_opts(iam_client_cli_opts, DOMAIN_IAM_CLIENT)


def main():
    config.parse(sys.argv[1:])

    infra_log.configure()
    log = logging.getLogger(__name__)

    engines.engine_factory.configure_postgresql_factory(CONF)
    iam_client = iam.IAMClient(
        endpoint=CONF[DOMAIN_IAM_CLIENT].endpoint,
        username=CONF[DOMAIN_IAM_CLIENT].username,
        password=CONF[DOMAIN_IAM_CLIENT].password,
        timeout=CONF[DOMAIN_IAM_CLIENT].timeout,
    )

    service = agents.EventBuilderAgent(
        iam_client=iam_client,
        iter_min_period=3,
    )

    service.start()

    log.info("Bye!!!")


if __name__ == "__main__":
    main()
