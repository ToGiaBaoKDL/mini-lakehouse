from pydantic import model_validator

from mini_lakehouse.contracts.base import ContractModel
from mini_lakehouse.contracts.catalog import CatalogContract
from mini_lakehouse.contracts.domains import DomainContract
from mini_lakehouse.contracts.policies import PolicyContract
from mini_lakehouse.contracts.sources import SourceContract


class PlatformContracts(ContractModel):
    catalog: CatalogContract
    sources: tuple[SourceContract, ...]
    domains: tuple[DomainContract, ...]
    policies: tuple[PolicyContract, ...]

    @model_validator(mode="after")
    def validate_references(self) -> "PlatformContracts":
        namespace_paths = {namespace.path for namespace in self.catalog.namespaces}
        source_names = [source.name for source in self.sources]
        domain_names = [domain.name for domain in self.domains]
        policy_keys = [(policy.namespace, policy.name) for policy in self.policies]
        if len(source_names) != len(set(source_names)):
            raise ValueError("Source names must be unique")
        if len(domain_names) != len(set(domain_names)):
            raise ValueError("Domain names must be unique")
        if len(policy_keys) != len(set(policy_keys)):
            raise ValueError("Policy namespace/name pairs must be unique")

        for source in self.sources:
            if source.landing_namespace not in namespace_paths:
                raise ValueError(f"Source {source.name!r} references an unknown landing namespace")
        for domain in self.domains:
            if domain.analytics_namespace not in namespace_paths:
                raise ValueError(
                    f"Domain {domain.name!r} references an unknown analytics namespace"
                )

        source_namespaces = {source.landing_namespace for source in self.sources}
        landing_leaves = {
            path
            for path in namespace_paths
            if path[0] == "landing"
            and len(path) >= 3
            and not any(other[:-1] == path for other in namespace_paths)
        }
        if landing_leaves != source_namespaces:
            raise ValueError("Every landing source leaf must have exactly one source contract")

        domain_namespaces = {domain.analytics_namespace for domain in self.domains}
        analytics_domains = {
            path for path in namespace_paths if path[0] == "analytics" and len(path) == 2
        }
        if analytics_domains != domain_namespaces:
            raise ValueError(
                "Every direct analytics namespace must have exactly one domain contract"
            )

        attachments: set[tuple[str, tuple[str, ...], str]] = set()
        for policy in self.policies:
            if policy.namespace not in namespace_paths:
                raise ValueError(f"Policy {policy.name!r} is stored in an unknown namespace")
            for target in policy.targets:
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
