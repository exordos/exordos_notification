# APNs transport for Workspace

Install `notification_apns` with an APNs-capable Exordos Notification build.
The transport owns the provider for `com.exordos.workspace`. A producer
provides the event type, template and sender permissions.

Set the Core Secret `notification-apns-credentials` to a JSON object with
`sandbox` and/or `production` members. Each configured member has
`team_id`, `key_id` and `private_key` containing the original PEM from
the Apple APNs key. Use a key authorized for the topic and environment.
Core writes this secret only on the Notification node, with mode `0600`,
and restarts the delivery worker when it changes. Keep secret and config
read permissions restricted to operators.

The APNs template consumes the producer's encrypted JSON envelope,
destination device UUID and token, environment, collapse ID and priority.
The worker uses HTTP/2 and ES256 provider authentication. Alert pushes use a
localized generic title and carry the encrypted envelope separately.
Background updates use Apple's low priority delivery and a separate collapse
ID so they cannot replace a pending visible alert.

APNs acceptance reports transport success. A 410 response indicating
`Unregistered` or `ExpiredToken` returns `registration_token_inactive`
for the target device. The producer decides whether that registration is
still current. Authentication and environment errors leave the registration
intact.
