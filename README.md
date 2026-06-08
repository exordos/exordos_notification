# exordos_notification

Platform notification service for Exordos. Sends email confirmations and other transactional emails via a dedicated mailaas relay.

## Requirements

- `core` ≥ 0.0.0
- `dbaas` ≥ 2.2.4
- `mailaas` ≥ 0.0.0

## Installation

### 1. Install the element

```bash
exordos em elements install exordos_notification
```

This installs the notification service, its database, mail relay instance, IAM users and permissions. Wait for the element to become `ACTIVE`.

### 2. Configure site-specific settings

`exordos_notification` creates a VS variable `notification_noreply_address` with no default value — it is installation-specific (depends on your mail domain). Until it is set, the SMTP provider resource stays `NEW` and no emails are sent.

Copy the example manifest and fill in your noreply address:

```bash
cp exordos/manifests/site_config.yaml.j2.example exordos/manifests/site_config.yaml.j2
# Edit site_config.yaml.j2 and set the noreply address:
#   value: "noreply@your-mail-domain.example.com"
exordos em elements install exordos/manifests/site_config.yaml.j2
```

The `notification_site_config` element must be installed **once per deployment** after `exordos_notification` is active. Reinstalling `exordos_notification` will not overwrite it.

## Architecture

```
IAM (core)  ──event──►  exordos_notification  ──SMTP──►  mailaas exim4 DP
                             │
                             └── PostgreSQL (dbaas)
```

- **notification-cp** — the notification control plane VM (runs the notification service)
- **mailaas relay** — a dedicated `mail.instances` provisioned by metapaas; the notification service authenticates as the `smtp` account
- **VS variables** — `notification_noreply_address` (set via `site_config`) and `notification_endpoint` (auto-set by the manifest)
