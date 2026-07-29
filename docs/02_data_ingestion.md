# Source processing

One EMR entry point owns a bounded source window. It:

1. resolves raw prefix, table identifiers, and Spark schemas from the versioned contract bundle;
2. captures immutable source payloads under the source-owned landing prefix;
3. validates and replaces only the bounded landing partition;
4. applies deterministic curated merges using stable business keys;
5. exits non-zero before marking publication complete when the source needs a publication marker.

Airflow retries the complete remote job. Raw object keys and merge conditions make a retry logically
idempotent. Source-specific protocol parsing stays in its job; S3 URI handling, contract loading,
logging, CLI parsing, Spark setup, and table preconditions stay in the shared EMR runtime.
