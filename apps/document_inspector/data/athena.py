"""Document Inspector's read-only Athena boundary."""

from collections.abc import Mapping
from typing import Any

import awswrangler as wr
import boto3
import pandas as pd


class AthenaReader:
    def __init__(
        self,
        *,
        workgroup: str,
        s3_output: str,
        region_name: str,
        profile_name: str | None = None,
    ) -> None:
        self._workgroup = workgroup
        self._s3_output = s3_output
        self._session = boto3.Session(
            region_name=region_name,
            profile_name=profile_name,
        )

    def query(
        self,
        sql: str,
        *,
        database: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> pd.DataFrame:
        return wr.athena.read_sql_query(
            sql=sql,
            database=database,
            params=dict(parameters or {}),
            paramstyle="named",
            ctas_approach=False,
            unload_approach=False,
            dtype_backend="pyarrow",
            workgroup=self._workgroup,
            s3_output=self._s3_output,
            boto3_session=self._session,
        )
