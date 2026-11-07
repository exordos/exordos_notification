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
        kind: firebase
        registration_token: "{{ registration_token }}"
        data:
          payload: "{{ encrypted_payload }}"
        android_priority: "{{ priority | default('normal') }}"
```

The rendered `registration_token` must be non-empty. FCM data keys and values
must be strings, and their compact JSON representation must not exceed 4096
bytes. Android priority is `normal` or `high`. The provider applies a
one-hour FCM TTL and a ten-second HTTP timeout.

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

Provision the file through a private Core config resource with mode `0600`,
targeting the Notification node. Do not commit the credential or pass it in
command-line arguments. Restart `exordos-notification-mail-agent` after the
file is installed or rotated; the process caches the authorized Google session.

Creating the Google service account, enabling FCM HTTP v1 and granting its
Firebase messaging role are separate installation actions. This repository does
not create or modify Google or Firebase settings.

## Delivery result

A successful Notification event means that FCM accepted the request. It does
not prove delivery to the device, local decryption, display or opening by the
user. FCM `UNREGISTERED` is reported as
`firebase_registration_token_inactive`; other failures expose only a
sanitized HTTP status code and never the provider response body, token or
payload.
