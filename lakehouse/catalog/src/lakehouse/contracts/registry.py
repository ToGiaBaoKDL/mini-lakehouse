from pydantic import Field, model_validator

from lakehouse.contracts.base import (
    ContractModel,
    ContractName,
    Identifier,
)
from lakehouse.contracts.curated import CuratedProductContract
from lakehouse.contracts.domains import DomainContract
from lakehouse.contracts.sources import SourceContract


class NamespaceContract(ContractModel):
    path: tuple[Identifier, ...] = Field(min_length=1, max_length=1)
    owner: ContractName
    description: str = Field(min_length=1)
    properties: dict[str, str] = Field(default_factory=dict)


class DataContracts(ContractModel):
    sources: tuple[SourceContract, ...]
    curated: tuple[CuratedProductContract, ...]
    domains: tuple[DomainContract, ...]

    @model_validator(mode="after")
    def validate_references(self) -> "DataContracts":
        source_names = [source.name for source in self.sources]
        product_names = [product.name for product in self.curated]
        domain_names = [domain.name for domain in self.domains]
        for label, values in (
            ("Source", source_names),
            ("Curated product", product_names),
            ("Domain", domain_names),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} names must be unique")

        identifiers = [
            source.table_identifier(table.key) for source in self.sources for table in source.tables
        ]
        identifiers.extend(
            product.table_identifier(table.key)
            for product in self.curated
            for table in product.tables
        )
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Managed Iceberg table identifiers must be globally unique")

        known_sources = set(source_names)
        for product in self.curated:
            unknown = set(product.upstream_sources) - known_sources
            if unknown:
                raise ValueError(
                    f"Curated product {product.name!r} references unknown sources "
                    f"{sorted(unknown)!r}"
                )
        known_products = set(product_names)
        for domain in self.domains:
            unknown = set(domain.upstream_curated_products) - known_products
            if unknown:
                raise ValueError(
                    f"Domain {domain.name!r} references unknown curated products "
                    f"{sorted(unknown)!r}"
                )
        return self

    def managed_namespaces(self) -> tuple[NamespaceContract, ...]:
        namespaces = [
            NamespaceContract(
                path=(source.database,),
                owner=source.owner,
                description=source.description,
                properties={"data_tier": "landing", "source": source.name},
            )
            for source in self.sources
        ]
        namespaces.extend(
            NamespaceContract(
                path=(product.database,),
                owner=product.owner,
                description=product.description,
                properties={"data_tier": "curated", "data_product": product.name},
            )
            for product in self.curated
        )
        namespaces.extend(
            NamespaceContract(
                path=(domain.database,),
                owner=domain.owner,
                description=domain.description,
                properties={
                    "data_tier": "analytics",
                    "business_domain": domain.name,
                    "business_owner": domain.business_owner,
                },
            )
            for domain in self.domains
        )
        paths = [namespace.path for namespace in namespaces]
        if len(paths) != len(set(paths)):
            raise ValueError("Every Glue database must have exactly one owner")
        return tuple(namespaces)

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
