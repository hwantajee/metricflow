from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from metricflow.dataflow.sql_table import SqlTable
from metricflow.mf_logging.formatting import indent
from metricflow.mf_logging.pretty_print import mf_pformat
from metricflow.protocols.sql_client import (
    SqlClient,
)
from metricflow.random_id import random_id
from metricflow.sql.sql_bind_parameters import SqlBindParameters
from metricflow.sql_request.sql_request_attributes import SqlRequestId

logger = logging.getLogger(__name__)


class SqlClientException(Exception):
    """Raised when an interaction with the SQL engine has an error."""

    pass


class BaseSqlClientImplementation(ABC, SqlClient):
    """Abstract implementation that other SQL clients are based on."""

    @staticmethod
    def _format_run_query_log_message(statement: str, sql_bind_parameters: SqlBindParameters) -> str:
        message = f"Running query:\n\n{indent(statement)}"
        if len(sql_bind_parameters.param_dict) > 0:
            message += f"\n\nwith parameters:\n\n{indent(mf_pformat(sql_bind_parameters.param_dict))}"
        return message

    def query(
        self,
        stmt: str,
        sql_bind_parameters: SqlBindParameters = SqlBindParameters(),
    ) -> pd.DataFrame:
        """Query statement; result expected to be data which will be returned as a DataFrame.

        Args:
            stmt: The SQL query statement to run. This should produce output via a SELECT
            sql_bind_parameters: The parameter replacement mapping for filling in
                concrete values for SQL query parameters.
        """
        start = time.time()
        SqlRequestId(f"mf_rid__{random_id()}")
        logger.info(BaseSqlClientImplementation._format_run_query_log_message(stmt, sql_bind_parameters))
        df = self._engine_specific_query_implementation(
            stmt=stmt,
            bind_params=sql_bind_parameters,
        )
        if not isinstance(df, pd.DataFrame):
            raise RuntimeError(f"Expected query to return a DataFrame, got {type(df)}")
        stop = time.time()
        logger.info(f"Finished running the query in {stop - start:.2f}s with {df.shape[0]} row(s) returned")
        return df

    def execute(  # noqa: D
        self,
        stmt: str,
        sql_bind_parameters: SqlBindParameters = SqlBindParameters(),
    ) -> None:
        start = time.time()
        logger.info(BaseSqlClientImplementation._format_run_query_log_message(stmt, sql_bind_parameters))
        self._engine_specific_execute_implementation(
            stmt=stmt,
            bind_params=sql_bind_parameters,
        )
        stop = time.time()
        logger.info(f"Finished running the query in {stop - start:.2f}s")
        return None

    def dry_run(
        self,
        stmt: str,
        sql_bind_parameters: SqlBindParameters = SqlBindParameters(),
    ) -> None:
        """Dry run statement; checks that the 'stmt' is queryable. Returns None. Raises an exception if the 'stmt' isn't queryable.

        Args:
            stmt: The SQL query statement to dry run.
            sql_bind_parameters: The parameter replacement mapping for filling in
                concrete values for SQL query parameters.
        """
        start = time.time()
        logger.info(
            f"Running dry_run of:"
            f"\n\n{indent(stmt)}\n"
            + (f"\nwith parameters: {dict(sql_bind_parameters.param_dict)}" if sql_bind_parameters.param_dict else "")
        )
        results = self._engine_specific_dry_run_implementation(stmt, sql_bind_parameters)
        stop = time.time()
        logger.info(f"Finished running the dry_run in {stop - start:.2f}s")
        return results

    @abstractmethod
    def _engine_specific_query_implementation(
        self,
        stmt: str,
        bind_params: SqlBindParameters,
    ) -> pd.DataFrame:
        """Sub-classes should implement this to query the engine."""
        pass

    @abstractmethod
    def _engine_specific_execute_implementation(
        self,
        stmt: str,
        bind_params: SqlBindParameters,
    ) -> None:
        """Sub-classes should implement this to execute a statement that doesn't return results."""
        pass

    @abstractmethod
    def _engine_specific_dry_run_implementation(self, stmt: str, bind_params: SqlBindParameters) -> None:
        """Sub-classes should implement this to check a query will run successfully without actually running the query."""
        pass

    @abstractmethod
    def create_table_from_dataframe(  # noqa: D
        self,
        sql_table: SqlTable,
        df: pd.DataFrame,
        chunk_size: Optional[int] = None,
    ) -> None:
        pass

    def create_schema(self, schema_name: str) -> None:  # noqa: D
        self.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")

    def drop_schema(self, schema_name: str, cascade: bool = True) -> None:  # noqa: D
        self.execute(f"DROP SCHEMA IF EXISTS {schema_name}{' CASCADE' if cascade else ''}")

    def close(self) -> None:  # noqa: D
        pass

    def render_bind_parameter_key(self, bind_parameter_key: str) -> str:
        """Wrap execution parameter key with syntax accepted by engine."""
        return f":{bind_parameter_key}"
