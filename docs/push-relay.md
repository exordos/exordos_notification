# Forward Workspace push through another Notification

The local Notification accepts a Workspace event with one encrypted Android
payload. A template with content kind `notification_relay` uses the
`notification_relay` provider to forward the payload to a receiving Notification.
The relay submits the same event UUID and preserves the device UUID and payload.
A pending receiving event keeps the local event pending. The relay reports a
permanent `registration_token_inactive` only for the original device.

## Receiving installation

Install `notification_firebase` and configure its sending credential as
described in [the Firebase guide](firebase.md). Create a separate event type,
default Firebase Android template, and IAM service identity for each forwarding
installation. Give the identity only create and read permissions for its event
type. The Firebase template renders `device_uuid`, `registration_token`,
`payload`, and `priority` from the event parameters. Use that event type's UUID
and project UUID in the forwarding installation.

## Forwarding installation

Install `workspace_mesh_firebase_push` with a local Notification that has the
relay provider. Set the Core Secret `workspace-mesh-upstream` to a JSON object
with these fields.

| Field | Value |
| --- | --- |
| `notification_endpoint` | Receiving Notification HTTPS base URL ending in `/api/notification`. |
| `iam_endpoint` | Receiving Core HTTPS base URL ending in `/api/core`. |
| `event_type_uuid` | Dedicated event type UUID on the receiver. |
| `project_uuid` | Project UUID of the receiving event type. |
| `access_token` | Scoped receiving IAM access token. May be omitted when a refresh token is supplied. |
| `refresh_token` | Optional receiving IAM refresh token. |

Both endpoints must have certificates trusted by the local delivery worker.
Use a distinct receiving installation. Core installs the JSON only on the
forwarding Notification node and restarts the delivery worker upon changes.
The worker refreshes IAM credentials in memory when a refresh token is
available. Replace the configured token bundle before its refresh lifetime
expires or when rotating credentials. The receiver holds the Firebase sending
credential.

Check an ordinary Workspace message on Android and verify the receiving event
using the same UUID. When testing an invalidated test device, verify the
terminal result reaches the forwarding Notification and Workspace removes
the same registration.
