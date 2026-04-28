"""Unit tests for REST-API destination discovery."""

from datahub.ingestion.source.fivetran.response_models import (
    FivetranDestinationDetails,
)


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
