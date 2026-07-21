# Declarative contracts and source-scalable lakehouse refactor

## Metadata

- Status: `complete_with_backfill_range_verification_deferred`
- Created: `2026-07-21`
- Change type: `refactor`
- Primary scope: contracts, Polaris, Iceberg, dbt, Prefect, storage layout và deployment configuration
- Catalog: `prod`
- Runtime target hiện tại: local Docker Compose, MinIO, Polaris root credentials và Prefect local process worker
- Related components: `src/mini_lakehouse`, `dbt_project`, `orchestration`, `infra`, `compose.*.yaml`, `.env*`

Implementation status:

- Contract models đã tách theo catalog/source/domain/policy ownership; platform bootstrap chỉ còn
  là composition root của catalog, RBAC, namespace và policy reconcilers độc lập.
- Source ownership, dbt structure, Prefect structure, Compose modules, uv packaging và
  documentation đã được triển khai.
- Unit/contract/static/Compose verification pass. Live bootstrap double-apply, Polaris/MinIO/Trino
  integration, dbt full build rồi immediate rebuild trên landing table rỗng đã pass.
- Live GitHub Archive recovery và duplicate-ingest đã pass với identity-partition table; bounded
  backfill range isolation vẫn là data-bearing verification riêng.
- Inventory data cũ được waive vì owner xác nhận có thể tạo lại toàn bộ; refactor không mang compatibility branch cho layout/data cũ.

Deferred verification không chặn architecture refactor:

- Chạy bounded multi-hour backfill range isolation khi cần thêm data-bearing proof; unit tests đã
  khóa conditional create, metadata-only checkpoint và partition overwrite.
- Destructive detach/delete plan được giữ ngoài safe bootstrap. Polaris hiện không cung cấp direct
  mapping inventory đầy đủ để suy diễn detach; quy trình reviewed migration nằm trong operations
  runbook. Không thêm delete heuristic để chỉ “đủ checklist”.

Verification evidence tại completion:

- `78 passed` (sau orchestration/notification refactor), integration catalog readiness pass,
  Ruff và Pyright clean.
- Contract registry: catalog `prod`, 1 catalog-role grant, 8 namespace, 1 source, 1 domain,
  4 policy; deterministic load pass.
- Ba Compose profile render được; runtime/orchestration/dashboard image build bằng frozen lock;
  long-lived services healthy/running.
- Polaris bootstrap lần sau: catalog/RBAC/namespaces match, 4 policy body unchanged.
- Hai dbt build liên tiếp: `PASS=32`, `ERROR=0`, `NO-OP=1`, `TOTAL=33`.
- Governance flow: 7 table discovered/completed, 28 maintenance statement.
- Prefect có 2 deployment sau khi backfill được gộp thành custom parameters của ingestion flow;
  dashboard discover cùng 7 table từ catalog.
- Live recovery: 164,186 rows giờ 04 và 169,288 rows giờ 05; rerun giờ 04 trả
  `was_written=false` mà không download/parse lại.
- Scheduled flow ingest giờ 07, source freshness pass, dbt `PASS=32`, success hook pass; custom
  parameter run backfill giờ 06 hoàn tất và đóng gap 04–07 với 666,937 raw rows.

## Mục tiêu

Refactor codebase theo hướng declarative, modular và có ownership rõ ràng để:

- YAML là source of truth cho desired state và metadata ổn định.
- Pydantic chịu trách nhiệm parse, validate và normalize contract trước khi có side effect.
- Python chỉ giữ runtime behavior, integration logic và reconciliation.
- SQL/dbt giữ business transformation, tests, freshness và model contracts.
- Secrets và endpoint theo môi trường chỉ đi qua environment variables.
- Bổ sung source mới mà không phải sửa xuyên suốt storage, platform, orchestration và dbt.
- Polaris policy được apply đầy đủ, idempotent, safe-by-default và không âm thầm bỏ qua field;
  destructive detach/delete dùng reviewed migration thay vì heuristic.
- Không làm thay đổi dữ liệu vật lý ngoài ý muốn trong quá trình refactor cấu hình.

## Non-goals

- [x] Không triển khai GCS dependency trong giai đoạn này.
- [x] Không thêm ClickHouse hoặc giữ compatibility logic cho ClickHouse.
- [x] Không chuyển dbt model sang incremental lúc này; model vẫn materialize thành `table`.
- [x] Không xây một ingestion framework tổng quát quá sớm cho mọi loại source.
- [x] Không đổi catalog khỏi `prod`.
- [x] Không đổi khỏi ba bucket `landing`, `curated`, `analytics`.
- [x] Không chuyển Prefect sang remote worker/Kubernetes trong scope này.
- [x] Không thay Polaris root credentials cho local; production credential model sẽ là plan riêng.
- [x] Không di chuyển hoặc rewrite toàn bộ table data chỉ để đổi cấu trúc code/config.

## Quyết định đã khóa

- [x] Tên plan dùng convention `YYYY-MM-DD_<change-type>_<scope>.md`.
- [x] Catalog duy nhất hiện tại là `prod`.
- [x] Object storage có đúng ba bucket theo data lifecycle: `landing`, `curated`, `analytics`.
- [x] Landing giữ transport/source boundary; curated bỏ transport detail; analytics tổ chức theo business domain.
- [x] dbt source đọc từ landing, không đọc từ curated. Staging làm sạch source trước khi intermediate/mart sử dụng.
- [x] dbt model hiện tại materialize thành `table`; logic watermark/incremental có thể giữ lại nhưng không được kích hoạt.
- [x] Mọi deployable DAG nằm trong `orchestration/flows/` và theo convention; reusable mechanics
  tách `utils/`, `plugins/`, không có catch-all `tasks.py`.
