# Publication Protocol

One data-product contract describes one consumer-visible dataset with one grain, schema or object
contract, quality policy, writer identity, and version. A business domain may own many such
contracts. A job may write several physical tables, but job success and table commits are not
publication. Iceberg-like formats ordinarily commit one table snapshot at a time; a portable
platform must not assume a cross-table transaction.

## One dataset

1. Resolve the exact immutable inputs, logical interval, contract version, and artifact revision.
2. Write into an isolated or recoverable target. Validate every declared quality gate against the
   candidate output and record the output's immutable version.
3. When the dataset belongs to a consistent publication set, emit an immutable `certified` record
   with its exact output version. `certified` is valid but **not** consumer-visible. For an
   independent dataset, atomically publish the validated version through its consumer-facing
   pointer and emit one immutable `published` record with `published_at` and the same quality proof.
   Warning gates may warn or fail without blocking, but their result must still be recorded.
4. Consumers resolve the published pointer and pin that version rather than query a mutable working
   table directly. A pointer move and its published record form one recoverable operation: the
   adopting platform must prove there is no state where an unrecorded version is visible.
5. For failure, emit `rejected` or `quarantined` with a reason and no certified output reference.
   Preserve staged data only under a non-public recovery identity.

The product contract and publication record are validated together. Product name, contract version,
and writer identity must match; all declared gates must be present, blocking gates must pass, and
the output reference kind must match the product kind. Each gate result carries its declared
versioned control reference, immutable evidence reference, and the exact output version it tested.
The adopting runtime must persist and verify that evidence; this validator cannot execute a
control or prove the evidence object exists. A publication ID is immutable: retrying the same attempt either
returns the same record or fails on conflicting content. A corrected interval uses a new ID and a
new pointer update; it does not overwrite history. Publisher-specific code must enforce uniqueness
and compare-and-swap pointer updates because a standalone YAML validator cannot enforce global
state.

## Consistent cut across datasets

When consumers require several datasets from the same logical interval to be mutually consistent:

1. Give each dataset its own contract and `certified` publication. Do not publish members
   independently.
2. Validate a `publication-set` record containing at least two distinct certified members, their
   exact IDs/contract versions, one logical interval, and a publication time after all members
   finished certification.
3. Atomically expose one immutable set manifest or move one versioned group pointer. The consumer
   resolves every member from that manifest and pins its exact output version for the query or job.
4. If the chosen catalog/serving layer cannot provide a single atomic visibility point and pinned
   member reads, do not claim a consistent cut. Publish datasets independently or choose a
   serving mechanism that can satisfy the requirement.

The set is a visibility protocol, not a claim that the underlying table format rolled back every
member write together. Failed set publication leaves the member outputs certified but invisible;
cleanup or retry must preserve them until the retention/rollback window closes.

## Output references

| Product kind | Certified output reference |
| --- | --- |
| `object_collection` | `object_manifest` |
| `table` or `feature_set` | `table_snapshot` |
| `view` | `view_revision` |
| `stream` | `stream_position` |

Physical locations, pointer implementation, retention, policy grants, and query-engine behavior
belong to the adopting platform. Its integration tests must show that untrusted readers cannot
see staged/certified-only versions, retries cannot regress a pointer, and a multi-dataset consumer
never mixes member versions.
