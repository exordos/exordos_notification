# Encrypted Workspace mobile pushes

This extension uses the existing v1 Event → Template → Provider pipeline.
There are no registration revisions, content callbacks, v2 event endpoint, or
additional public delivery states. Android is the first supported client.

```text
Android -- public key + FCM token --> Workspace push_devices
Workspace counter transaction -- SDK Event --> existing SDK sender
Workspace push handler --> local Notification -- firebase protocol --> FCM -- data payload --> Android
Android -- decrypt locally --> system notification -- click --> saved account and chat
```

The server/FCM delivery lifetime is at most one hour. Android keeps received
unread notifications and their authenticated routes without an age deadline.
It stores only routing and receipt decisions on disk; restored cards use a generic
preview because decrypted text stays in process memory. Workspace clears terminal
ciphertext/device snapshots and removes old terminal delivery results. Each changed topic snapshot is independent of earlier delivery rows; a zero
active-unread snapshot removes the card even after old transport rows were pruned.

Notification chooses the user's bound templates, falling back to published defaults.
The Firebase template has `content.kind=mobile_push` and expands the encrypted
snapshot for each device without reading the message or looking up an IAM profile.
Only the central sender holds Firebase credentials. Set
`push.local_organization_uuid` to its Workspace installation UUID. An authorized
foreign source uses the configured defaults rather than this installation's user
bindings. The optional forwarding transport is described in the [relay guide](push-relay.md).

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

User-exchange API responses keep the original v1 `user_id` field alongside the
canonical `user_uuid`; new event producers use `user_uuid`.

## Events and encryption

The producer emits `notification_topic_update`, EventType
`aba6e448-3b72-51ef-b0c6-d2e3be17efe7`. Existing
`notification_message_create` (`120f3970-14e5-5997-bdc2-daca77fbf894`) and
`notification_message_cancel` (`0abcf177-2b7f-5af9-a759-84fafdf9b271`) remain
accepted for already queued legacy deliveries; they are no longer produced by
the topic-counter flow.

One standard v1 Event targets one recipient **and one device**:

```json
{
  "uuid": "<EVENT_UUID>",
  "project_id": "<PROJECT_UUID>",
  "event_type": "/v1/event_types/aba6e448-3b72-51ef-b0c6-d2e3be17efe7",
  "exchange": {"kind": "User", "user_uuid": "<RECIPIENT_UUID>"},
  "event_params": {
    "kind": "notification_topic_update",
    "organization_uuid": "<INSTALLATION_UUID>",
    "project_uuid": "<PROJECT_UUID>",
    "stream_uuid": "<STREAM_UUID>",
    "topic_uuid": "<TOPIC_UUID>",
    "message_uuid": "<FIRST_UNREAD_PUBLIC_MESSAGE_UUID>",
    "priority": "high",
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

The plaintext exists only in trusted Workspace and after decryption on Android:

```json
{
  "kind": "topic",
  "stream_name": "Development",
  "topic_name": "Mobile pushes",
  "sender_name": "Alice",
  "text": "First unread message",
  "created_at": "2026-01-01T12:00:00+00:00",
  "unread_count": 3,
  "active_unread_count": 3,
  "passive_unread_count": 0,
  "epoch_version": 100,
  "epoch_generation": "<EXISTING_WEBSOCKET_GENERATION_UUID>"
}
```

The message route and text use the **first visible unread** placement in canonical
history order. After all messages are read they use the last visible message. If
the last message was deleted, the zero snapshot keeps its known UUID solely for
removal. Text is limited to 768 UTF-8 bytes, sender to 128, stream/topic names to
256 each, cutting at character boundaries. No image/resolver/content_ref request
is needed. Existing end-to-end encrypted chat payloads remain opaque and render
a generic encrypted-message preview; push encryption does not change ordinary
Workspace message storage into end-to-end encrypted chat.

`organization_uuid` is the persistent transport element UUID exported by the
managed manifest, and `project_uuid` equals root `project_id`. Route UUIDs are
canonical lowercase strings; `message_uuid` is the public placement UUID used by
the Messenger API. `exchange.user_id` remains accepted as legacy input. Extra
plaintext mobile fields are rejected. No registration revisions are introduced.

HPKE uses RFC 9180 base mode, DHKEM X25519/HKDF-SHA256, HKDF-SHA256 and AES-256-GCM
(`kem_id=0x0020`, `kdf_id=0x0001`, `aead_id=0x0002`), unpadded base64url and raw
32-byte public/encapsulated keys. `info` is UTF-8 `workspace-mobile-push`. AAD is
the compact JSON array in this exact order:

```text
[event_uuid, kind, organization_uuid, project_uuid, stream_uuid, topic_uuid,
 message_uuid, user_uuid, device_uuid, key_uuid]