- [x] Tên DAG tuân theo `[job_type]_[description].py`, với `etl`, `el`, `tl`, `rpt`, `mon`, `bk`, `gov`, `test`.
- [x] Thư mục orchestration không phải Python package nếu không có lý do import thực tế; không cần `__init__.py`.
- [x] Image service tiếp tục dùng tag `latest` theo yêu cầu hiện tại.
- [x] Compose được tách modular và đặt tên nhất quán theo capability.
- [x] YAML chỉ chứa non-secret desired state; secrets không được commit.
- [x] Schema vật lý của Iceberg tiếp tục code-owned trong source package ở phase đầu để bảo vệ field ID và type evolution.
- [x] Refactor config không được tự động drop table, namespace, policy hoặc object.

## Invariant không được phá vỡ

- [x] Mọi Iceberg table identifier resolve về cùng catalog `prod`.
- [x] Namespace nhiều cấp phải được quote đúng khi query qua Trino, ví dụ `"prod"."landing.api.github_archive"."events_raw"`; không suy diễn thành nhiều SQL schema ngoài semantics của Trino connector.
- [x] Object path phản ánh lifecycle, source/domain và table; mỗi table có prefix riêng.
- [x] Landing object gốc là immutable; conditional create không overwrite object đã tồn tại.
- [x] Mỗi source partition có natural idempotency key; GitHub Archive dùng `source_hour` và dynamic partition overwrite để retry/concurrent commit không tạo duplicate.
- [x] Iceberg field ID đã phát hành không được thay đổi hoặc tái sử dụng cho field khác.
- [x] Curated namespace không chứa `api`, `rdbms`, `stream` hoặc transport concern tương tự.
- [x] Analytics namespace và dbt mart đều có domain owner rõ ràng.
- [x] Không có secret, access key, root credential hoặc token trong YAML contract.
- [x] Reconciler mặc định chỉ create/update an toàn; delete/detach phải xuất hiện trong plan và cần mode áp dụng rõ ràng.
- [x] Một cấu hình không được có hai source of truth đồng thời sau khi phase migration hoàn tất.

## Target architecture

```text
contracts/
├── catalog.yaml
├── domains/
│   └── engineering.yaml
├── policies/
│   ├── data_compaction.yaml
│   ├── metadata_compaction.yaml
│   ├── snapshot_expiry.yaml
│   └── orphan_file_removal.yaml
└── sources/
    └── github_archive.yaml

src/mini_lakehouse/
├── config/
│   └── settings.py
├── contracts/
│   ├── base.py
│   ├── catalog.py
│   ├── sources.py
│   ├── domains.py
│   ├── policies.py
│   ├── registry.py
│   ├── identifiers.py
│   ├── loader.py
│   └── __init__.py
├── platform/
│   ├── access.py
│   ├── catalog.py
│   ├── namespaces.py
│   ├── policies.py
│   ├── maintenance.py
│   ├── polaris.py
│   ├── runtime.py
│   └── bootstrap.py
├── storage/
│   ├── object_store.py
│   └── iceberg.py
└── sources/
    └── github_archive/
        ├── client.py
        ├── models.py
        ├── parser.py
        ├── repository.py
        ├── schema.py
        └── service.py

dbt_project/
├── models/
│   ├── staging/<source>/
│   ├── intermediate/<business_concern>/
│   └── marts/<domain>/
├── selectors.yml
└── dbt_project.yml

orchestration/
├── flows/
│   ├── etl_github_archive.py
│   └── gov_iceberg_maintenance.py
├── utils/
│   ├── dbt.py
│   └── retries.py
└── plugins/
    └── notifications.py
```

## Current-code audit inventory

Các file dưới đây là điểm audit bắt buộc theo codebase tại thời điểm tạo plan. Việc xuất hiện trong danh sách không đồng nghĩa phải rewrite; chỉ thay đổi khi ownership hoặc source of truth thực sự sai.

### Configuration và contracts

- [x] `src/mini_lakehouse/config/settings.py`: changed — chỉ giữ secret/runtime setting và loại stale default.
- [x] `src/mini_lakehouse/contracts/tables.py`: removed — typed YAML contracts và identifier renderer thay thế registry hardcode.
- [x] `pyproject.toml` và `uv.lock`: changed — direct dependency, dependency group và frozen runtime install.
- [x] `.env` và `.env.example`: changed — key parity, local defaults, không có ClickHouse/GCS/stale key.

### Platform, Polaris và maintenance

- [x] `src/mini_lakehouse/platform/bootstrap.py`: changed — composition root mỏng; catalog/RBAC/namespace reconcile đã isolate.
- [x] `src/mini_lakehouse/platform/polaris.py`: changed — authentication, Management API boundary, pagination và concurrency semantics.
- [x] `src/mini_lakehouse/platform/policies.py`: changed — typed policy translation; unsupported content fail fast.
- [x] `src/mini_lakehouse/platform/maintenance.py`: changed — catalog discovery, metadata-only planning và per-table failure isolation.
- [x] `infra/polaris/bootstrap.sh`: unchanged intentionally — idempotent wrapper duy nhất quanh official realm bootstrap tool, không chứa desired catalog state.

### Storage và GitHub Archive source

- [x] `src/mini_lakehouse/storage/iceberg.py`: changed — chỉ còn generic catalog adapter; source schema/repository đã di chuyển.
- [x] `src/mini_lakehouse/storage/object_store.py`: changed — validated relative key và atomic conditional create qua S3 interface.
- [x] `src/mini_lakehouse/sources/github_archive/models.py`: raw event fidelity, typed record và checkpoint metadata.
- [x] `src/mini_lakehouse/sources/github_archive/parser.py`: payload preservation, parse failure semantics và schema compatibility.
- [x] `src/mini_lakehouse/sources/github_archive/service.py`: prefix/table hardcode, partition discovery, idempotency và bounded backfill.

