# Declarative contracts

This directory is the reviewed, non-secret source of truth for stable lakehouse
metadata. Contracts are loaded with strict Pydantic models before any network or
storage operation.

## Ownership

| Contract | Owns |
|---|---|
| `platform.yaml` | Catalog identity and the three lifecycle root namespaces |
| `access.yaml` | Polaris service identities, role graph, and scoped grants |
| `maintenance.yaml` | Tier retention defaults and bounded optimization overrides |
| `sources/*.yaml` | External source boundary, landing schemas, checkpoints, and partitions |
| `curated/*.yaml` | Canonical product schemas, keys, partitions, and upstream sources |
| `processors/*.yaml` | Stable processor input/output semantics and remote resource requirements |
| `domains/*.yaml` | Analytics domain ownership, namespace, and upstream products |

Analytics model schemas, grains, tests, and materializations live only in dbt.
Executable extraction and transformation logic lives only in Python or SQL.
Endpoints, storage roots, and credentials are runtime settings rather than
contract fields.

`access.yaml` never contains a secret. Principal names are also their stable client IDs and
principal-role names, removing three independently configurable identifiers. Local secret values
come from the ignored `.env` and are mounted into application containers through Docker Compose
Secrets. A production deployment supplies the same settings from its secret manager. Credential
rotation is an explicit platform operation and is not part of normal contract reconciliation.
Lifecycle-root namespace grants cover their child namespaces, so adding a curated product does
not require duplicating the same workload privileges.

The validated contract registry derives:

- Catalog and namespace payloads.
- Landing and curated table identifiers and physical locations.
- Iceberg schemas and partition specs with stable field IDs.
- Polaris maintenance policies from tier defaults.

`platform.yaml`, `access.yaml`, and `maintenance.yaml` are the required control-plane
contracts. Entity collections under `sources/`, `curated/`, `processors/`, and `domains/`
may start empty and grow independently; cross-file validation rejects a reference until its
owner contract exists.

Landing table locations follow
`<landing>/<source-type>/<source>/tables/<table-key>`. Curated table locations
follow `<curated>/<product>/<table>`. No contract declares a full physical table
location, catalog UUID, or engine-generated file name.

## Validation

```bash
make validate
```

Validation rejects unknown fields, duplicate YAML keys, secret-like keys,
duplicate ownership, broken cross-contract references, unsafe prefixes, invalid
keys, unstable partition definitions, and maintenance targets that disagree with
managed table partitions.

## Adding a source

1. Add `sources/<source>.yaml` with explicit schema field IDs and checkpoint rules.
2. Implement only the source-specific client and parser.
3. Add `curated/<product>.yaml` and transformation code when canonical data is
   required.
4. Reference the curated product from a domain when dbt marts consume it.
5. Add a bounded optimization override only after file metrics justify one.
6. Run `make validate` and idempotency tests before enabling a schedule.

Adding a source must not require changes to catalog bootstrap, namespace
administration, table-location rules, or generic maintenance behavior.
