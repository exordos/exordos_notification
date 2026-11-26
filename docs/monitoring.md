# Notification monitoring

Install `notification_monitoring` after `exordos_notification` and the shared
`observability` element. Use a Notification version built with the monitoring
element or newer. The matching build includes the collector executable and units.

```bash
exordos em elements install notification_monitoring --version <version>
```

The element starts a timer on the Notification node and installs **Notification
Service** in the shared Grafana's **Notification** folder. The timer takes a
read-only PostgreSQL snapshot about every 30 seconds, probes the local API, and
reads unit state and restart counts. The builder and sender publish counters and
the time of their last successful cycles. The existing node exporter textfile
collector and vmagent forward all of these metrics to VictoriaMetrics.

Removing the element removes its Grafana binding and its activation file on
the Notification node. The collector's next run removes the three metric files
and disables its timer. Worker publication also stops when the activation file
is removed.

## Dashboard signals

| Signal | Interpretation |
| --- | --- |
| API and PostgreSQL | Local HTTP response and a PostgreSQL connection to the primary. Use an external probe for the HTTPS ingress path. |
| Queue collector | Whether the aggregate query succeeded. On failure, queue series disappear. A successfully measured empty queue reports zero. |
| Worker cycle age | Time since the builder or sender completed its last successful database cycle, including an empty queue. |
| Queue depth and age | Preparation and delivery have separate views. Scheduled items are waiting for retry. Items with no retry before their deadline and expired items are shown separately. |
| Rendering | Render attempts, errors and events without a matching template. Retries count as separate attempts. |
| Delivery | Attempts by channel and outcome, including relay polling and inactive push tokens. Accepted means the provider or relay destination accepted the message. Retained queue rows and attempt counters describe different quantities. |
| Delivery p95 | Time from creating a rendered event to its acceptance, including queue time and retries. A synthetic email or push to a dedicated receiver measures end-to-end receipt. |

Only aggregate state, bounded labels and durations are exported. Notifications,
recipient identifiers, delivery error details and credentials stay in the
application.

## Suggested alerts

Use the existing Grafana Alerting contact point for operations, with a route
that works during a Notification outage. Suggested initial rules:

- API or DB down for 3 minutes. Alert on either
  `exordos_notification_api_up == 0` or
  `exordos_notification_database_up == 0`.
- Snapshot stale or missing. Alert when
  `time() - exordos_notification_snapshot_timestamp_seconds > 180` or when
  there have been no snapshot samples for 3 minutes. A separate
  `exordos_notification_queue_collection_up == 0` rule can detect query failures
  while PostgreSQL is reachable.
- Ready work stuck. Alert when
  `max(exordos_notification_queue_overdue_seconds{state="ready"}) > 120`
  for 5 minutes. Scheduled retries are excluded from this rule.

Tune the thresholds and provider error rules after observing normal traffic.