### dbt

- [x] `dbt_project/dbt_project.yml`: changed — table/view/ephemeral materialization theo layer và schema ownership.
- [x] `dbt_project/models/staging/github_archive/`: changed — source freshness bằng `ingested_at`, source boundary và tests.
- [x] `dbt_project/models/intermediate/github/`: changed — grain/reuse/cost và private ownership rõ.
- [x] `dbt_project/models/marts/core/github/`: removed — technical `core` nesting không mang ownership.
- [x] `dbt_project/models/marts/github/`: added — curated GitHub product ownership.
- [x] `dbt_project/models/marts/engineering/`: changed — domain owner, public access, contracts và explicit projection.
- [x] `dbt_project/macros/generate_schema_name.sql`: unchanged intentionally — nested namespace/schema rendering đã đúng connector semantics.
- [x] `dbt_project/profiles.yml`: changed — dùng trực tiếp `LAKEHOUSE_TRINO__*`, loại source of
  truth trùng `DBT_CATALOG`/`TRINO_*`.
- [x] `dbt_project/tests/generic/`: unchanged intentionally — unique key nhiều cột vẫn là test project-specific cần thiết.

### Orchestration, deployment và presentation

- [x] `orchestration/flows/`: changed — chứa toàn bộ convention-named deployable DAG; flow và
  source-owned tasks co-locate, không có private task sidecar.
- [x] `orchestration/utils/`: added — một dbt runner boundary và retry policy dùng chung.
- [x] `orchestration/plugins/notifications.py`: added — threaded Slack/Gmail lifecycle hooks;
  task failure reply theo flow thread, terminal flow update parent.
- [x] `prefect.yaml`: changed — anchors, work queues, schedules và stable deployment entrypoints.
- [x] `compose.core.yaml`, `compose.dashboard.yaml`, `compose.prefect.yaml`: changed — modular capability ownership và shared container env.
- [x] `src/mini_lakehouse/presentation/data_loader.py`: changed — domain registry và shared identifier renderer.
- [x] `src/mini_lakehouse/presentation/pages/`: changed — business pages chỉ dùng public domain loader; metadata page là operational concern riêng.
- [x] `README.md` và `docs/`: changed — topology, operations, contracts, ownership và onboarding được cập nhật.

### Audit completion rule

- [x] Mỗi audit target được đánh dấu `unchanged`, `changed`, `added` hoặc `removed` ở trên.
- [x] Hardcode còn giữ được phân loại là invariant, local environment default hoặc source-owned behavior.
- [x] `rg` cuối cùng kiểm tra code, tests, docs, shell, Compose, dbt và sample environment.

## Boundary: YAML, Python, SQL và environment

### YAML as code

- [x] Catalog metadata non-secret.
- [x] Namespace hierarchy, lifecycle tier, owner và description.
- [x] Source identity, source type và checkpoint strategy; schedule ở native Prefect deployment YAML.
- [x] Logical table identifier, object prefix, partition intent và write mode được phép cấu hình.
- [x] Domain ownership và consumer-facing contract metadata.
- [x] Polaris policy type/version/content/target/attachment.
- [x] Prefect deployment metadata, schedule, work pool và concurrency limit phù hợp.
- [x] dbt sources, freshness, tests, groups, access, exposures và selectors trong native dbt YAML.

### Python

- [x] Pydantic models với `extra="forbid"` cho contract do project quản lý.
- [x] Cross-file validation: uniqueness, reference integrity, namespace/lifecycle rule và ownership.
- [x] Object store, Polaris REST, Iceberg/Arrow integration và retry/error handling.
- [x] Parser/source-specific behavior, checkpoint implementation và idempotency.
- [x] Desired-state reconciliation, canonicalization và safe apply; destructive plan/apply deferred rõ bên dưới.
- [x] Compiler/renderer khi API cần payload hoặc SQL; YAML không chứa executable code tùy ý.

### SQL và dbt native configuration

- [x] Business transformation nằm trong SQL model.
- [x] Source freshness, tests, model contracts, group/access và exposures nằm trong dbt YAML cạnh model phù hợp.
- [x] Không tạo một global YAML DSL khác để generate lại toàn bộ dbt project.

### Environment variables

- [x] Endpoint, credential, token, password và deployment-specific override.
- [x] `.env.example` chỉ có placeholder/default local không nhạy cảm.
- [x] `.env` phục vụ local theo yêu cầu, không được dùng như registry metadata của platform.

## Contract model tối thiểu

### Catalog contract

- [x] `version` của schema contract.
- [x] `catalog.name` và runtime Polaris/Trino settings cùng resolve thành `prod` ở environment hiện tại.
- [x] Ba storage roots: `landing`, `curated`, `analytics`.
- [x] Namespace có path, storage tier, owner, description và optional properties.
- [x] Validate namespace parent tồn tại trước child.
- [x] Validate bucket mapping đúng lifecycle, không reuse bucket sai mục đích.

### Source contract

- [x] `name` và `type` là stable identifier, không lấy display name làm key.
- [x] `owner`, `description`, `landing_namespace` và `object_prefix` bắt buộc.
- [x] `checkpoint` là Pydantic discriminated union: `hourly_partition`, `timestamp`, `cursor`, `offset`.
- [x] Mỗi table khai báo identifier, partition intent, write mode và schema contract reference.
- [x] Source-specific runtime option được validate bởi settings model riêng, không phải `dict[str, Any]` mở hoàn toàn.
- [x] Contract source không chứa secret value hoặc runtime credential reference.

