"""Tests for the REST-only log reader.

Each test is driven by sample JSON captured in the prerequisites step;
copy the saved /tmp/fv_*.json contents into the inline fixtures or load
them from `tests/unit/fivetran/fixtures/` if your fixtures get large.
"""

from datahub.ingestion.source.fivetran.response_models import (
    FivetranConnectionSchemas,
    FivetranListConnectionsResponse,
    FivetranListUsersResponse,
    FivetranSyncHistoryResponse,
)


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
