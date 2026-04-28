"""Tests for the REST-only log reader.

Each test is driven by sample JSON captured in the prerequisites step;
copy the saved /tmp/fv_*.json contents into the inline fixtures or load
them from `tests/unit/fivetran/fixtures/` if your fixtures get large.
"""

from unittest.mock import MagicMock, patch

from datahub.configuration.common import AllowDenyPattern
from datahub.ingestion.source.fivetran.config import (
    FivetranAPIConfig,
    FivetranSourceReport,
)
from datahub.ingestion.source.fivetran.fivetran_log_rest_reader import (
    FivetranLogRestReader,
)
from datahub.ingestion.source.fivetran.fivetran_rest_api import FivetranAPIClient
from datahub.ingestion.source.fivetran.response_models import (
    FivetranColumn,
    FivetranConnectionSchemas,
    FivetranListConnectionsResponse,
    FivetranListedConnection,
    FivetranListUsersResponse,
    FivetranSchema,
    FivetranSyncHistoryResponse,
    FivetranTable,
)


def _make_client():
    return FivetranAPIClient(FivetranAPIConfig(api_key="k", api_secret="s"))


class TestResponseModelParsing:
    def test_list_connections_paginated(self):
        raw = {
            "code": "Success",
            "data": {
                "items": [
                    {
                        "id": "calendar_elected",
                        "schema": "postgres_public",
                        "service": "postgres",
                        "paused": False,
                        "sync_frequency": 1440,
                        "group_id": "g1",
                        "connected_by": "reapply_phone",
                    }
                ],
                "next_cursor": "abc123",
            },
        }
        parsed = FivetranListConnectionsResponse.model_validate(raw["data"])
        assert len(parsed.items) == 1
        assert parsed.items[0].id == "calendar_elected"
        assert parsed.items[0].service == "postgres"
        assert parsed.next_cursor == "abc123"

    def test_connection_schemas_with_columns(self):
        raw = {
            "schemas": {
                "public": {
                    "name_in_destination": "postgres_public",
                    "enabled": True,
                    "tables": {
                        "employee": {
                            "name_in_destination": "employee",
                            "enabled": True,
                            "columns": {
                                "id": {
                                    "name_in_destination": "id",
                                    "enabled": True,
                                    "is_primary_key": True,
                                },
                                "name": {
                                    "name_in_destination": "name",
                                    "enabled": True,
                                    "is_primary_key": False,
                                },
                            },
                        }
                    },
                }
            }
        }
        parsed = FivetranConnectionSchemas.model_validate(raw)
        assert "public" in parsed.schemas
        schema = parsed.schemas["public"]
        assert schema.name_in_destination == "postgres_public"
        assert "employee" in schema.tables
        table = schema.tables["employee"]
        assert table.name_in_destination == "employee"
        assert len(table.columns) == 2
        assert table.columns["id"].is_primary_key is True

    def test_list_users(self):
        raw = {
            "items": [
                {
                    "id": "reapply_phone",
                    "email": "shubham@example.com",
                    "given_name": "Shubham",
                    "family_name": "Jagtap",
                }
            ],
            "next_cursor": None,
        }
        parsed = FivetranListUsersResponse.model_validate(raw)
        assert parsed.items[0].id == "reapply_phone"
        assert parsed.items[0].email == "shubham@example.com"

    def test_sync_history(self):
        raw = {
            "items": [
                {
                    "sync_id": "4c9a03d6-eded-4422-a46a-163266e58243",
                    "started_at": "2023-09-20T06:37:32.606Z",
                    "completed_at": "2023-09-20T06:38:05.056Z",
                    "status": "SUCCESSFUL",
                    "message": "{}",
                }
            ],
            "next_cursor": None,
        }
        parsed = FivetranSyncHistoryResponse.model_validate(raw)
        assert parsed.items[0].sync_id.startswith("4c9a")
        assert parsed.items[0].status == "SUCCESSFUL"

    def test_unknown_fields_are_tolerated(self):
        # Future-proof against Fivetran adding new fields.
        raw = {
            "items": [
                {
                    "id": "x",
                    "schema": "s",
                    "service": "postgres",
                    "paused": False,
                    "sync_frequency": 1,
                    "group_id": "g",
                    "connected_by": None,
                    "field_we_dont_know_about": [1, 2, 3],
                }
            ]
        }
        FivetranListConnectionsResponse.model_validate(raw)  # must not raise


