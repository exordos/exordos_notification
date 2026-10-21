# Encrypted Workspace mobile pushes

This extension uses the existing v1 Event → Template → Provider pipeline.
There are no registration revisions, content callbacks, v2 event endpoint, or
additional public delivery states. Android is the first supported client.

```text
Android -- public key + FCM token --> Workspace push_devices
Workspace message/read transaction -- encrypted event --> durable outbox
Workspace worker --> local Notification -- notification protocol --> upstream Notification
upstream Notification -- firebase protocol --> FCM -- data payload --> Android
Android -- decrypt locally --> system notification -- click --> saved account and chat
```

The server/FCM delivery lifetime is at most one hour. Android keeps received
unread notifications and their authenticated routes without an age deadline.
It stores only routing and receipt decisions on disk; restored cards use a generic
preview because decrypted text stays in process memory. Workspace removes expired
ciphertext/device snapshots but retains minimal delivery routes so later reads or
deletions can cancel an existing notification, including one shown weeks earlier.

A local Notification chooses the user's bound templates, falling back to the
published defaults. A forwarding template has `content.kind=notification` and
passes the complete Event unchanged. On Orion, the Firebase template has
`content.kind=mobile_push` and expands the device list. Only Orion possesses
Firebase sender credentials. A forwarded foreign installation does not use
Orion's user bindings or IAM profile lookup. Set `push.local_organization_uuid`
on each Notification to its local Workspace installation UUID.

## Device registration

Workspace already owns the authenticated registration API:

- `PUT /api/workspace/v1/push_devices/{device_uuid}` replaces a registration.
- `DELETE /api/workspace/v1/push_devices/{device_uuid}` removes it idempotently.
- The authenticated token supplies `project_id` and `user_uuid`.

```json
{
  "transport": "fcm",
  "platform": "android",
  "registration_token": "<FCM_REGISTRATION_TOKEN>",
  "encryption": {
    "kind": "HPKE",
    "algorithm": "HPKE-v1-BASE-X25519-HKDF-SHA256-AES-256-GCM",
    "key_uuid": "<KEY_UUID>",
    "public_key": "<BASE64URL_RAW_X25519_PUBLIC_KEY>"
  }
}
```

Android keeps one device UUID and push key pair per saved server/account/project.
The private key is wrapped by Android Keystore through encrypted preferences,
separate from chat keys, and excluded from cloud backup and device transfer.
WorkManager reconciles the latest FCM token after login, project selection,
`onNewToken`, launch and network retries. A token refresh replaces the existing
registration without changing its UUID or key pair. `/me` and the registration
response verify the owner. Switching owners replaces the local push keys and
removes the old owner's displayed notifications.

Logout attempts DELETE while credentials still exist, then removes the local
push key and that connection's notifications. Registration and logout are
serialized. If the network is unavailable, server-side removal cannot be
confirmed; removing the local key still prevents further decryption/display.
Other accounts on the same phone retain their own keys and registrations.

The upstream Notification relay endpoint must use HTTPS; cleartext endpoints are
rejected before credentials are loaded or an authenticated request is sent.
The relay does not follow redirects, including POST redirects that could resend
device addresses outside TLS. Configure the final upstream URL directly.
User-exchange API responses keep the original v1 `user_id` field alongside the
canonical `user_uuid`; new event producers use `user_uuid`.

## Events and encryption

The kinds and their fixed EventType UUIDs are:

| kind | EventType UUID |
| --- | --- |
| `workspace_message_create` | `864b5689-2675-5cbf-b545-5307fa158449` |
| `workspace_message_cancelled` | `90492b40-2a35-56aa-8821-82d383fe8730` |

Workspace enriches the content and encrypts it **before** it leaves the backend.
A minimal plaintext preview is:

```json
{"kind":"message","sender_name":"Alice","text":"Hello"}
```

The preview takes at most 128 UTF-8 bytes of sender name and 768 bytes of message
text, cutting at a character boundary. It contains no image download or resolver
requirement. Existing end-to-end encrypted chat content remains opaque to the
backend; it produces a generic encrypted-message preview. Key request/grant and
other protocol-only messages do not generate a user alert. This push encryption
protects the backend-to-device content; it does not make ordinary Workspace
message storage end-to-end encrypted.

One Event targets one recipient user, with independently encrypted content for
each registered Android device:

