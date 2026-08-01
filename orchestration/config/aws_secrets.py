"""Use AWS-native stores for Airflow connections and runtime variables."""

from airflow.providers.amazon.aws.secrets.secrets_manager import SecretsManagerBackend
from airflow.providers.amazon.aws.secrets.systems_manager import SystemsManagerParameterStoreBackend
from airflow.secrets import BaseSecretsBackend


class AwsSecretsBackend(BaseSecretsBackend):
    """Delegate credentials to Secrets Manager and configuration to Parameter Store."""

    def __init__(
        self,
        *,
        connections_prefix: str,
        variables_prefix: str,
        region_name: str | None = None,
    ) -> None:
        self._connections = SecretsManagerBackend(
            connections_prefix=connections_prefix,
            region_name=region_name,
        )
        self._variables = SystemsManagerParameterStoreBackend(
            variables_prefix=variables_prefix,
            region_name=region_name,
        )

    def get_conn_value(self, conn_id: str, team_name: str | None = None) -> str | None:
        return self._connections.get_conn_value(conn_id, team_name)

    def get_variable(self, key: str, team_name: str | None = None) -> str | None:
        return self._variables.get_variable(key, team_name)
