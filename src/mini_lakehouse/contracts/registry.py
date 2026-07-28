from pydantic import model_validator

from mini_lakehouse.contracts.access import AccessContract, NamespaceGrantContract
from mini_lakehouse.contracts.base import ContractModel
from mini_lakehouse.contracts.curated import CuratedProductContract
from mini_lakehouse.contracts.domains import DomainContract
from mini_lakehouse.contracts.maintenance import MaintenanceContract, MaintenancePolicy
from mini_lakehouse.contracts.platform import NamespaceContract, PlatformContract
from mini_lakehouse.contracts.processors import ProcessorContract
from mini_lakehouse.contracts.sources import SourceContract


class PlatformContracts(ContractModel):
    platform: PlatformContract
    access: AccessContract
    maintenance: MaintenanceContract
    sources: tuple[SourceContract, ...]
    curated: tuple[CuratedProductContract, ...]
    processors: tuple[ProcessorContract, ...]
    domains: tuple[DomainContract, ...]

    @property
    def policies(self) -> tuple[MaintenancePolicy, ...]:
        return self.maintenance.policies()

    @model_validator(mode="after")
    def validate_references(self) -> "PlatformContracts":
        source_names = [source.name for source in self.sources]
        product_names = [product.name for product in self.curated]
        processor_names = [processor.name for processor in self.processors]
        domain_names = [domain.name for domain in self.domains]
        policy_keys = [(policy.namespace, policy.name) for policy in self.policies]
        if len(source_names) != len(set(source_names)):
            raise ValueError("Source names must be unique")
        if len(product_names) != len(set(product_names)):
            raise ValueError("Curated product names must be unique")
        if len(processor_names) != len(set(processor_names)):
            raise ValueError("Processor names must be unique")
        if len(domain_names) != len(set(domain_names)):
            raise ValueError("Domain names must be unique")
        if len(policy_keys) != len(set(policy_keys)):
            raise ValueError("Policy namespace/name pairs must be unique")

        managed_namespaces = self.managed_namespaces()
        namespace_paths = {namespace.path for namespace in managed_namespaces}
        if len(namespace_paths) != len(managed_namespaces):
            raise ValueError("Every managed namespace must have exactly one owner")
        for namespace in managed_namespaces:
            if len(namespace.path) > 1 and namespace.path[:-1] not in namespace_paths:
                raise ValueError(
                    f"Namespace {'.'.join(namespace.path)!r} is missing its parent namespace"
                )
        for role in self.access.catalog_roles:
            for grant in role.grants:
                if (
                    isinstance(grant, NamespaceGrantContract)
                    and grant.namespace not in namespace_paths
                ):
                    raise ValueError(
                        f"Catalog role {role.name!r} references unknown namespace "
                        f"{'.'.join(grant.namespace)!r}"
                    )

        source_table_identifiers = [
            source.table_identifier(table.key) for source in self.sources for table in source.tables
        ]
        if len(source_table_identifiers) != len(set(source_table_identifiers)):
            raise ValueError("Landing source table identifiers must be globally unique")

        known_sources = set(source_names)
        for product in self.curated:
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
        products_by_name = {product.name: product for product in self.curated}
        for processor in self.processors:
            if processor.source not in known_sources:
                raise ValueError(
                    f"Processor {processor.name!r} references unknown source {processor.source!r}"
                )
            if processor.curated_product not in known_products:
                raise ValueError(
                    f"Processor {processor.name!r} references unknown curated product "
                    f"{processor.curated_product!r}"
                )
            elif (
                processor.source not in products_by_name[processor.curated_product].upstream_sources
            ):
                raise ValueError(
                    f"Processor {processor.name!r} source {processor.source!r} is not upstream "
                    f"of curated product {processor.curated_product!r}"
                )
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
                for product in self.curated
                for table in product.tables
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
                    if policy.execution is None:
                        raise ValueError(
                            f"Policy {policy.name!r} has no bounded optimization contract"
                        )
                    if target.type == "table-like":
                        known_fields = table_partition_fields.get(target.path)
                        matching_tables = (
                            {target.path: known_fields} if known_fields is not None else {}
                        )
                    else:
                        matching_tables = {
                            path: fields
                            for path, fields in table_partition_fields.items()
                            if path[:-1][: len(target.path)] == target.path
                        }
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

    def managed_namespaces(self) -> tuple[NamespaceContract, ...]:
        product_namespaces = tuple(
            NamespaceContract(
                path=product.curated_namespace,
                owner=product.owner,
                description=product.description,
                properties={"data_product": product.name},
            )
            for product in self.curated
        )
        domain_namespaces = tuple(
            NamespaceContract(
                path=domain.analytics_namespace,
                owner=domain.owner,
                description=domain.description,
                properties={
                    "business_domain": domain.name,
                    "business_owner": domain.business_owner,
                },
            )
            for domain in self.domains
        )
        return (*self.platform.namespaces, *product_namespaces, *domain_namespaces)

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
            return next(product for product in self.curated if product.name == name)
        except StopIteration as error:
            raise KeyError(f"Unknown curated product: {name}") from error

    def processor(self, name: str) -> ProcessorContract:
        try:
            return next(processor for processor in self.processors if processor.name == name)
        except StopIteration as error:
            raise KeyError(f"Unknown processor: {name}") from error