class TestListConnections:
    def test_single_page(self):
        client = _make_client()
        resp = MagicMock()
        resp.json.return_value = {
            "code": "Success",
            "data": {
                "items": [
                    {
                        "id": "c1",
                        "schema": "s",
                        "service": "postgres",
                        "paused": False,
                        "sync_frequency": 1440,
                        "group_id": "g",
                        "connected_by": None,
                    }
                ],
                "next_cursor": None,
            },
        }
        resp.raise_for_status = MagicMock()
        with patch.object(client._session, "get", return_value=resp):
            result = list(client.list_connections(group_id="g"))
        assert len(result) == 1
        assert result[0].id == "c1"

    def test_paginates_until_cursor_none(self):
        client = _make_client()

        def _page(_url, **kwargs):
            cursor = kwargs.get("params", {}).get("cursor")
            r = MagicMock()
            if cursor is None:
                r.json.return_value = {
                    "code": "Success",
                    "data": {
                        "items": [
                            {
                                "id": "p1",
                                "schema": "s",
                                "service": "postgres",
                                "paused": False,
                                "sync_frequency": 1,
                                "group_id": "g",
                            }
                        ],
                        "next_cursor": "next1",
                    },
                }
            elif cursor == "next1":
                r.json.return_value = {
                    "code": "Success",
                    "data": {
                        "items": [
                            {
                                "id": "p2",
                                "schema": "s",
                                "service": "postgres",
                                "paused": False,
                                "sync_frequency": 1,
                                "group_id": "g",
                            }
                        ],
                        "next_cursor": None,
                    },
                }
            r.raise_for_status = MagicMock()
            return r

        with patch.object(client._session, "get", side_effect=_page) as mocked:
            result = list(client.list_connections(group_id="g"))
        assert [c.id for c in result] == ["p1", "p2"]
        assert mocked.call_count == 2


