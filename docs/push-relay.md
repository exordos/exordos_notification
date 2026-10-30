# Forwarding encrypted mobile notifications

Install the email/base and Firebase changes before this relay extension. The
shared mobile event contract, validation, per-device renderer and errors live in
the Firebase change; this extension adds the `notification` protocol and
`content.kind=notification`, with no new database migration or delivery loop.

The local service accepts an ordinary authorized Event, chooses its configured
Template/Provider and forwards the encrypted snapshot to the upstream Event API.
It never decrypts, enriches or requests message content. The upstream Firebase
renderer produces one data payload per device. Both services use the normal
Event status and inactive-token handling. A queued upstream Event remains
pending locally; it is not reported as FCM acceptance.

## Deployment

1. Install `notification_firebase` on the central sender as described in the
   [Firebase guide](mobile-push.md#installation-and-configuration).
2. On a forwarding installation, render `notification_relay.yaml.j2` with the
   final upstream HTTPS `notification_endpoint`. Install it and wait for ACTIVE.
   Its persistent element UUID is exported as `organization_uuid`.
3. Render local `push_source.yaml.j2` and Workspace's `mobile_push.yaml.j2` with
   `notification_transport=notification_relay`. They import the local identity
   and provision the source account/managed sender token.
4. On the upstream Core, render `push_source.yaml.j2` with that local exported
   `organization_uuid`. This creates the separate upstream source account and
   source-scoped permissions. Supply its IAM credentials only to the local relay
   through the private file below. Each installation gets a separate identity.
5. Deploy the Android consumer, both Notification services and provider config
   before enabling Workspace's producer. Keep its endpoint empty while setting up
   credentials. Update through the normal manifest operation after validation.

Install only one transport element per Notification service. Firebase and relay
manifests publish the same event-type UUIDs and default mobile templates. A relay
installation needs no Firebase credential. Keep existing resources/imports when
upgrading an older combined transport until its ownership migration is tested.

For example, from this checkout (all arguments are non-secret):

```sh
python3 - <<'PY_RENDER'
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined
environment = Environment(loader=FileSystemLoader('exordos/manifests'),
                          undefined=StrictUndefined)
variables = dict(notification_endpoint='https://<UPSTREAM_HOST>/api/workspace/notification',
                 notification_transport='notification_relay')
Path('rendered').mkdir(exist_ok=True)
for name in ['notification_relay', 'push_source']:
    Path(f'rendered/{name}.yaml').write_text(
        environment.get_template(f'{name}.yaml.j2').render(**variables))
PY_RENDER
exordos em elements install rendered/notification_relay.yaml
exordos em elements install rendered/push_source.yaml
```

Relay-only installations require this file, containing the **upstream** Core
IAM service credentials generated on the upstream Core:

```text
/etc/exordos_notification/mobile-push-auth.json
```

The JSON shape accepted by the existing SDK is:

```json
{
  "base_url": "https://<UPSTREAM_HOST>/api/core",
  "username": "<SOURCE_SERVICE_USER>",
  "password": "[REDACTED]",
  "scope": ""
}
```

Only Orion requires `/etc/exordos_notification/firebase-service-account.json`
with the Google service-account credential, permission to send FCM messages and
the FCM HTTP v1 API enabled. A Firebase Android client `google-services.json`
does not grant sending access. Do not distribute the service-account file to
customer realms. Firebase project/IAM/key changes require operator approval.
Android builds consume the matching client config from a private, ignored file.

If a sender credential is lost, identify its user-managed service-account key in
the matching Firebase project and disable that key. Keep Google-managed keys,
unrelated service accounts, Android app configuration and device tokens intact.
Store a replacement sender credential in private credential storage and the
upstream service only, then verify sending and the old key's disabled state.
Never paste either credential into an event, log, review or test record.
Disabling a key does not revoke access tokens already issued from it. Immediate
revocation of those tokens requires disabling the service account and affects
every workload using that account; inspect its usage before taking that step.

The optional gateway exposes the Event API at:

```text
https://<WORKSPACE_HOST>/api/workspace/notification/v1/events/
```

The outer Workspace gateway must terminate HTTPS. It overwrites
`X-Notification-Relay: 1`; Notification rejects legacy events on this ingress.
Mobile events always require their source permission, including direct internal
requests. Internal legacy Core/email callers keep their existing v1 path.
No new v2 path or callback endpoint is introduced. Restrict direct port 8080 to
the trusted realm network. Verify that upstream and local relay endpoints are
different instances, to avoid a forwarding loop.


Both the relay endpoint and the IAM `base_url` in its credential file must use
HTTPS. The existing SDK IAM client uses a redacting,
non-redirecting HTTP session for login, refresh, Event submission and status
polling. Cleartext URLs are rejected before credentials are read. Request and
response bodies stay redacted even if an upstream error echoes input. Only
methods, URLs and status codes remain available for diagnosis.

## Verification

Run the aggregate suite with a dedicated PostgreSQL database via
`DATABASE_URI`. Relay coverage uses a real TLS listener, signed JWT policies,
the real SDK HTTP client and PostgreSQL, including duplicate submission,
partial inactive-token errors, pending polling, permanent rejection and redirect
refusal. The Firebase call is mocked in these automated transport tests; actual
provider delivery still needs the real-device acceptance in the Firebase guide.