### Domain contract

- [x] Stable domain name.
- [x] Technical owner và business owner.
- [x] Analytics publish namespace bắt buộc nằm dưới lifecycle root `analytics`.
- [x] dbt group, public model registry và exposure mapping.
- [x] Không thêm data classification/SLA giả khi chưa có consumer enforcement.

### Table physical contract

- [x] YAML khai báo identifier, partition fields, write mode và schema reference; format dùng adapter default thống nhất.
- [x] Arrow/Iceberg schema được định nghĩa trong source-owned Python module ở phase đầu.
- [x] Có test khóa field name, type, required flag và Iceberg field ID.
- [x] Chỉ cân nhắc schema DSL trong YAML sau khi có ít nhất ba source family thực tế và yêu cầu reuse đủ rõ.

## Polaris policy best practices

- [x] Pin contract theo Polaris policy type/version mà runtime đang hỗ trợ; không dùng payload vô version.
- [x] Một file YAML quản lý một policy để diff và review rõ ràng.
- [x] Policy name là stable identifier; description không được dùng làm identifier.
- [x] Target là typed union cho catalog/namespace/table, không phải chuỗi tự do.
- [x] Policy tier-wide attach ở lifecycle namespace tương ứng, không lạm dụng catalog target.
- [x] Validate inheritance để một resource không nhận nhiều policy cùng type ngoài ý muốn.
- [x] Validate toàn bộ `content`; field chưa support fail fast thay vì bị bỏ qua.
- [x] Field chưa được Trino runner enforce bị từ chối rõ bởi strict Pydantic content model.
- [x] Serialize payload thành canonical JSON để diff ổn định.
- [x] `deferred`: destructive plan mode chờ direct-mapping inventory; safe bootstrap chỉ
  create/update/attach và refuse unsafe catalog drift.
- [x] Apply idempotent: double bootstrap cho catalog/RBAC/namespace/policy body là no-op; mapping `PUT` idempotent.
- [x] `409 Conflict` khi bootstrap dẫn đến read-and-reconcile, không mặc định coi là thành công.
- [x] Applicable-policy API listing xử lý pagination và repeated-token guard.
- [x] Mapping bị xóa khỏi YAML không tự động detach; reviewed detach migration được document vì API thiếu direct mapping inventory.
- [x] Update kiểm tra immutable policy type và optimistic current version.
- [x] Log không chứa credential hoặc full auth response.
- [x] Local tiếp tục dùng Polaris root credentials; production principal/role/privilege separation là follow-up riêng.

## Storage và namespace strategy

### Object storage

```text
landing/
├── api/<source>/<table-or-stream>/<partition>/...
├── rdbms/<database>_raw/<schema>/<table>/<partition>/...
└── stream/<platform>/<topic>/<partition>/...

curated/
└── <product-or-dataset>/<table>/...

analytics/
└── <domain>/<data-product-or-mart>/<table>/...
```

- [x] Prefix được tạo từ validated source contract, không nối string rải rác ngoài source behavior.
- [x] Mỗi table có location riêng và không ghi file trực tiếp vào namespace root.
- [x] Partition path stable theo source-hour contract; legacy layout không được hỗ trợ theo owner decision.
- [x] Provider adapter tương lai chỉ thay transport/config, không thay logical namespace contract.
- [x] Không thêm dependency GCS khi chưa có deployment GCS.

### Polaris namespaces

```text
prod
├── landing.api.<source>
├── landing.rdbms.<database>_raw
├── landing.stream.<platform>
├── curated.<product-or-dataset>
└── analytics.<domain>
```

- [x] Dùng nested namespace để giữ boundary rõ ràng, không flatten mọi thứ thành `prod.landing`.
- [x] Query qua Trino quote namespace chứa dấu chấm theo connector semantics.
- [x] Một `TableIdentifier` renderer được dashboard, maintenance và source dùng chung.
- [x] Namespace ownership đến từ catalog/source/domain contract, không hardcode trong bootstrap.

## Source extensibility

- [x] `storage/` chỉ giữ generic object store, Iceberg catalog properties và catalog discovery.
- [x] GitHub-specific schema/repository nằm hoàn toàn trong `sources/github_archive/`.
- [x] Landing repository và raw event schema thuộc ownership của GitHub Archive source.
- [x] Parser trả typed event record và giữ full raw event JSON.
- [x] Checkpoint strategy là typed union; implementation idempotency vẫn source-specific.
- [x] Backfill nhận inclusive partition range và chỉ query partition/file metadata cần thiết.
- [x] Không list/full-scan bucket hoặc table rows để tìm work.
- [x] Rerun partition complete no-op; commit mới dùng dynamic partition overwrite, không append.
- [x] Source mới vào một validated registry loader; fixture RDBMS chứng minh core không cần sửa.

### Checklist onboarding một source mới

Đây là checklist template cố ý để trống cho từng source mới, không phải unfinished work của GitHub
Archive source hiện tại.

- [ ] Tạo `contracts/sources/<source>.yaml`.
- [ ] Chọn namespace landing theo transport: `api`, `rdbms` hoặc `stream`.
- [ ] Tạo source package gồm client/parser/repository/service cần thiết.
- [ ] Định nghĩa và test schema cùng immutable field IDs.
- [ ] Chọn checkpoint strategy và idempotency key.
- [ ] Tạo DAG theo `[job_type]_[description].py`.
- [ ] Tạo dbt source + staging models 1:1.
- [ ] Gán owner/domain trước khi tạo curated/intermediate/mart model.
- [ ] Thêm policy attachment theo tier; không copy policy content.
- [ ] Thêm integration test cho bootstrap, rerun và backfill.
- [ ] Update lineage/exposure/documentation.

