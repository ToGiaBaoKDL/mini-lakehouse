# Data Incident Recovery

## Triage

1. Declare severity from consumer impact, sensitivity, financial/regulatory risk, and duration.
2. Assign incident commander, data-product owner, platform operator, communications owner, and
   scribe. Preserve logs, manifests, snapshots, query IDs, and deployment evidence.
3. Identify the earliest affected logical interval and last certified output. Distinguish runtime
   failure, freshness failure, correctness failure, unauthorized access, and cost exhaustion.
4. Stop or quarantine publication when continued writes could expand impact. Do not destroy Raw
   evidence or mutate history during diagnosis.

## Recover

Choose one explicit path:

- resume/retry from the same immutable inputs;
- roll back the artifact or configuration;
- restore the last certified snapshot;
- correct logic and run a bounded backfill;
- revoke access and rotate credentials for a security event.

Validate contract, keys, reconciliation, consumer metrics, and SLO before reopening publication.
Communicate corrected intervals and whether historical values changed.

## Learn

Record timeline, root and contributing causes, detection gap, affected products/consumers, recovery
evidence, and corrective owners/dates. Prefer stronger contracts, gates, isolation, or automation
over reminders to be careful. Test the corrective control before closing the incident.

