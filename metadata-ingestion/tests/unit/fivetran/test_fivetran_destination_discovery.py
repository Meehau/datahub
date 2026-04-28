"""Unit tests for REST-API destination discovery."""

from unittest.mock import MagicMock, patch

import pydantic
import pytest

from datahub.ingestion.source.fivetran.config import (
    FivetranAPIConfig,
    FivetranSourceConfig,
)
from datahub.ingestion.source.fivetran.fivetran_rest_api import FivetranAPIClient
from datahub.ingestion.source.fivetran.response_models import (
    FivetranDestinationDetails,
)


def _make_client() -> FivetranAPIClient:
    return FivetranAPIClient(FivetranAPIConfig(api_key="key", api_secret="secret"))


class TestFivetranDestinationDetailsParsing:
    """Pin the response model against fields we actually consume.
    Unknown fields must be ignored so future Fivetran additions don't break parsing."""

    def test_managed_data_lake_response_parses(self):
        # Real-shape response for an MDL destination with Glue toggle on.
        raw = {
            "id": "interval_unconstitutional",
            "service": "managed_data_lake",
            "region": "AWS_US_EAST_1",
            "group_id": "g_123",
            "setup_status": "CONNECTED",
            "config": {
                "bucket": "my-lake-bucket",
                "prefix_path": "fivetran",
                "region": "us-east-1",
                "table_format": "ICEBERG",
            },
            # extra unknown field — must be ignored
            "future_field_we_dont_know_about": {"x": 1},
        }
        details = FivetranDestinationDetails.model_validate(raw)
        assert details.id == "interval_unconstitutional"
        assert details.service == "managed_data_lake"
        assert details.config.bucket == "my-lake-bucket"
        assert details.config.region == "us-east-1"

    def test_snowflake_response_parses_with_partial_config(self):
        # Snowflake destinations expose different config keys; the model
        # must tolerate config fields it doesn't know about.
        raw = {
            "id": "snowflake_dest_1",
            "service": "snowflake",
            "region": "AWS_US_WEST_2",
            "group_id": "g_456",
            "setup_status": "CONNECTED",
            "config": {
                "host": "abc.snowflakecomputing.com",
                "port": 443,
                "database": "DATAHUB_COMMUNITY",
                "user": "fivetran_user",
            },
        }
        details = FivetranDestinationDetails.model_validate(raw)
        assert details.service == "snowflake"
        assert details.config.database == "DATAHUB_COMMUNITY"
        # bucket is None for non-MDL destinations
        assert details.config.bucket is None

    def test_unknown_top_level_field_does_not_break(self):
        raw = {
            "id": "x",
            "service": "snowflake",
            "region": "X",
            "group_id": "g",
            "setup_status": "CONNECTED",
            "config": {},
            "some_new_field_added_by_fivetran": [1, 2, 3],
        }
        FivetranDestinationDetails.model_validate(raw)  # must not raise


class TestGetDestinationDetailsByID:
    """`get_destination_details_by_id` is the per-destination REST lookup.
    Must: (1) parse the success envelope, (2) cache per id, (3) raise on
    non-success codes so callers can decide whether to fall back."""

    def test_success_response_parses_and_caches(self):
        client = _make_client()
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "code": "Success",
            "data": {
                "id": "dest_1",
                "service": "managed_data_lake",
                "region": "AWS_US_EAST_1",
                "group_id": "g",
                "setup_status": "CONNECTED",
                "config": {"bucket": "b"},
            },
        }
        fake_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", return_value=fake_resp) as mocked:
            first = client.get_destination_details_by_id("dest_1")
            second = client.get_destination_details_by_id("dest_1")

        assert first.service == "managed_data_lake"
        assert first is second  # same cached instance
        assert mocked.call_count == 1  # only one HTTP call

    def test_distinct_ids_each_make_one_call(self):
        client = _make_client()

        def _resp(dest_id: str) -> MagicMock:
            r = MagicMock()
            r.json.return_value = {
                "code": "Success",
                "data": {
                    "id": dest_id,
                    "service": "snowflake",
                    "region": "X",
                    "group_id": "g",
                    "setup_status": "CONNECTED",
                    "config": {},
                },
            }
            r.raise_for_status = MagicMock()
            return r

        with patch.object(
            client._session,
            "get",
            side_effect=lambda url, **_: _resp(url.rsplit("/", 1)[-1]),
        ) as mocked:
            client.get_destination_details_by_id("a")
            client.get_destination_details_by_id("b")
            client.get_destination_details_by_id("a")  # cache hit

        assert mocked.call_count == 2

    def test_non_success_code_raises_value_error(self):
        client = _make_client()
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"code": "NotFound", "message": "no such id"}
        fake_resp.raise_for_status = MagicMock()
        with (
            patch.object(client._session, "get", return_value=fake_resp),
            pytest.raises(ValueError, match="NotFound"),
        ):
            client.get_destination_details_by_id("missing")

    def test_invalid_payload_raises_validation_error(self):
        client = _make_client()
        fake_resp = MagicMock()
        # Missing required field `id`.
        fake_resp.json.return_value = {
            "code": "Success",
            "data": {"service": "snowflake", "region": "X", "config": {}},
        }
        fake_resp.raise_for_status = MagicMock()
        with (
            patch.object(client._session, "get", return_value=fake_resp),
            pytest.raises(pydantic.ValidationError),
        ):
            client.get_destination_details_by_id("dest_x")


class TestUseDestinationDiscoveryFlag:
    def test_defaults_to_false(self):
        # Backwards-compat: the flag must default off so existing recipes
        # don't change behaviour.
        cfg = FivetranSourceConfig.model_validate(
            {
                "fivetran_log_config": {
                    "destination_platform": "snowflake",
                    "snowflake_destination_config": {
                        "account_id": "x",
                        "username": "u",
                        "password": "p",
                        "warehouse": "w",
                        "database": "d",
                        "log_schema": "s",
                    },
                },
            }
        )
        assert cfg.use_destination_discovery is False

    def test_can_enable(self):
        cfg = FivetranSourceConfig.model_validate(
            {
                "use_destination_discovery": True,
                "fivetran_log_config": {
                    "destination_platform": "snowflake",
                    "snowflake_destination_config": {
                        "account_id": "x",
                        "username": "u",
                        "password": "p",
                        "warehouse": "w",
                        "database": "d",
                        "log_schema": "s",
                    },
                },
                "api_config": {
                    "api_key": "k",
                    "api_secret": "s",
                },
            }
        )
        assert cfg.use_destination_discovery is True