```json
{
  "uuid": "<NOTIFICATION_EVENT_UUID>",
  "project_id": "<PROJECT_UUID>",
  "event_type": "/v1/event_types/864b5689-2675-5cbf-b545-5307fa158449",
  "exchange": {"kind":"User","user_uuid":"<RECIPIENT_UUID>"},
  "event_params": {
    "kind": "workspace_message_create",
    "organization_uuid": "<WORKSPACE_INSTALLATION_UUID>",
    "project_uuid": "<PROJECT_UUID>",
    "stream_uuid": "<STREAM_UUID>",
    "topic_uuid": "<TOPIC_UUID>",
    "message_uuid": "<PUBLIC_MESSAGE_PLACEMENT_UUID>",
    "devices": [{
      "device_uuid": "<DEVICE_UUID>",
      "registration_token": "<FCM_REGISTRATION_TOKEN>",
      "content": {
        "kind": "encrypted",
        "algorithm": "HPKE-v1-BASE-X25519-HKDF-SHA256-AES-256-GCM",
        "key_uuid": "<KEY_UUID>",
        "enc": "<BASE64URL_ENCAPSULATED_KEY>",
        "ciphertext": "<BASE64URL_CIPHERTEXT_AND_TAG>"
      }
    }]
  }
}
```

`organization_uuid` identifies the installation. The managed Workspace manifest
uses its stable backend node UUID. `project_uuid` equals the standard root
`project_id`. All wire UUIDs are canonical lowercase strings. `message_uuid` is
the public placement UUID used by the Messenger API, not the internal canonical
message UUID. `exchange.user_id` remains accepted as legacy input;
`user_uuid` is the canonical output. Extra plaintext fields in mobile event
parameters or the encrypted envelope are rejected. `content_ref` is deferred.

HPKE follows RFC 9180 base mode, DHKEM X25519/HKDF-SHA256, HKDF-SHA256,
AES-256-GCM (`kem_id=0x0020`, `kdf_id=0x0001`, `aead_id=0x0002`). Keys and
ciphertext use unpadded base64url; public/encapsulated keys are raw 32-byte values.
The HPKE `info` is UTF-8 `workspace-mobile-push`. Associated data is the compact
JSON array, in this exact order:

```text
[event_uuid, kind, organization_uuid, project_uuid, stream_uuid, topic_uuid,
 message_uuid, user_uuid, device_uuid, key_uuid]
```

The action, recipient and click route are authenticated. A cancellation is a new
Event UUID and the cancellation EventType, retaining the message route and
recipient and encrypting `{}` separately. Repeated reads reuse its deterministic
UUID. Android decrypts and authenticates cancellations before removing a
notification. Android deduplicates each message within its saved connection,
owner and project. Received unread routes and read/delete/dismiss decisions stay
durable; a tap or failed navigation does not discard an unread route. Only
transient read/delete guards for messages without received pushes expire after
the existing one-hour delivery window. Old read-history pages do not create
permanent receipt rows. No decrypted preview is persisted in receipt records or
logs.

## Backend queue and delivery

Native fanout writes the encrypted snapshot into `messenger_push_outbox` in its
existing database transaction. It uses existing membership, unread, stream mute,
topic override and mention rules, skips the author and historical imports, and
does not push stale messages older than one hour. Read events for messages,
topics and streams, and message deletion, enqueue cancellation for recorded push
delivery routes even after their original ciphertext snapshot has been removed.

The existing primary Messenger worker processes the outbox; projection-only
workers remain independent. Retries reuse the same UUID, ciphertext and initial
one-hour deadline. Network calls have bounded timeouts. A stopped process or a
lost HTTP response can retry safely: it GETs the standard Event UUID, POSTs only
on 404 and handles a concurrent 409 by GET. Notification retains its standard
`NEW`, `IN_PROGRESS`, `ACTIVE`, `ERROR` states. An upstream pending response
keeps the local event pending. Temporary transport errors remain retryable;
permanent errors and expiry terminate the corresponding delivery.

FCM receives a data-only message with one string `data.payload`. The payload
contains the event UUID, action, route, recipient/device UUID and encrypted
content. It contains neither `notification.title/body` nor a registration token.
FCM addressing still uses the target token, and FCM can see timing and the opaque
routing UUIDs. Content is never decrypted in either Notification. Creates use
high priority; cancellations use normal priority. No collapse key combines
create and cancel, because losing either action would change semantics.
The final UTF-8 data size is checked against FCM's 4096-byte limit before sending.

