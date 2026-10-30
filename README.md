# exordos_notification

Platform notification service for Exordos. The base element runs the service and its database. Install email as a separate transport element.

## Requirements

- `core` ≥ 0.0.0
- `dbaas` ≥ 2.2.4

## Installation

### 1. Install the element

```bash
exordos em elements install exordos_notification
```

This installs the notification service, its database, IAM users and permissions. Wait for the element to become `ACTIVE`. No mail relay is installed by the base element.

### 2. Install the optional email transport

With `mailaas` available, install email separately:

```bash
exordos em elements install notification_email
```

This adds the SMTP relay/account, provider and sender variables. Application
event types, letter templates and sender configuration belong to the producing
application. All Event submissions and status reads require authorization. See the
[email authentication and rollout guide](docs/email.md).

### 3. Configure email site settings

`notification_email` creates a VS variable `notification_noreply_address` with no default value — it is installation-specific (depends on your mail domain). Until it is set, the SMTP provider resource stays `NEW` and no emails are sent.

Copy the example manifest and fill in your noreply address:

```bash
cp exordos/manifests/site_config.yaml.j2.example exordos/manifests/site_config.yaml.j2
# Edit site_config.yaml.j2 and set the noreply address:
#   value: "noreply@your-mail-domain.example.com"
exordos em elements install exordos/manifests/site_config.yaml.j2
```

The `notification_site_config` element must be installed **once per deployment** after `notification_email` is active. Reinstalling the base or email element will not overwrite it.

### 4. Enable Core email events

Install `core_notification_email` from the Core repository. Its requirements
automatically pull in `notification_email` and the base service. It supplies
Core's registration event type and confirmation letter template, provisions a
managed IAM token, and configures Core's existing asynchronous SDK sender.
Installing the common email transport alone does not enable Core delivery.

### Existing combined-element installations

The previous base manifest owned email resources and exported the sender variables
and email provider. This split changes their element ownership. Do not update a
combined installation in place or remove its existing email resources: its site
configuration still imports the old exports. Preserve the installed manifest and
SMTP/database state. A data-preserving ownership/import migration must be tested
on a copy of that installation before rollout. The new element layout is ready
for fresh installations; the combined-installation migration remains **NOT RUN**.

The initial 2.0.0 email element also owned Core's sender configuration and event
type, while its site configuration owned the confirmation template. Adopting
`core_notification_email` moves these resources to Core. Preserve existing
tokens, event types and customized templates until their ownership has been
migrated; do not install duplicate resources or delete the working transport.

## Architecture

```
IAM (core)  ──event──►  exordos_notification  ──SMTP──►  mailaas exim4 DP
                             │
                             └── PostgreSQL (dbaas)
```

- **notification-cp** — the notification control plane VM (runs the notification service)
- **notification_email** — optional email transport; its mailaas relay is a dedicated `mail.instances` provisioned by metapaas; the notification service authenticates as the `smtp` account
- **VS variables** — email sender/domain values belong to `notification_email` and are set via `site_config`.

## Optional PostgreSQL backups

After `exordos_notification` and `communal_s3` are active, install the separate
backup element (requires `dbaas` ≥ 2.6.2 and `s3aas`):

```bash
exordos em elements install notification_pg_backup
```

`exordos/manifests/notification_pg_backup.yaml.j2` imports the existing
`notification_cluster_pg` and creates its backup policy, a private
`notification-pg-backups` bucket on `communal_s3`, a scoped S3 user, and generated
access, secret and encryption keys. It enables WAL archiving, weekly full backups
and daily incremental backups, retaining two full backups with their incremental
backups and WAL. Preserve the encryption key for restores.

The notification element can be installed without the backup element or S3.

## Restore PostgreSQL backups

After a successful backup is available, install the separate restore element:

```bash
exordos em elements install notification_pg_restore
```

`exordos/manifests/notification_pg_restore.yaml.j2` creates a new PostgreSQL 18
instance, `notification-pg-restored`, with 1 CPU, 2048 MiB RAM and a 20 GiB disk,
matching the source cluster. It imports the source cluster UUID from
`exordos_notification` and the bucket and credentials from `notification_pg_backup`,
then restores through the latest archived WAL. Both elements and `communal_s3`
must remain installed; DBaaS ≥ 2.6.2 is required.

For a restore to a specific time, change `restore_from.target` before installing:

```yaml
target:
  kind: time
  time: "2026-10-06T08:00:00Z"
```

The notification service continues using its original database; switching it to
the restored instance is a separate step. The restored instance has no backup
policy of its own. `restore_from` is applied only when creating the instance;
to repeat the restore, delete the restore element and install it again.