## dbt structure và contract

### Staging

- [x] Tổ chức `models/staging/<source>/`.
- [x] Mỗi staging model đọc trực tiếp từ `source()`, gần 1:1 với source relation.
- [x] Staging chỉ cast, rename, basic cleanup và derive source-local field nhẹ.
- [x] Staging materialize `view` trong private namespace.
- [x] Source YAML khai báo loader, owner/meta, loaded-at semantics và freshness threshold.
- [x] Freshness dùng `ingested_at`, không dùng event timestamp.

### Intermediate

- [x] Tổ chức `models/intermediate/<business_concern>/`.
- [x] Join, deduplication, grain preparation và reusable logic nằm ở intermediate.
- [x] Lightweight logic ephemeral; enriched event dùng table để tránh lặp parse/scan trên Trino.
- [x] Model private theo mặc định.

### Marts

- [x] Tổ chức `models/marts/<domain>/`, không còn technical folder `core`.
- [x] Mỗi model có grain, owner, description và primary data quality tests.
- [x] Public model dùng dbt `group`/`access` và enforced contract.
- [x] File path đổi nhưng physical model name/schema được kiểm chứng qua double build.
- [x] Analytics output ghi vào `analytics.<domain>`.

### Selection và execution

- [x] `selectors.yml` có named source-freshness và engineering pipeline selection.
- [x] Prefect gọi named selector thay vì lặp graph expression.
- [x] `dbt parse`, selector resolution và double `dbt build` đã pass trên live Trino/Polaris stack.
- [x] Không còn `+on_table_exists`; adapter config được validate bằng dbt runtime.
- [x] Active persisted models dùng `table`; bounded `is_incremental()` predicates được giữ inactive.

## Prefect và orchestration

- [x] Mỗi DAG file tự chứa flow và source-specific task nhỏ liên quan.
- [x] Chỉ shared business/storage capability được extract; không có shared `tasks.py`.
- [x] Orchestration không có `__init__.py` và không là application module.
- [x] Tên file và deployment tuân theo job type convention.
- [x] `prefect.yaml` dùng anchors cho work pool và singleton concurrency.
- [x] Schedule/source parameters đặt cạnh deployment và input được domain model validate.
- [x] Maintenance discover target từ Iceberg catalog và applicable Polaris policies.
- [x] Maintenance có bounded concurrency, per-table failure isolation và summary.
- [x] Prefect local process worker là default.
- [x] Dashboard metadata và maintenance cùng dùng generic Iceberg discovery/identifier renderer.

## Compose, uv và environment

- [x] Compose modular theo capability với convention `compose.<capability>.yaml`.
- [x] Không có generic `compose.yaml` gây hiểu nhầm scope.
- [x] Document file set cho core, orchestration và dashboard.
- [x] Non-secret container env dùng committed env file; Compose anchors dùng cho repeated service blocks.
- [x] Ba merged profile đều pass `docker compose ... config --quiet`.
- [x] Service images giữ `latest` theo project policy hiện tại.
- [x] `.env` và `.env.example` đồng bộ local key; contract metadata không nằm trong env.
- [x] Không có GCS package/extra.
- [x] `pyproject.toml` khai báo direct Pydantic/PyYAML và mọi import runtime.
- [x] Dev tooling dùng dependency group; runtime/orchestration/dashboard sync đúng extra riêng.
- [x] `uv.lock` current; Docker và CI dùng frozen install.
- [x] Core dependency không bị ẩn trong optional extra.

## Implementation phases

### Phase 0 — Baseline và safety net

- [x] Fresh stack inventory: 8 namespace, 7 Iceberg table và 4 inherited policy được live discovery.
- [x] Ba bucket/location roots và source/table prefixes được khóa trong strict contracts.
- [x] dbt graph/relation mapping được parse/double-build; source freshness live pass, fixture
  failure-threshold test vẫn deferred.
- [x] Chụp/verify merged config của từng Compose stack.
- [x] Chạy toàn bộ test hiện có; không có known non-integration failure.
- [x] Normalized non-secret desired state là deterministic typed contract registry.
- [x] Không có ClickHouse import, env, compose service, docs hoặc dependency active.

Exit criteria:

- [x] Owner waive legacy baseline và cho phép recreate; fresh desired state/live inventory là baseline mới.
- [x] Không giữ compatibility branch cho state cũ theo explicit owner decision.

### Phase 1 — Pydantic contract foundation

- [x] Thêm direct dependencies cần thiết bằng uv và update lockfile.
- [x] Tạo versioned Pydantic models cho catalog, namespace, source, domain và policy.
- [x] Bật strict validation/`extra="forbid"` tại boundary phù hợp.
- [x] Tạo loader deterministic, resolve file theo sorted path.
- [x] Tạo cross-contract validator cho duplicate ID, missing reference, invalid tier và unsafe prefix.
- [x] Tạo canonical serializer; JSON Schema artifact được đánh giá là chưa cần commit ở scope hiện tại.
- [x] Thêm command `validate` không tạo side effect.

Exit criteria:

- [x] Invalid/unknown config fail trước khi gọi MinIO, Polaris, Trino hoặc Prefect.
- [x] Cùng input tạo cùng normalized manifest và diff ổn định.

### Phase 2 — Catalog và namespace desired state

