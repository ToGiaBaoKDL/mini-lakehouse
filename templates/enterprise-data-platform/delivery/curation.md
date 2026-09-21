# Landing-to-Curated Curation Standard

This is a required boundary for a published Silver product, not a fixed sequence of five
transformations. Apply only the operations justified by its source semantics and contract. Bronze
preserves source meaning and replay lineage; Silver publishes stable domain meaning. Gold owns
consumer-specific metrics and presentation. A small pipeline may combine compute steps, but it must
still prove these distinct boundaries.

## 1. Protect data before publication

- Classify source and output fields, including identifiers hidden inside JSON and free text. Record
  the output representation and an approved policy reference in the product contract. Never put
  secrets, salts, keys, or reversible token maps in the contract or logs.
- Minimize sensitive fields. When a stable join is needed, use a governed keyed pseudonym or token
  with explicit scope, key custody, rotation, and reprocessing semantics. A plain hash or a shared
  static salt does not make low-entropy identifiers anonymous. Reversible use requires a separately
  controlled tokenization or encryption service.
- Distinguish stored-data protection from display masking. A masked card number is not a substitute
  for protecting an original PAN retained elsewhere. Choose the visible prefix and suffix under the
  applicable card-program policy and business need; never hard-code a six-digit BIN or six `X`
  characters for every PAN length.
- Keep policy intent portable. In AWS, a separately owned Lake Formation/IAM mapping may implement
  table or column grants and LF-tags; the curation job does not grant access merely by writing a
  tag. Test both allowed and denied query paths and direct object-storage access.

## 2. Clean, order, and reconcile

- Normalize missing values, types, units, encodings, and invalid records explicitly. Quarantine
  records that cannot be safely interpreted with the original payload, reason, and lineage.
- Deduplicate using the source's stable key **and** authoritative ordering tuple: transaction or
  log position, source version, event time, capture position, then a deterministic tie-breaker as
  applicable. `modified_at` alone is not proof of total order. Define equal-version conflicts.
- For CDC, apply deletes/tombstones and late updates according to the declared state model. Keep
  source position, operation, source event time, capture time, and schema version in immutable Raw or
  Bronze evidence even when they are omitted from a consumer-facing Silver projection.
- Reconcile source and output over a declared logical interval, including rejected, deleted, and
  unchanged records. A successful write is not a quality certificate.

## 3. Normalize structure and time

- Parse nested JSON/BSON with a versioned schema and validate required paths. Flatten only stable,
  useful fields; keep repeated entities in child tables with deterministic parent keys and array
  positions when that better preserves grain. Do not silently discard unknown fields.
- Store instants in UTC and declare the source timezone and conversion rule. Keep local business
  date or session as a separate semantic field where needed. Choose decimal precision and scale
  from the source range and business unit, with overflow and rounding tests.

## 4. Derive business attributes deliberately

- Put stable canonical attributes in Silver and consumer-specific metrics in Gold. Derived fields
  declare source fields, formula/version, null behavior, and point-in-time availability.
- External lookups such as phone carrier, geography, or merchant category need an effective-dated
  reference dataset and a refresh owner; prefixes alone can become stale or wrong.
- Partition by measured pruning, file size, write amplification, and maintenance cost. A physical
  `part_date` string is not mandatory, particularly for table formats with partition transforms.

## 5. Publish a governed table

- Use the declared table format and catalog with one writer identity per dataset. A table-format
  commit normally covers one table; it does not automatically make several datasets visible as one
  cut. Follow `publication.md` for independent publications or a consistent publication set.
- Capture input manifests or snapshots, code artifact, output snapshot, row/control totals, quality
  outcomes, and logical interval in the publication record. Consumers read only versions exposed by
  a published product or publication-set pointer.
- Set snapshot retention and compaction from replay, audit, rollback, and cost requirements. Time
  travel lasts only while the needed snapshots and files are retained; schema evolution remains a
  reviewed compatibility change, not permission for writers to silently alter tables.

## Required proof

- Fixtures cover duplicate, out-of-order, equal-version conflict, delete, late arrival, malformed
  nested data, timezone boundary, decimal overflow, and rerun/backfill behavior.
- Sensitive values do not leak into the Silver projection, logs, query results, or unauthorized
  reader paths; any token remains stable only within its declared scope and rotation version.
- Source/output counts and control totals reconcile with documented exclusions; blocking quality
  results and exact output snapshot are recorded before consumer-visible publication.
- A representative workload meets the freshness and cost objectives, and failure/replay tests show
  how to recover from a partial multi-table write.
