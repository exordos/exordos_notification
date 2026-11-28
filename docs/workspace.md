# Send Notification events to Workspace

The `workspace` provider delivers Markdown to a Workspace topic or a
one-to-one chat. It uses the existing Notification event API, template
selection, queue and delivery status.

## Configure the sender

Create a dedicated IAM sender with an access token scoped to the destination
Workspace project. Give the sender permission to post in the destination
channels, including any required stream membership. For direct messages, the
recipient must be a user in that Workspace project. Workspace uses the token's
project and sender identity for both operations.

Create a Notification Provider with `protocol: {kind: workspace}`. On the
Notification sender node, deliver a root-owned, mode-`0600` file to
`/etc/exordos_notification/workspace/<notification-project-id>/<provider-uuid>.json`.
Use the provider's Notification project ID for the directory, or `global`
for a provider with a null project ID. The file has this shape.

```json
{
  "endpoint": "https://workspace.example.com",
  "access_token": "<project-scoped IAM access token>"
}
```

Use an HTTPS origin with a certificate trusted by the sender node. Keep
the credential in the deployment's secret store and deliver the file through
managed configuration. For a renewable Core IAM token, use
`$core.iam.tokens` with `auto_renew: true` and render its `:access_token`
into the file with `$core.config.configs`. The worker reads the file at every
delivery attempt and picks up replacements without a restart. Keep the
Workspace origin, project, and sender identity stable when rotating a token.

## Create a template and submit an event

Use the same EventType, Provider and Template resources as for Zulip.
For Core manifests, the existing resource family is
`$exordos_notification.types.email`. Set `is_default: true` to use a
template by default, or select it via a binding as with other transports.

For a message in an existing topic, set the template's `content` field as
shown below. Use fixed stream/topic UUIDs in a template to pin its route, or
pass `stream_uuid` and `topic_uuid` in `event_params` to route each event.

```yaml
content:
  kind: workspace_stream_message
  stream_uuid: "00000000-0000-4000-8000-000000000001"
  topic_uuid: "00000000-0000-4000-8000-000000000002"
  content: "**Deployment finished**\n{{ message }}"
```

For a direct message, pass the Workspace recipient UUID as
`event_params.user_uuid`. Workspace resolves or creates the native
one-to-one chat and the worker posts to its default topic. Each direct
template addresses one user and sends plain Markdown.

```yaml
content:
  kind: workspace_direct_message
  user_uuid: "{{ user_uuid }}"
  content: "{{ message }}"
```

The defaults are `{{ message }}` for the Markdown and the matching UUID
parameters for the route. Markdown supports 1 to 40,000 characters.
Submit the event through the normal authenticated Notification `POST
/v1/events/` endpoint with the selected EventType and its `event_params`.

## Delivery status

`ACTIVE` means Workspace accepted the message in the requested topic.
Each event and template pair gets a stable Workspace message UUID. Delivery
retries reuse that UUID. Timeouts, rate limits, expired credentials and
server errors use the Notification retry schedule. Permanent Workspace
rejections such as `403` stop retries and appear in the event's `errors`.
