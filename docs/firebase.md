# Firebase push transport

The `notification_firebase` element adds Firebase Cloud Messaging (FCM) as a
transport for Exordos Notification. Its ownership boundary matches
`notification_email`: the transport element owns and exports one shared
provider, while each producer owns its event types, templates, IAM service
identity, token and sender configuration.

The transport sends data-only FCM HTTP v1 messages. It never creates an FCM
`notification` title or body. Producers can therefore put an encrypted
envelope in the data payload and let the mobile application decrypt it before
display.

## Producer contract

A producer imports `firebase_provider` from `notification_firebase` and
creates its own template:

```yaml
imports:
  firebase_provider:
    element: "$notification_firebase"
    kind: resource
    link: "$exordos_notification.types.email.providers.$firebase"

resources:
  $exordos_notification.types.email.templates:
    mobile_push:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      name: "Workspace mobile push"
      event_type: $exordos_notification.types.email.event_types.$mobile_push:uuid
      provider: $producer.imports.$firebase_provider:uuid
      is_default: true
      params: {}
      content:
        kind: firebase_android
        device_uuid: "{{ device_uuid }}"
        registration_token: "{{ registration_token }}"
        data:
          payload: "{{ encrypted_payload }}"
        priority: "{{ priority | default('normal') }}"
```

The Android-specific content kind keeps its platform priority explicit without
requiring Android fields from future iOS content. The rendered `device_uuid`
identifies the registration that the owner must update after a permanent
delivery result. The rendered `registration_token` must be
non-empty. FCM data keys and values must be strings, and their compact JSON
representation must not exceed 4096 bytes. Android priority is `normal` or
`high`. The provider applies a one-hour FCM TTL and a ten-second HTTP timeout.

The producer remains responsible for validating its event payload. In
particular, the transport does not define Workspace event UUIDs, HPKE fields,
device ownership rules or sender permissions. Those belong beside the producer
that understands them.

## Credentials

The provider reads exactly one service-account file:

```text
/etc/exordos_notification/firebase-service-account.json
```

The path is fixed rather than accepted from provider data, so a provider writer
cannot make the Notification service read an arbitrary local file. The Firebase
project ID is read from the same credential and is not duplicated in the
manifest.

The credential is delivered separately from the Firebase provider. Install
`notification_firebase_secret` first, then set
`notification-firebase-service-account` in Core Secrets to the complete
service-account JSON. Use the installation's secure operator workflow to set
the value without placing it in a manifest, command-line argument, or log.

After the secret is populated, install `notification_firebase_credentials`.
It renders the secret into the file on the Notification node with ownership
`root:root` and mode `0600`. A change restarts
`exordos-notification-mail-agent.service`, which caches the authorized Google
session. Restrict read access to the Core Secret and the generated Core config
to the operators who manage the credential.

When migrating an installation, seed the Core Secret with the current
service-account JSON. If an unmanaged Core config already targets the same
path and node, reuse its UUID. Add it as `uuid` under
`resources.$core.config.configs.service_account` in a local copy of the
rendered credentials manifest and install that copy. Core enforces one
config per path and target. For a config already owned by an element,
add the secret import to that element and update its existing config
resource to reference the secret.

Verify that Core reports the config as active, the file matches the original
and the delivery worker is running. Verify the match without exposing either
credential value. For key rotation, update the Core Secret and check that
the file and worker have picked up the change.

Creating the Google service account, enabling FCM HTTP v1 and granting its
Firebase messaging role are separate installation actions. This repository does
not create or modify Google or Firebase settings.

## Delivery result

A successful Notification event means that FCM accepted the request. It does
not prove delivery to the device, local decryption, display or opening by the
user. FCM `UNREGISTERED` is a permanent
`registration_token_inactive` result carrying the rendered `device_uuid`.
The producer must deactivate that device only when its stored token still
matches the event snapshot, so an old result cannot disable a rotated token.
Notification also remembers a hash of the rejected token and fails later
events for it without another FCM request; it never persists the token in the
failure cache.

Transient failures start with a five-second retry delay and use exponential
backoff capped at 60 seconds. A valid provider `Retry-After` value takes
precedence. HTTP 408, 429, 500, 502, 503 and 504 responses are transient;
other provider HTTP failures are permanent. Credential loading and session
setup failures produce permanent `firebase_credentials_error` results;
authentication refresh failures produce `firebase_auth_error` results and
preserve Google's retryable classification, while network failures produce
transient `firebase_transport_error` results. Permanent failures are scheduled
365 days after the event cleanup
deadline, providing a safety margin if cleanup is delayed.
`status_description` retains the machine-readable code, while the structured
`errors` list carries the device UUID and permanence flag. Provider response
bodies, tokens and payloads are never copied into either result.

## Upgrade from the retired push format

The database migration removes templates with kind `mobile_push`, their
bindings, and delivery snapshots with kind `rendered_mobile_push`.
Notification drops outstanding deliveries in the retired format. Existing
`firebase_android` templates and events remain available.