```

## Backend queue and delivery

The existing counter UPDATE captures the previous state and keeps its current
COUNT queries and row locks. If the topic's three counters change, the transaction
saves a minimal `WorkspaceTopicSignal` through `AsyncEventClient`. The signal
contains identifiers, the source event UUID and the previous active count. No
device lookup, plaintext preview, encryption or HTTP runs in the recount path.

The independently managed `workspace-push-handler` loads current access,
notification settings, counts, first-unread preview and devices. It waits while a
recount or topic publication is pending, and selects the latest published
recipient/topic epoch whose counters match the current snapshot. The original
signal supplies the membership fence rather than an obsolete outgoing epoch.
After encryption outside the read transaction, the handler atomically queues
ready per-device SDK Events and completes the signal. Ready payloads contain only
ciphertext and addressing metadata; SDK signal timestamps set their deadline.

The independent `workspace-push-sender` subclasses the existing SDK
`EventSenderService`, selects only ready deliveries and inherits send/retry/status
polling. HTTP holds no recount/schema/device lock. A stale membership or replaced
token/key is rejected before handoff. There is no second queue, `save_once`, custom
sender loop or lookup of previous delivery rows in the counter transaction.

Muted chats produce no events. Mentions-only chats update when their active count
changes, including zero after the last mention is read; passive-only changes do
not create alerts. Topic overrides keep their existing meaning. History import
and protocol-only messages do not create alerts. No per-message create/cancel
notifications accompany this counter flow.

Notification applies ordinary user bindings/default templates locally. Firebase
renders opaque device payloads and never enriches or decrypts content. The Android card is keyed by saved connection,
owner, project, stream and topic. New absolute state replaces the count, preview
and click target; zero active count removes it. Existing WebSocket epoch/generation
reject duplicate and delayed state without a separate revision scheme.

FCM receives one string `data.payload`, with authenticated route/recipient/device
IDs and encrypted content. It receives no `notification.title/body`, sender name,
preview text or registration token inside data. The token still addresses the FCM
request; timing and opaque route IDs remain visible to the transport. Priority is
`high` for active-unread growth and `normal` for decrease/zero. Normal priority may
be delayed by Doze. No collapse key is required for correctness; Android compares
absolute snapshots. Final data size must fit FCM's 4096-byte limit.

The SDK retains its one-hour delivery deadline and standard
`NEW`/`IN_PROGRESS`/`ACTIVE`/`ERROR` states. `ACTIVE` proves transport acceptance,
not device delivery, display, opening or reading. Retryable failures stay pending;
permanent errors/expiry terminate the event. A repeated POST conflict is accepted
only after reading the same UUID with the source credentials.

FCM `UNREGISTERED` maps to `registration_token_inactive` for the exact device/token.
Other `INVALID_ARGUMENT` errors do not retire a token. Every hop filters that
known-inactive token fingerprint and propagates a sanitized error while other
deliveries remain pending. A replacement token is unaffected. Workspace removes
only the registration whose token and key still match the rejected snapshot.
No raw provider response body, token or decrypted text is logged.

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

Deploy the email/base change first; email authentication and rollout are described
in [the email guide](email.md). This release adds the Firebase protocol, encrypted
mobile event models, per-device rendering and migration 0001 (delivery errors,
inactive-token fingerprints and parent-status aggregation). The forwarding protocol is configured separately; see the
[relay guide](push-relay.md). Install `notification_email` only if SMTP is needed.

Build and deploy immutable Notification and Workspace images through the standard
platform tooling, preserving database/config backups. Do not patch installed
Python files. Deploy Android before enabling the Workspace producer. Workspace
migrations follow the current backend master; use that release's migration chain.

1. Render `notification_firebase.yaml.j2` with the non-secret `firebase_project_id`.
   Install it and wait for `ACTIVE`. The persistent transport element exports its
   own `organization_uuid` and configures `push.local_organization_uuid`.
2. Render `push_source.yaml.j2` with `notification_transport=notification_firebase`.
   It imports that identity and provisions source-scoped create/read permissions.
3. Install Workspace's `mobile_push.yaml.j2` with the same transport name. It
   imports the source account, provisions an automatically renewed Core IAM token
   and configures the independent preparation and SDK sender services.
4. Deliver `/etc/exordos_notification/firebase-service-account.json` through a
   private mode-0600 Core config resource. Only the central sender receives this
   Google credential. Enable FCM HTTP v1 and grant messaging access in the matching
   Firebase project. Android's `google-services.json` cannot authorize sending.
   Keep durable credentials in KeePass, never in Git, arguments or test records.
5. Restart the provider after replacing its cached Google credential. The
   Workspace sender's managed token file is reloaded per request without a
   restart. Keep the producer disabled until the provider and device are ready.

For example, from this checkout (all arguments are non-secret):

```sh
python3 - <<'PY_RENDER'
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined
environment = Environment(loader=FileSystemLoader('exordos/manifests'),
                          undefined=StrictUndefined)
variables = dict(firebase_project_id='<FIREBASE_PROJECT_ID>',
                 notification_transport='notification_firebase')
Path('rendered').mkdir(exist_ok=True)
for name in ['notification_firebase', 'push_source']:
    Path(f'rendered/{name}.yaml').write_text(
        environment.get_template(f'{name}.yaml.j2').render(**variables))
PY_RENDER
exordos em elements install rendered/notification_firebase.yaml
exordos em elements install rendered/push_source.yaml
```

Mobile API responses omit `event_params.devices`; the stored snapshot retains the
addresses for delivery. Poll the normal Event UUID/status/errors. No new v2 API,
content callback or registration revision is needed. A permanent inactive-token
error is cached as a fingerprint and returned to the producer; a replacement
token is independent of the old rejection.

For older combined email/transport installations, retain the installed resources
until their ownership/import migration has been tested. See the migration gate
in the [installation guide](../README.md#existing-combined-element-installations).

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
aggregation; exact-token invalidation; changed topic-counter transaction hooks;
transient retries; and an instrumented physical-device offline receipt test.
The fixture's private keys are public test material and must never be deployed.

Full acceptance requires an operator-approved managed development environment, native
Workspace with no active Zulip integration, and a real FCM send to the connected
Android device. Verify foreground/background receipt, duplicate suppression,
first-unread/partial-read/zero updates, stale snapshots, mute/mention rules,
logout, token replacement, and cold/warm
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


The Notification API omits request/response bodies from diagnostic logs. Do not
add device tokens, credentials or encrypted snapshots to log messages.

A topic update that closes an empty topic after its last message was deleted has
`message_uuid: null`. Its authenticated route still includes the topic; Android
removes that topic's card without creating a click target. A positive active
unread count requires a non-null message UUID. Existing message create/cancel
payloads still require a canonical message UUID.
