# Data Sharing

Use governed sharing for cross-domain, cross-account, partner, or customer access. Prefer access to
a versioned product over unmanaged extracts and emailed files.

## Contract

- Share a named Gold product or purpose-built projection with owner, consumer, purpose, fields,
  classification, region, retention, and expiry.
- Minimize columns and rows; apply masking, tokenization, row policy, or aggregation before access.
- Separate provider and consumer identities and audit both authorization and use.
- Define freshness, availability, schema compatibility, support, revocation, and egress/cost
  responsibility.
- Cross-region or cross-cloud copies declare residency, replication lag, deletion, and recovery.
- Exports include manifest, checksum, contract version, logical interval, and expiry.

## Required proof

- policy-as-code review and negative access tests;
- consumer acceptance against a non-production or synthetic product;
- schema-change and revocation test;
- usage, egress, and stale-share monitoring;
- deletion/expiry evidence and incident contact path.

Do not expose Bronze, internal workspaces, physical storage paths, or shared credentials as a data
sharing interface.

