# Authenticated email events

[Русская версия](email.ru.md)

Every Event POST and GET now requires a valid IAM token, including Core email
confirmation events. The standard IAM middleware protects the v1 API; there is
no email bypass for nested EventType lookups. Email senders need
`exordos_notification.event_type_<TYPE_UUID>.create/read`. Catalog access and
source/type authorization are both required. Read-only senders cannot update or
delete events. Management CRUD uses the standard project-scoped IAM controllers;
ordinary authenticated users can still read published catalog entries.

The `notification_email` element supplies the common SMTP provider and sender
settings. It does not configure Core or any other producer, and it does not
install application-specific event types or letter templates.

Install `core_notification_email` from the Core repository to enable Core email
events. Its requirements pull in `notification_email`; that element in turn
requires the base Notification service and mailaas. Core owns its registration
and password-reset event types, letter templates, service identity and sender
configuration.

The Core element follows the existing `metapaas_audit` and `ecosystem_audit`
manifests: it provisions a `type: service` IAM user with only
registration/reset-type create/read and catalog-read permissions, plus a
`$core.iam.tokens` resource with `auto_renew: true`. It renders `:access_token`
into a separate mode-`0600` Core events token drop-in as `exordos_api_token`.

Core's existing token renewal service renews it before half its lifetime has
passed. Config reconciliation rewrites the token drop-in when the token changes
and restarts only `ec-gservice`, so the existing SDK sender reads the new token
for submission and status polling. The static async-client configuration and
event mapping restart `ec-user-api` when installed or changed; token renewal
does not interrupt the Core API. Missing prerequisite files skip the token hook;
a sender restart failure remains an error for the config agent. No password login, separate refresh
protocol, or new SDK/Core authentication implementation is required. Keep
`client_type = async`: registration stores its event locally and the existing
sender service delivers it asynchronously.

Existing installations must preserve the current email resources and custom
templates until their element ownership and imports have been migrated. Do not
install a second copy of the same event type or remove a working SMTP relay to
adopt the new ownership layout.

The initial 2.0.0 email element also owned Core's sender configuration and event
type, while its site configuration owned the confirmation template. Adopting
`core_notification_email` moves these resources to Core. Preserve existing
tokens, event types and customized templates until their ownership has been
migrated; do not install duplicate resources or delete the working transport.

Provision the managed token and verify its config delivery before deploying
Notification's mandatory authentication. Keep the email EventType UUID and
existing v1 payload. Test an actual Core registration through queued event,
authenticated Notification POST/GET and SMTP. Trigger a renewal on a short-lived
test token and verify config replacement, sender restart, an unchanged user API
process, and successful sending with the replacement token. API acceptance alone
does not prove inbox delivery.