- [x] Chuyển catalog/storage root/namespace metadata sang `contracts/catalog.yaml`.
- [x] Refactor bootstrap thành reconciler đọc typed contract.
- [x] Tách render identifier/location ra shared component.
- [x] Bootstrap catalog conflict bằng read-and-reconcile.
- [x] `deferred`: thêm destructive `plan/apply` khi Polaris có direct-mapping inventory; safe
  reconciler hiện tại không suy diễn delete/detach và fail catalog drift.
- [x] Chạy apply hai lần để chứng minh idempotency.

Exit criteria:

- [x] Baseline data cũ được owner waive; fresh desired state được validate và bootstrap từ empty state.
- [x] Catalog/namespace/location đúng contract trên fresh local stack.
- [x] Apply lần hai không tạo catalog/RBAC/namespace/policy-body mutation.

### Phase 3 — Polaris policy as code

- [x] Tách mỗi policy thành một YAML file có version/type/content/target rõ ràng.
- [x] Implement full typed content cho policy đang dùng.
- [x] Fail fast cho field/type/version chưa support.
- [x] Implement paginated discovery, diff, create/update và desired attachment reconciliation.
- [x] Tách safe apply khỏi detach/delete apply; detach bắt buộc reviewed migration do Polaris không có direct-mapping list API.
- [x] Test inheritance và duplicate effective policy.
- [x] Loại bỏ target/table hardcode khỏi maintenance policy code.

Exit criteria:

- [x] Không còn policy content/target hardcode trong Python.
- [x] Apply lần hai giữ policy body unchanged; mapping `PUT` là idempotent.
- [x] Mọi field trong YAML đều được apply hoặc bị reject; không field nào bị ignore.

### Phase 4 — Storage/source ownership split

- [x] Tách generic Iceberg catalog/table factory khỏi GitHub-specific repository/schema.
- [x] Di chuyển raw event schema và landing repository vào source package.
- [x] Tạo typed source contract và checkpoint protocol.
- [x] Refactor GitHub Archive service dùng contract, không hardcode prefix/table.
- [x] Giữ landing raw event fidelity; không discard payload cần cho replay/debug.
- [x] Tối ưu discovery bằng Iceberg partition/file metadata thay cho full data scan.
- [x] Thêm bounded backfill theo time partition với rerun idempotent.

Exit criteria:

- [x] Generic storage module không import GitHub Archive.
- [x] Ingest cùng partition hai lần không tạo duplicate.
- [ ] Backfill không scan ngoài requested range.

### Phase 5 — dbt layer alignment

- [x] Inventory model grain, dependency, owner và physical relation hiện tại.
- [x] Chuyển tree về `staging/<source>`, `intermediate/<concern>`, `marts/<domain>`.
- [x] Giữ model name/schema để tránh đổi physical relation khi chỉ rename file/path.
- [x] Sửa source freshness theo loaded-at semantics thực tế.
- [x] Chuyển active model materialization về `table`.
- [x] Cô lập và giữ inactive incremental predicate/checkpoint logic.
- [x] Tạo groups/access/contracts cho public mart phù hợp.
- [x] Tạo named selectors và update Prefect command.
- [x] Chạy selector resolution và double `dbt build`; cả hai lần `PASS=32`, `ERROR=0`.

Exit criteria:

- [x] DAG thể hiện source → staging → intermediate → mart rõ ràng.
- [x] Không có mart đọc trực tiếp landing nếu chưa có exception được document.
- [x] Freshness phản ánh ingestion delay đúng.
- [x] Không relation bị đổi identifier chỉ do refactor path.

### Phase 6 — Prefect, maintenance và dashboard

- [x] Deployable DAG tập trung trong `flows/`; source task trùng được factor vào private module.
- [x] Rename DAG/deployment theo job type convention.
- [x] Generate maintenance target từ validated contracts/catalog discovery.
- [x] Dashboard đọc shared registry/metadata, không giữ physical table identifier riêng.
- [x] Deduplicate Prefect deployment config bằng anchors/definitions.
- [x] Gắn Slack/Gmail best-effort lifecycle hooks: flow running/success/failure và task failure;
  alert có failed object, flow link, Slack thread và styled Gmail HTML.
- [x] Giữ local process worker và root credentials local.
- [x] Unit test partial failure giữ successful sibling summary; live flow hoàn tất 7/7 table.

Exit criteria:

- [x] Maintenance và dashboard discover table từ catalog; không có table allowlist.
- [x] Hai deployment entrypoint được Prefect load bằng orchestration image; ingestion deployment
  dùng cùng parameter schema cho schedule, one-hour replay và inclusive backfill.

### Phase 7 — Compose và package cleanup

- [x] Chuẩn hóa toàn bộ tên file `compose.<capability>.yaml`.
- [x] Document command start/stop/log cho mỗi profile.
- [x] Deduplicate non-secret environment và update `.env`/`.env.example`.
- [x] Xóa stale env, dependency, code path liên quan ClickHouse/GCS/old compose naming; future-provider docs chỉ là explicit documentation.
- [x] Verify every merged Compose config.
- [x] Runtime, orchestration và dashboard image build thành công bằng `uv sync --frozen`.

Exit criteria:

- [x] Không còn compose file có scope mơ hồ.
- [x] `docker compose ... config` pass cho mọi documented profile.
- [x] `rg` không còn active ClickHouse/GCS/stale config reference, trừ migration history hoặc explicit documentation.

### Phase 8 — Extensibility proof và documentation

- [x] Tạo fixture/dummy contract cho source family thứ hai trong test, không cần deploy production source.
- [x] Chứng minh contract registry/namespace validation không cần sửa core cho fixture mới.
- [x] Viết source onboarding runbook.
- [x] Viết ownership matrix cho platform, source, domain và operations.
- [x] Viết policy operation runbook gồm safe apply/detach/rollback.
- [x] Update architecture diagram và data flow documentation.

