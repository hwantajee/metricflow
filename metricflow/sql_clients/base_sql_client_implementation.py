from __future__ import annotations

import logging
import textwrap
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

import jinja2
import pandas as pd
from dbt_semantic_interfaces.pretty_print import pformat_big_objects

from metricflow.dataflow.sql_table import SqlTable
from metricflow.logging.formatting import indent_log_line
from metricflow.protocols.sql_client import (
    SqlClient,
    SqlEngineAttributes,
)
from metricflow.protocols.sql_request import SqlJsonTag, SqlRequestId, SqlRequestTagSet
from metricflow.random_id import random_id
from metricflow.sql.sql_bind_parameters import SqlBindParameters
from metricflow.sql_clients.sql_statement_metadata import CombinedSqlTags, SqlStatementCommentMetadata

logger = logging.getLogger(__name__)


class SqlClientException(Exception):
    """Raised when an interaction with the SQL engine has an error."""

    pass


class BaseSqlClientImplementation(ABC, SqlClient):
    """Abstract implementation that other SQL clients are based on."""

    def generate_health_check_tests(self, schema_name: str) -> List[Tuple[str, Any]]:  # type: ignore
        """List of base health checks we want to perform."""
        table_name = "health_report"
        return [
            ("SELECT 1", lambda: self.execute("SELECT 1")),
            (f"Create schema '{schema_name}'", lambda: self.create_schema(schema_name)),
            (
                f"Create table '{schema_name}.{table_name}' with a SELECT",
                lambda: self.create_table_as_select(
                    SqlTable(schema_name=schema_name, table_name="health_report"), "SELECT 'test' AS test_col"
                ),
            ),
            (
                f"Drop table '{schema_name}.{table_name}'",
                lambda: self.drop_table(SqlTable(schema_name=schema_name, table_name="health_report")),
            ),
        ]

    def health_checks(self, schema_name: str) -> Dict[str, Dict[str, str]]:
        """Perform health checks."""
        checks_to_run = self.generate_health_check_tests(schema_name)
        results: Dict[str, Dict[str, str]] = {}

        for step, check in checks_to_run:
            status = "SUCCESS"
            err_string = ""
            try:
                resp = check()
                logger.info(f"Health Check Item {step}: succeeded" + f" with response {str(resp)}" if resp else None)
            except Exception as e:
                status = "FAIL"
                err_string = str(e)
                logger.error(f"Health Check Item {step}: failed with error {err_string}")

            results[f"{type(self).__name__.lower()} - {step}"] = {
                "status": status,
                "error_message": err_string,
            }

        return results

    @staticmethod
    def _format_run_query_log_message(statement: str, sql_bind_parameters: SqlBindParameters) -> str:
        message = f"Running query:\n\n{indent_log_line(statement)}"
        if len(sql_bind_parameters.param_dict) > 0:
            message += (
                f"\n"
                f"\n"
                f"with parameters:\n"
                f"\n"
                f"{indent_log_line(pformat_big_objects(sql_bind_parameters.param_dict))}"
            )
        return message

    def query(
        self,
        stmt: str,
        sql_bind_parameters: SqlBindParameters = SqlBindParameters(),
        extra_tags: SqlJsonTag = SqlJsonTag(),
    ) -> pd.DataFrame:
        """Query statement; result expected to be data which will be returned as a DataFrame.

        Args:
            stmt: The SQL query statement to run. This should produce output via a SELECT
            sql_bind_parameters: The parameter replacement mapping for filling in
                concrete values for SQL query parameters.
        """
        start = time.time()
        request_id = SqlRequestId(f"mf_rid__{random_id()}")
        combined_tags = BaseSqlClientImplementation._consolidate_tags(json_tags=extra_tags, request_id=request_id)
        statement = SqlStatementCommentMetadata.add_tag_metadata_as_comment(
            sql_statement=stmt, combined_tags=combined_tags
        )
        logger.info(BaseSqlClientImplementation._format_run_query_log_message(statement, sql_bind_parameters))
        df = self._engine_specific_query_implementation(
            stmt=statement,
            bind_params=sql_bind_parameters,
            system_tags=combined_tags.system_tags,
            extra_tags=combined_tags.extra_tag,
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
        extra_tags: SqlJsonTag = SqlJsonTag(),
    ) -> None:
        start = time.time()
        request_id = SqlRequestId(f"mf_rid__{random_id()}")
        combined_tags = BaseSqlClientImplementation._consolidate_tags(json_tags=extra_tags, request_id=request_id)
        statement = SqlStatementCommentMetadata.add_tag_metadata_as_comment(
            sql_statement=stmt, combined_tags=combined_tags
        )
        logger.info(BaseSqlClientImplementation._format_run_query_log_message(statement, sql_bind_parameters))
        self._engine_specific_execute_implementation(
            stmt=statement,
            bind_params=sql_bind_parameters,
            system_tags=combined_tags.system_tags,
            extra_tags=combined_tags.extra_tag,
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
            f"\n\n{indent_log_line(stmt)}\n"
            + (f"\nwith parameters: {dict(sql_bind_parameters.param_dict)}" if sql_bind_parameters.param_dict else "")
        )
        results = self._engine_specific_dry_run_implementation(stmt, sql_bind_parameters)
        stop = time.time()
        logger.info(f"Finished running the dry_run in {stop - start:.2f}s")
        return results

    @property
    @abstractmethod
    def sql_engine_attributes(self) -> SqlEngineAttributes:
        """Struct of SQL engine attributes.

        This is used by MetricFlow for things like SQL rendering and safe use of multi-threading.
        """
        raise NotImplementedError

    @abstractmethod
    def _engine_specific_query_implementation(
        self,
        stmt: str,
        bind_params: SqlBindParameters,
        system_tags: SqlRequestTagSet = SqlRequestTagSet(),
        extra_tags: SqlJsonTag = SqlJsonTag(),
    ) -> pd.DataFrame:
        """Sub-classes should implement this to query the engine."""
        pass

    @abstractmethod
    def _engine_specific_execute_implementation(
        self,
        stmt: str,
        bind_params: SqlBindParameters,
        system_tags: SqlRequestTagSet = SqlRequestTagSet(),
        extra_tags: SqlJsonTag = SqlJsonTag(),
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

    def table_exists(self, sql_table: SqlTable) -> bool:  # noqa: D
        return sql_table.table_name in self.list_tables(sql_table.schema_name)

    def create_table_as_select(  # noqa: D
        self,
        sql_table: SqlTable,
        select_query: str,
        sql_bind_parameters: SqlBindParameters = SqlBindParameters(),
    ) -> None:
        self.execute(
            jinja2.Template(
                textwrap.dedent(
                    """\
                    CREATE TABLE {{ sql_table }} AS
                      {{ select_query | indent(2) }}
                    """
                ),
                undefined=jinja2.StrictUndefined,
            ).render(
                sql_table=sql_table.sql,
                select_query=select_query,
            ),
            sql_bind_parameters=sql_bind_parameters,
        )

    def create_schema(self, schema_name: str) -> None:  # noqa: D
        self.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")

    def drop_schema(self, schema_name: str, cascade: bool = True) -> None:  # noqa: D
        self.execute(f"DROP SCHEMA IF EXISTS {schema_name}{' CASCADE' if cascade else ''}")

    def drop_table(self, sql_table: SqlTable) -> None:  # noqa: D
        self.execute(f"DROP TABLE IF EXISTS {sql_table.sql}")

    def close(self) -> None:  # noqa: D
        pass

    def render_bind_parameter_key(self, bind_parameter_key: str) -> str:
        """Wrap execution parameter key with syntax accepted by engine."""
        return f":{bind_parameter_key}"

    @staticmethod
    def _consolidate_tags(json_tags: SqlJsonTag, request_id: SqlRequestId) -> CombinedSqlTags:
        """Consolidates json tags and request ID into a single set of tags."""
        return CombinedSqlTags(
            system_tags=SqlRequestTagSet().add_request_id(request_id=request_id),
            extra_tag=json_tags,
        )