`ACTIVE` means the configured transport accepted the submission. It does not
prove delivery, display, opening or reading on the device.

An explicit FCM `UNREGISTERED` becomes `registration_token_inactive` for the exact
device/token snapshot. Other `INVALID_ARGUMENT` errors do not invalidate tokens.
Each hop caches only a fingerprint of a known-inactive token and filters later
attempts with that exact value. Partial inactive errors propagate while other
devices remain pending. A replacement token is not blocked. Error objects expose
`code`, optional `device_uuid` and `permanent`; raw provider response bodies and
tokens are not logged. Workspace deletes a registration only when the stored
token still matches the rejected snapshot, so a late error cannot delete a
refreshed token. Android can subsequently register its latest token normally.

## Transport preferences

The Notification API supports `GET/POST /v1/bindings/` and owner-scoped
`GET/PUT/DELETE /v1/bindings/{uuid}`. A binding chooses an EventType and Template;
the Template chooses its Provider. Published default templates form the catalog
for authenticated users. Users cannot change another user's bindings or bind a
private template from a different project. Repeating a selection does not
multiply deliveries. Multiple distinct bindings select multiple transports.
Deleting all selections restores the configured defaults.

Workspace preference proxy endpoints and Android preference screens are
deferred. They must later call this same API; they must not bypass its routing.

## Installation and configuration

Mobile Event POST/GET responses omit `event_params.devices`; the durable event
snapshot retains it for rendering and forwarding. Callers poll `uuid`, `status`
and `errors`. Legacy email responses keep their original parameters.

Upgrade the Notification and Workspace code before installing the push site
manifests. Use `exordos build` and `exordos deploy` with immutable build output;
keep database backups and review the generated manifest. Do not patch a running
guest's Python files. Workspace migration 0189 adds only the push outbox.
Notification migration 0001 adds error JSON, inactive-token fingerprints and an
aggregate parent-status view. Existing email and Zulip transports remain usable.

1. Deploy the updated Workspace and Notification elements in the local realm.
   A new push-only Notification can be built with `--manifest-var enable_email=false`
   to omit mailaas, SMTP resources and Core email wiring. Existing email
   installations keep the default `enable_email=true`; do not switch them to
   push-only mode as part of this rollout.
2. Read the Workspace backend node UUID; use it as `organization_uuid` everywhere
   for this source. Keep that identity stable through updates.
3. Install `exordos/manifests/push_source.yaml.j2` on the local Notification's
   Core, passing that organization UUID. It creates one service account with
   `exordos_notification.source_<UUID>.create/read` and the existing
   `event_type.read` catalog permission, required when the service principal has
   no project scope. Repeat on the
   upstream Core for the local relay. Each Core generates a separate password.
4. Install the Workspace repository's `exordos/manifests/mobile_push.yaml.j2` in
   the local realm with the same UUID. It delivers a private IAM credential file,
   enables the existing worker's Notification endpoint, and adds an optional
   gateway route. The base configuration keeps the stable installation UUID.
5. Install this repository's `exordos/manifests/mobile_push.yaml.j2` locally with
   `protocol_kind=notification`, that local UUID, and the upstream HTTPS
   `notification_endpoint`.
6. On Orion, install the same transport manifest with `protocol_kind=firebase`,
   Orion's local Workspace UUID and `firebase_project_id`. Forwarded customer
   UUIDs skip Orion's user binding lookup; Orion's own notifications still use
   local preferences.
7. Provision the private files below through restricted Core config resources,
   mode `0600`, owned by the service user/root. Store durable copies in KeePass.
   Never put secret values in Git, manifest arguments, shell history, Workspace
   messages, screenshots or reports. File changes must restart the associated
   provider/worker so cached credentials are replaced.

Render the site templates to plain YAML before installing them. For example,
from the Notification checkout (all values below are non-secret):