Exit criteria:

- [x] Fixture RDBMS thêm source-owned contract mà không sửa core registry/storage implementation.
- [x] Ownership matrix chỉ rõ owner của namespace, model, policy, dashboard và deployment.

## Test matrix

### Unit tests

- [x] Unknown YAML key bị reject.
- [x] Duplicate source/domain/policy/table ID bị reject bởi model/cross-file validation.
- [x] Missing namespace/source/domain/policy target reference bị reject.
- [x] Invalid lifecycle-to-bucket mapping bị reject.
- [x] Secret-like key trong contract bị reject trước model parsing.
- [x] Invalid nested namespace/identifier bị reject.
- [x] Duplicate/effective policy conflict bị reject.
- [x] Unsupported policy content field bị reject.
- [x] Checkpoint union parse theo discriminated `kind`.
- [x] Identifier và object prefix có validation/golden tests.
- [x] Iceberg field IDs/name/required flags có snapshot test.

### Contract/golden tests

- [x] Normalized registry stable qua nhiều lần load và có deterministic test.
- [x] Legacy hardcoded desired state equivalence được waive; old source of truth đã bị xóa.
- [x] Polaris policy request content có canonical JSON test.
- [x] Approved fresh relation map đúng landing/curated/analytics contract và pass double build.
- [x] N/A snapshot file: cả ba merged Compose topology đã render và boot/health-check live.

### Integration tests

- [x] Polaris bootstrap từ empty state.
- [x] Polaris bootstrap lần hai no-op cho mutable desired state.
- [x] Drifted namespace và versioned policy body update đúng qua reconciliation tests.
- [x] Removed mapping không bị safe bootstrap tự detach; manual reviewed migration được document.
- [x] Live maintenance resolve đủ bốn inherited policy cho 7 discovered table.
- [x] Live dynamic-partition-overwrite cùng identity partition rerun không duplicate; giờ 04
  `was_written=false`, row count giữ 164,186.
- [ ] Backfill chỉ đọc requested range.
- [x] Maintenance live discovery chạy 7/7 table, 28 statement; failure isolation có unit test.
- [x] Dashboard container discover cùng 7 table từ generic Iceberg catalog traversal.

### dbt verification

- [x] N/A: project không có `packages.yml`; dbt adapter/runtime được lock bằng `uv.lock`.
- [x] `dbt parse` pass.
- [x] Named selector resolve đúng graph và được build live.
- [ ] Source freshness pass/fail đúng bằng fixture có loaded-at timestamp.
- [x] Double `dbt build` pass staging, intermediate và marts.
- [x] Public model contracts enforce type/name qua Trino build introspection.
- [x] Không model active nào materialize incremental.

### Deployment verification

- [x] Tất cả documented Compose profile render được.
- [x] Core, Prefect và dashboard long-lived services healthy/running; one-shot services exit 0.
- [x] Prefect deployment entrypoints load và register được.
- [x] Prefect work pool/concurrency/schedule resolve qua `prefect deploy --all`.
- [x] `.env.example` và `.env` có cùng local configuration key set.
- [x] Notification config fail-fast khi Slack/Gmail thiếu credential pair; Slack task payload có
  thread timestamp, detail và parent flow-run URL; Gmail có HTML test.

## Migration safety và rollback

- [x] N/A phase commits: implementation là một authorized clean-slate refactor; suggested slices ở dưới chỉ là commit guidance.
- [x] Config source-of-truth cutover không đổi approved physical relation identifiers.
- [x] Legacy current-state manifest được owner waive; fresh deterministic registry là baseline mới.
- [x] Legacy equivalence test được owner waive cùng compatibility/data recreation decision.
- [x] Source of truth cũ (`contracts/tables.py`, old source package, dbt `core` tree) bị xóa cùng cutover.
- [x] Policy detach/delete không nằm trong default apply.
- [x] dbt path rename giữ physical model name và schema, được double-build verify.
- [x] Table schema không đổi field ID; snapshot test bảo vệ future evolution.
- [x] Reconciler không tự drop resource khi failure/drift; rollback procedure được document.
- [x] Không ghi synthetic source data trong verification; dbt table rỗng thuộc fresh local stack.

## Suggested commit slices

Danh sách dưới đây là commit-message template cho lần commit/review tiếp theo, không phải execution
checklist của plan.

- [ ] `docs: add declarative lakehouse refactor plan`
- [ ] `refactor(config): add validated platform contracts`
- [ ] `refactor(platform): reconcile catalog and namespaces from contracts`
- [ ] `refactor(polaris): manage policies as validated desired state`
- [ ] `refactor(storage): move github archive ownership into source package`
- [ ] `refactor(dbt): align staging intermediate and domain marts`
- [ ] `refactor(orchestration): modularize flows and metadata-driven maintenance`
- [ ] `refactor(deploy): normalize compose and environment configuration`
- [ ] `docs: add source onboarding and operations runbooks`

Không bắt buộc đúng số commit trên nếu dependency thực tế yêu cầu gộp/tách, nhưng mỗi commit phải pass test phù hợp và không trộn cleanup không liên quan.

## Risk register