class TestGetConnectionSchemas:
    def test_returns_parsed_schemas(self):
        client = _make_client()
        resp = MagicMock()
        resp.json.return_value = {
            "code": "Success",
            "data": {
                "schemas": {
                    "public": {
                        "name_in_destination": "postgres_public",
                        "enabled": True,
                        "tables": {
                            "employee": {
                                "name_in_destination": "employee",
                                "enabled": True,
                                "columns": {
                                    "id": {
                                        "name_in_destination": "id",
                                        "enabled": True,
                                        "is_primary_key": True,
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }
        resp.raise_for_status = MagicMock()
        with patch.object(client._session, "get", return_value=resp):
            result = client.get_connection_schemas("conn_x")
        assert "public" in result.schemas
        assert "employee" in result.schemas["public"].tables


class TestListUsers:
    def test_paginates(self):
        client = _make_client()

        def _page(_url, **kwargs):
            cursor = kwargs.get("params", {}).get("cursor")
            r = MagicMock()
            if cursor is None:
                r.json.return_value = {
                    "code": "Success",
                    "data": {
                        "items": [
                            {"id": "u1", "email": "u1@x"},
                        ],
                        "next_cursor": "n",
                    },
                }
            else:
                r.json.return_value = {
                    "code": "Success",
                    "data": {
                        "items": [{"id": "u2", "email": "u2@x"}],
                        "next_cursor": None,
                    },
                }
            r.raise_for_status = MagicMock()
            return r

        with patch.object(client._session, "get", side_effect=_page):
            users = list(client.list_users(group_id="g"))
        assert {u.id for u in users} == {"u1", "u2"}


class TestGetSyncHistory:
    def test_paginated(self):
        client = _make_client()

        def _page(_url, **kwargs):
            cursor = kwargs.get("params", {}).get("cursor")
            r = MagicMock()
            if cursor is None:
                r.json.return_value = {
                    "code": "Success",
                    "data": {
                        "items": [
                            {
                                "sync_id": "s1",
                                "started_at": "2023-09-20T06:37:32.606Z",
                                "completed_at": "2023-09-20T06:38:05.056Z",
                                "status": "SUCCESSFUL",
                                "message": "{}",
                            }
                        ],
                        "next_cursor": None,
                    },
                }
            r.raise_for_status = MagicMock()
            return r

        with patch.object(client._session, "get", side_effect=_page):
            items = list(client.get_sync_history("c1"))
        assert items[0].sync_id == "s1"
        assert items[0].status == "SUCCESSFUL"


def _make_reader(api_client):
    reader = FivetranLogRestReader.__new__(FivetranLogRestReader)
    reader.api_client = api_client
    reader._user_email_cache = {}
    reader._group_ids = ["g1"]  # populated in __init__ in real construction
    return reader


class TestGetAllowedConnectorsListRest:
    def test_assembles_connector_with_table_lineage_from_schemas(self):
        api = MagicMock()
        api.list_connections.return_value = iter(
            [
                FivetranListedConnection(
                    id="c1",
                    schema="postgres_public",
                    service="postgres",
                    paused=False,
                    sync_frequency=1440,
                    group_id="g1",
                    connected_by="u1",
                )
            ]
        )
        api.get_connection_schemas.return_value = FivetranConnectionSchemas(
            schemas={
                "public": FivetranSchema(
                    name_in_destination="postgres_public",
                    enabled=True,
                    tables={
                        "employee": FivetranTable(
                            name_in_destination="employee",
                            enabled=True,
                            columns={
                                "id": FivetranColumn(
                                    name_in_destination="id",
                                    enabled=True,
                                    is_primary_key=True,
                                )
                            },
                        )
                    },
                )
            }
        )
        api.get_sync_history.return_value = iter([])

        reader = _make_reader(api)

        connectors = reader.get_allowed_connectors_list(
            connector_patterns=AllowDenyPattern.allow_all(),
            destination_patterns=AllowDenyPattern.allow_all(),
            report=FivetranSourceReport(),
            syncs_interval=7,
        )

        assert len(connectors) == 1
        c = connectors[0]
        assert c.connector_id == "c1"
        assert c.connector_type == "postgres"
        # Table lineage flattened from schemas:
        assert len(c.lineage) == 1
        tl = c.lineage[0]
        assert tl.source_table == "public.employee"
        assert tl.destination_table == "postgres_public.employee"
        # Column lineage:
        assert len(tl.column_lineage) == 1
        cl = tl.column_lineage[0]
        assert cl.source_column == "id"
        assert cl.destination_column == "id"

    def test_filters_disabled_schemas_and_tables(self):
        # Disabled schemas/tables/columns must not appear in the lineage.
        api = MagicMock()
        api.list_connections.return_value = iter(
            [
                FivetranListedConnection(
                    id="c1",
                    schema="x",
                    service="postgres",
                    paused=False,
                    sync_frequency=1,
                    group_id="g1",
                )
            ]
        )
        api.get_connection_schemas.return_value = FivetranConnectionSchemas(
            schemas={
                "public": FivetranSchema(
                    name_in_destination="public",
                    enabled=False,  # disabled
                    tables={"t": FivetranTable(name_in_destination="t")},
                ),
                "ok": FivetranSchema(
                    name_in_destination="ok",
                    enabled=True,
                    tables={
                        "disabled_t": FivetranTable(
                            name_in_destination="disabled_t", enabled=False
                        ),
                        "ok_t": FivetranTable(name_in_destination="ok_t"),
                    },
                ),
            }
        )
        api.get_sync_history.return_value = iter([])

        reader = _make_reader(api)
        connectors = reader.get_allowed_connectors_list(
            connector_patterns=AllowDenyPattern.allow_all(),
            destination_patterns=AllowDenyPattern.allow_all(),
            report=FivetranSourceReport(),
            syncs_interval=7,
        )
        c = connectors[0]
        # Only the enabled schema's enabled table is present
        assert {tl.source_table for tl in c.lineage} == {"ok.ok_t"}

    def test_connector_filter_drops_excluded(self):
        api = MagicMock()
        api.list_connections.return_value = iter(
            [
                FivetranListedConnection(
                    id="keep",
                    schema="s",
                    service="postgres",
                    paused=False,
                    sync_frequency=1,
                    group_id="g1",
                ),
                FivetranListedConnection(
                    id="drop",
                    schema="s",
                    service="postgres",
                    paused=False,
                    sync_frequency=1,
                    group_id="g1",
                ),
            ]
        )
        api.get_connection_schemas.return_value = FivetranConnectionSchemas()
        api.get_sync_history.return_value = iter([])
        reader = _make_reader(api)
        connectors = reader.get_allowed_connectors_list(
            connector_patterns=AllowDenyPattern(allow=["keep"]),
            destination_patterns=AllowDenyPattern.allow_all(),
            report=FivetranSourceReport(),
            syncs_interval=7,
        )
        assert {c.connector_id for c in connectors} == {"keep"}
