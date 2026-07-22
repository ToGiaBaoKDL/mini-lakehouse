from pydantic import model_validator

from mini_lakehouse.contracts.base import ContractModel
from mini_lakehouse.contracts.catalog import CatalogContract
from mini_lakehouse.contracts.curated_products import CuratedProductContract
from mini_lakehouse.contracts.domains import DomainContract
from mini_lakehouse.contracts.policies import PolicyContract
from mini_lakehouse.contracts.sources import SourceContract


class PlatformContracts(ContractModel):
    catalog: CatalogContract
    sources: tuple[SourceContract, ...]
    curated_products: tuple[CuratedProductContract, ...]
    domains: tuple[DomainContract, ...]
    policies: tuple[PolicyContract, ...]

    @model_validator(mode="after")
    def validate_references(self) -> "PlatformContracts":
        namespace_paths = {namespace.path for namespace in self.catalog.namespaces}
        source_names = [source.name for source in self.sources]
        product_names = [product.name for product in self.curated_products]
        domain_names = [domain.name for domain in self.domains]
        policy_keys = [(policy.namespace, policy.name) for policy in self.policies]
        if len(source_names) != len(set(source_names)):
            raise ValueError("Source names must be unique")
        if len(product_names) != len(set(product_names)):
            raise ValueError("Curated product names must be unique")
        if len(domain_names) != len(set(domain_names)):
            raise ValueError("Domain names must be unique")
        if len(policy_keys) != len(set(policy_keys)):
            raise ValueError("Policy namespace/name pairs must be unique")

        source_table_identifiers = [
            source.table_identifier(table.key) for source in self.sources for table in source.tables
        ]
        if len(source_table_identifiers) != len(set(source_table_identifiers)):
            raise ValueError("Landing source table identifiers must be globally unique")

        for source in self.sources:
            if source.landing_namespace not in namespace_paths:
                raise ValueError(f"Source {source.name!r} references an unknown landing namespace")
        known_sources = set(source_names)
        for product in self.curated_products:
            if product.curated_namespace not in namespace_paths:
                raise ValueError(
                    f"Curated product {product.name!r} references an unknown curated namespace"
                )
            unknown_sources = set(product.upstream_sources) - known_sources
            if unknown_sources:
                raise ValueError(
                    f"Curated product {product.name!r} references unknown sources "
                    f"{sorted(unknown_sources)!r}"
                )
        known_products = set(product_names)
        for domain in self.domains:
            if domain.analytics_namespace not in namespace_paths:
                raise ValueError(
                    f"Domain {domain.name!r} references an unknown analytics namespace"
                )
            unknown_products = set(domain.upstream_curated_products) - known_products
            if unknown_products:
                raise ValueError(
                    f"Domain {domain.name!r} references unknown curated products "
                    f"{sorted(unknown_products)!r}"
                )

        product_namespaces = {product.curated_namespace for product in self.curated_products}
        curated_leaves = {
            path
            for path in namespace_paths
            if path[0] == "curated"
            and len(path) >= 2
            and not any(other[:-1] == path for other in namespace_paths)
        }
        if curated_leaves != product_namespaces:
            raise ValueError("Every curated leaf must have exactly one curated product contract")

        domain_namespaces = {domain.analytics_namespace for domain in self.domains}
        analytics_domains = {
            path for path in namespace_paths if path[0] == "analytics" and len(path) == 2
        }
        if analytics_domains != domain_namespaces:
            raise ValueError(
                "Every direct analytics namespace must have exactly one domain contract"
            )

        attachments: set[tuple[str, tuple[str, ...], str]] = set()
        table_partition_fields = {
            source.table_identifier(table.key).iceberg: {
                partition.field for partition in table.partitioning
            }
            for source in self.sources
            for table in source.tables
        }
        table_partition_fields.update(
            {
                product.table_identifier(table.key).iceberg: {
                    partition.field for partition in table.partitioning
                }
                for product in self.curated_products
                for table in product.tables
            }
        )
        table_partition_fields.update(
            {
                domain.table_identifier(table.key).iceberg: {
                    partition.field for partition in table.partitioning
                }
                for domain in self.domains
                for table in domain.tables
            }
        )
        for policy in self.policies:
            if policy.namespace not in namespace_paths:
                raise ValueError(f"Policy {policy.name!r} is stored in an unknown namespace")
            for target in policy.targets:
                if target.path and target.path[0] != policy.namespace[0]:
                    raise ValueError(f"Policy {policy.name!r} cannot cross lifecycle tiers")
                if target.type == "namespace" and target.path not in namespace_paths:
                    raise ValueError(f"Policy {policy.name!r} targets an unknown namespace")
                if target.type == "table-like" and target.path[:-1] not in namespace_paths:
                    raise ValueError(
                        f"Policy {policy.name!r} targets a table in an unknown namespace"
                    )
                key = (target.type, target.path, policy.policy_type)
                if key in attachments:
                    raise ValueError(
                        "Only one inheritable policy of a type may target the same resource"
                    )
                attachments.add(key)
                if policy.policy_type == "system.data-compaction":
                    if target.type == "table-like":
                        known_fields = table_partition_fields.get(target.path)
                        matching_tables = (
                            {target.path: known_fields} if known_fields is not None else {}
                        )
                    elif target.type == "namespace":
                        matching_tables = {
                            path: fields
                            for path, fields in table_partition_fields.items()
                            if path[:-1][: len(target.path)] == target.path
                        }
                    else:
                        matching_tables = table_partition_fields
                    incompatible_tables = {
                        path: fields
                        for path, fields in matching_tables.items()
                        if policy.execution.partition_field not in fields
                    }
                    if incompatible_tables:
                        details = "; ".join(
                            f"{path!r} partitions by {sorted(fields)!r}"
                            for path, fields in sorted(incompatible_tables.items())
                        )
                        raise ValueError(
                            f"Policy {policy.name!r} bounds optimize by "
                            f"{policy.execution.partition_field!r}, but {details}"
                        )
        return self

    def source(self, name: str) -> SourceContract:
        try:
            return next(source for source in self.sources if source.name == name)
        except StopIteration as error:
            raise KeyError(f"Unknown source: {name}") from error

    def domain(self, name: str) -> DomainContract:
        try:
            return next(domain for domain in self.domains if domain.name == name)
        except StopIteration as error:
            raise KeyError(f"Unknown domain: {name}") from error

    def curated_product(self, name: str) -> CuratedProductContract:
        try:
            return next(product for product in self.curated_products if product.name == name)
        except StopIteration as error:
            raise KeyError(f"Unknown curated product: {name}") from error