| Risk | Impact | Mitigation | Verification |
|---|---|---|---|
| Namespace quoting sai trên Trino | Query fail hoặc trỏ sai relation | Một identifier renderer, golden SQL tests | `dbt compile` và integration query |
| YAML/Python thành hai source of truth | Drift và apply không dự đoán được | Equivalence test rồi xóa hardcode cùng phase | `rg` + normalized manifest |
| Policy field bị ignore | Retention/maintenance sai | Strict typed content, reject unsupported field | Negative unit tests |
| dbt path rename tạo relation mới | Duplicate/rebuild data | Giữ alias/schema, diff relation manifest | `dbt ls` trước/sau |
| Backfill append trùng | Sai metric/chi phí storage | Checkpoint + idempotency key + partition commit test | Rerun integration test |
| Generic framework quá trừu tượng | Onboarding khó và nhiều indirection | Chỉ abstract behavior đã có ít nhất hai use case | Source fixture proof |
| Field ID thay đổi | Iceberg schema corruption/incompatibility | Code-owned schema + snapshot test | Schema contract test |
| Compose dedup làm mất env | Service không boot | Render merged config của từng profile | Compose config + healthcheck |
| Safe reconcile xóa resource | Data/policy mất ngoài ý muốn | Plan-first, detach/delete opt-in | No-delete integration test |

## Definition of Done

- [x] Mọi phase trong scope đạt exit criteria hoặc có deferred verification kèm lý do ở đầu plan.
- [x] Không còn active ClickHouse hoặc GCS dependency/config/code path.
- [x] Không còn namespace, policy target, dashboard table hoặc maintenance table allowlist rải rác.
- [x] Catalog/namespace/source/domain/policy contracts parse bằng strict Pydantic modules riêng.
- [x] Polaris reconciliation idempotent và unsupported content field fail fast.
- [x] Storage generic không sở hữu GitHub-specific schema/repository.
- [x] dbt theo staging → intermediate → domain marts, freshness dùng `ingested_at`, active persisted model là table/view.
- [x] Prefect DAG nằm trong `flows/`, reusable utils/plugins modular, đúng naming convention.
- [x] Compose naming/config nhất quán; `.env` và `.env.example` đồng bộ.
- [x] Backfill bounded/checkpointed; discovery chỉ đọc partition/file metadata.
- [x] Dashboard/maintenance dùng generic catalog discovery và identifier renderer chung.
- [x] Unit/contract/static/catalog integration/dbt/deployment/maintenance và live idempotency pass;
  bounded backfill range proof deferred rõ ở đầu plan.
- [x] Architecture, ownership, onboarding và operations docs được cập nhật.
- [x] `git diff --check` pass; `.env`/generated dbt artifacts vẫn ignored và không được stage.

## Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-21 | Dùng YAML as code cho desired state, Pydantic cho validation | Reviewable, strict và giảm hardcode |
| 2026-07-21 | Giữ Iceberg schema code-owned ở phase đầu | Bảo vệ field ID/type evolution, tránh schema DSL quá sớm |
| 2026-07-21 | dbt source đọc landing | Staging là source-boundary; curated là output đã transform |
| 2026-07-21 | Duy trì nested namespaces | Giữ transport/source/domain ownership và mở rộng tốt hơn |
| 2026-07-21 | Reconcile plan-first, delete/detach opt-in | Giảm rủi ro mất resource/data |
| 2026-07-21 | Không physical rename trong config refactor | Tách architecture cleanup khỏi data migration |
| 2026-07-21 | Waive live data inventory | Owner xác nhận có thể recreate; không giữ compatibility path/layout cũ |
| 2026-07-21 | Idempotency theo `source_hour` partition | Conditional object create + dynamic partition overwrite là commit boundary lâu dài |
| 2026-07-21 | Policy detach là reviewed migration | Polaris không cung cấp API enumerate toàn bộ direct mappings của một policy |
| 2026-07-21 | Tách contract và platform reconciler theo ownership | Source/domain/policy models và catalog/RBAC/namespace apply có boundary/test độc lập |
| 2026-07-21 | Không thêm destructive-plan heuristic | Direct mapping inventory chưa đầy đủ; safe default không delete/detach |
| 2026-07-21 | Dashboard metadata dùng catalog discovery | Source/model mới xuất hiện mà không sửa presentation allowlist |
| 2026-07-21 | Identity partition cho `source_hour` | Dynamic partition overwrite của PyIceberg không hỗ trợ non-identity transform |
| 2026-07-21 | Polaris dùng configured data-plane endpoint | Iceberg client phải thấy endpoint container truy cập được; mutable update dùng `entityVersion` |
| 2026-07-21 | `flows/` là DAG discovery boundary | Giống `dags/` của Airflow; Prefect entrypoint và job-type filename nhất quán |
| 2026-07-21 | Slack Bot API + Prefect Variable thread | Incoming webhook không trả `thread_ts`; task failure cần reply đúng flow thread |
| 2026-07-21 | Một parameterized ingestion deployment | Scheduled run dùng previous hour; custom `start_hour`/`end_hour` thay DAG backfill redundant |

## References

- [dbt project structure](https://docs.getdbt.com/best-practices/how-we-structure/1-guide-overview)
- [dbt model contracts](https://docs.getdbt.com/docs/mesh/govern/model-contracts)
- [dbt model access](https://docs.getdbt.com/docs/mesh/govern/model-access)
- [dbt selectors](https://docs.getdbt.com/reference/node-selection/yaml-selectors)
- [Apache Polaris policies](https://polaris.apache.org/releases/1.5.0/policy/)
- [Apache Iceberg partitioning](https://iceberg.apache.org/docs/latest/partitioning/)
- [Trino Iceberg connector](https://trino.io/docs/current/connector/iceberg.html)
- [Prefect deployment YAML](https://docs.prefect.io/v3/deploy/infrastructure-concepts/prefect-yaml)
- [uv projects and dependencies](https://docs.astral.sh/uv/concepts/projects/dependencies/)
