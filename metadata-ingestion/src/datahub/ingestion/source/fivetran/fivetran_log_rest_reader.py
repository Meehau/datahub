"""REST-only implementation of FivetranLogReader.

Reads everything FivetranSource needs from the Fivetran REST API instead of
querying a destination-hosted log database. See `log_reader.py` for the
contract.
"""

import logging
from typing import Dict, List, Optional

from datahub.configuration.common import AllowDenyPattern
from datahub.ingestion.source.fivetran.config import (
    FivetranAPIConfig,
    FivetranSourceReport,
)
from datahub.ingestion.source.fivetran.data_classes import (
    ColumnLineage,
    Connector,
    Job,
    TableLineage,
)
from datahub.ingestion.source.fivetran.fivetran_rest_api import FivetranAPIClient
from datahub.ingestion.source.fivetran.response_models import (
    FivetranConnectionSchemas,
    FivetranListedConnection,
)

logger = logging.getLogger(__name__)


class FivetranLogRestReader:
    """REST-API implementation of FivetranLogReader."""

    def __init__(self, api_config: FivetranAPIConfig) -> None:
        self.api_client = FivetranAPIClient(api_config)
        self._user_email_cache: Dict[str, Optional[str]] = {}
        # Discover groups lazily on first connector list.
        self._group_ids: Optional[List[str]] = None

    @property
    def fivetran_log_database(self) -> str:
        # Sentinel — REST mode doesn't read a single log DB. Destination
        # database names come from `get_destination_details_by_id` per
        # connector at URN-construction time.
        return ""

    def _discover_group_ids(self) -> List[str]:
        if self._group_ids is not None:
            return self._group_ids
        # Fivetran's `GET /v1/groups` lists all groups the API key can see.
        # We iterate connectors across every visible group.
        resp = self.api_client._session.get(
            f"{self.api_client.config.base_url}/v1/groups",
            timeout=self.api_client.config.request_timeout_sec,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != "Success":
            raise ValueError(
                f"Fivetran /v1/groups returned non-success: {payload.get('code')!r}"
            )
        self._group_ids = [item["id"] for item in payload["data"]["items"]]
        return self._group_ids

    def get_user_email(self, user_id: Optional[str]) -> Optional[str]:
        if user_id is None:
            return None
        if user_id in self._user_email_cache:
            return self._user_email_cache[user_id]
        # Populate the cache lazily by listing all users across all groups.
        # (Per-user GET would also work; bulk is cheaper for typical accounts.)
        for group_id in self._discover_group_ids():
            for user in self.api_client.list_users(group_id):
                self._user_email_cache[user.id] = user.email
        return self._user_email_cache.get(user_id)

    def get_allowed_connectors_list(
        self,
        connector_patterns: AllowDenyPattern,
        destination_patterns: AllowDenyPattern,
        report: FivetranSourceReport,
        syncs_interval: int,
    ) -> List[Connector]:
        connectors: List[Connector] = []
        for group_id in self._discover_group_ids():
            for listed in self.api_client.list_connections(group_id):
                if not connector_patterns.allowed(listed.id):
                    report.report_connectors_dropped(listed.id)
                    continue
                if not destination_patterns.allowed(listed.group_id):
                    continue
                connectors.append(self._build_connector(listed, syncs_interval))
                report.report_connectors_scanned()
        return connectors

    def _build_connector(
        self, listed: FivetranListedConnection, syncs_interval: int
    ) -> Connector:
        # Pull per-connection schema for table+column lineage.
        schemas: Optional[FivetranConnectionSchemas]
        try:
            schemas = self.api_client.get_connection_schemas(listed.id)
        except Exception as e:
            logger.warning(
                "Failed to fetch schemas for connection %s: %s", listed.id, e
            )
            schemas = None

        lineage = self._extract_table_lineage(schemas) if schemas else []

        # Pull sync history.
        try:
            sync_items = list(self.api_client.get_sync_history(listed.id))
        except Exception as e:
            logger.warning("Failed to fetch sync history for %s: %s", listed.id, e)
            sync_items = []

        jobs = [
            Job(
                job_id=item.sync_id,
                start_time=int(item.started_at.timestamp() * 1000),
                end_time=int(item.completed_at.timestamp() * 1000)
                if item.completed_at
                else int(item.started_at.timestamp() * 1000),
                status=item.status,
            )
            for item in sync_items
        ]

        return Connector(
            connector_id=listed.id,
            # REST `connections` listing endpoint doesn't expose a display
            # name, so use the connector id as a fallback.
            connector_name=listed.id,
            connector_type=listed.service,
            paused=listed.paused,
            sync_frequency=listed.sync_frequency,
            destination_id=listed.group_id,
            user_id=listed.connected_by or "",
            lineage=lineage,
            jobs=jobs,
        )

    @staticmethod
    def _extract_table_lineage(
        schemas: FivetranConnectionSchemas,
    ) -> List[TableLineage]:
        # Flatten the nested `schemas → tables → columns` structure into the
        # same TableLineage shape the DB-based reader produces.
        result: List[TableLineage] = []
        for schema_name, schema in schemas.schemas.items():
            if not schema.enabled:
                continue
            dest_schema = schema.name_in_destination
            for table_name, table in schema.tables.items():
                if not table.enabled:
                    continue
                column_lineage = [
                    ColumnLineage(
                        source_column=col_name,
                        destination_column=col.name_in_destination,
                    )
                    for col_name, col in table.columns.items()
                    if col.enabled
                ]
                result.append(
                    TableLineage(
                        source_table=f"{schema_name}.{table_name}",
                        destination_table=(
                            f"{dest_schema}.{table.name_in_destination}"
                        ),
                        column_lineage=column_lineage,
                    )
                )
        return result