```sh
python3 - <<'PY'
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined
environment = Environment(loader=FileSystemLoader('exordos/manifests'),
                          undefined=StrictUndefined)
variables = dict(organization_uuid='<WORKSPACE_BACKEND_NODE_UUID>',
                 protocol_kind='notification',
                 notification_endpoint='https://<UPSTREAM_HOST>/api/workspace/notification')
Path('rendered').mkdir(exist_ok=True)
for name in ['push_source', 'mobile_push']:
    Path(f'rendered/{name}.yaml').write_text(
        environment.get_template(f'{name}.yaml.j2').render(**variables))
PY
exordos em elements install rendered/push_source.yaml
exordos em elements install rendered/mobile_push.yaml
```

On the upstream, use `protocol_kind='firebase'` and `firebase_project_id` instead
of `notification_endpoint`. Render the Workspace connector from its own checkout
with the same environment and organization UUID. Review the YAML, install one
manifest at a time, and wait for the runtime element and its resources to become
`ACTIVE`; the CLI's installation acknowledgement alone is insufficient. Core
dependency constraints use `X.Y.Z[-suffix]`, for example `1.2.8-dev`.

Provision the sender credential before enabling the Workspace producer. During
staged setup, leave the connector's `notification_endpoint` value empty. Once
the provider is ready, apply the rendered manifest through the normal element
edit/update operation; no registration revision or API version change is needed.

Relay-only installations require this file, containing the **upstream** Core
IAM service credentials generated in step 3:

```text
/etc/exordos_notification/mobile-push-auth.json
```

The JSON shape accepted by the existing SDK is:

```json
{
  "base_url": "https://<UPSTREAM_HOST>/api/core",
  "username": "<SOURCE_SERVICE_USER>",
  "password": "[REDACTED]",
  "scope": "project:12345678-c625-4fee-81d5-f691897b8142"
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

## Android rendering and opening

`FirebaseMessagingService` handles `data.payload` before the legacy bouncer
parser. Unknown encryption profiles, keys, routes or authentication failures are
dropped without logging input. Receipt/rendering needs no Workspace request.
Android notification permission and the messages channel govern whether it can
show an alert. The lock-screen public version contains only a generic preview.

The immutable PendingIntent carries a local receipt UUID. The saved receipt
resolves the original server, project, user, stream, topic and message. It cannot
select an arbitrary host from push data. After opening, the app restores/selects
the saved connection, waits for its verified `/me` owner, checks it against the
recipient, loads the authorized chat and anchors the message. Removed accounts
cannot resolve old notification routes. Ordinary chat loading after the click
still uses authenticated Workspace requests and the application's normal access
checks.

## Verification and acceptance

Automated coverage includes public RFC 9180 vectors; a shared Python-produced
fixture decrypted by Android; per-device rendering; user transport selection;
source authorization and legacy compatibility; real PostgreSQL status/error
aggregation; exact-token invalidation; native message/read transaction hooks;
transient retries; and an instrumented physical-device offline receipt test.
The fixture's private keys are public test material and must never be deployed.

Full acceptance requires an operator-approved managed development environment, native
Workspace with no active Zulip integration, and a real FCM send to the connected
Android device. Verify foreground/background receipt, duplicate suppression,
authentic and out-of-order cancellation, logout, token replacement, and cold/warm
clicks into the correct account/chat/message. Test with the Workspace backend
unreachable during rendering, then restore connectivity for chat loading.
A successful mock or offline handler test does not establish real FCM delivery.
Preserve protected workloads according to the environment's operating instructions.
Record deployed artifact versions, executed checks, unavailable checks, and
review PRs separately. No Google Play publication is part of this workflow.

References: [Firebase HTTP v1](https://firebase.google.com/docs/cloud-messaging/send/v1-api),
[FCM authorization](https://firebase.google.com/docs/cloud-messaging/auth-server),
[service-account key disabling](https://docs.cloud.google.com/iam/docs/keys-disable-enable),
[RFC 9180](https://www.rfc-editor.org/rfc/rfc9180.html),
[WorkManager releases](https://developer.android.com/jetpack/androidx/releases/work).


Relay HTTP request and response bodies are always redacted, including when
INFO/DEBUG logging is enabled or an upstream error echoes its input. The
Notification API also omits request/response bodies from diagnostic logs.
The same redacting, non-redirecting client also handles Core IAM login and
refresh, including token responses. Methods, URLs and status codes remain
available for troubleshooting. Do not
add device tokens, credentials or encrypted event snapshots to log messages.
