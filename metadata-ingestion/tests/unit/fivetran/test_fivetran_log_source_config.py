"""Tests for the `log_source` mode toggle and its credential-block
validation."""

from unittest.mock import patch

import pytest

from datahub.ingestion.api.common import PipelineContext
from datahub.ingestion.source.fivetran.config import FivetranSourceConfig
from datahub.ingestion.source.fivetran.fivetran import FivetranSource
from datahub.ingestion.source.fivetran.fivetran_log_api import FivetranLogAPI
from datahub.ingestion.source.fivetran.fivetran_log_rest_reader import (
    FivetranLogRestReader,
)


def _base_config(**overrides):
    cfg = {
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
    cfg.update(overrides)
    return cfg


class TestLogSourceField:
    def test_defaults_to_log_database_for_backwards_compat(self):
        cfg = FivetranSourceConfig.model_validate(_base_config())
        assert cfg.log_source == "log_database"

    def test_can_set_explicit_log_database(self):
        cfg = FivetranSourceConfig.model_validate(
            _base_config(log_source="log_database")
        )
        assert cfg.log_source == "log_database"

    def test_can_set_rest_api(self):
        cfg = FivetranSourceConfig.model_validate(
            _base_config(
                log_source="rest_api",
                api_config={"api_key": "k", "api_secret": "s"},
            )
        )
        assert cfg.log_source == "rest_api"

    def test_rest_api_mode_requires_api_config(self):
        with pytest.raises(ValueError, match="api_config"):
            FivetranSourceConfig.model_validate(_base_config(log_source="rest_api"))

    def test_invalid_value_rejected(self):
        with pytest.raises(ValueError):
            FivetranSourceConfig.model_validate(_base_config(log_source="kafka_topic"))


def test_log_database_mode_constructs_FivetranLogAPI():
    cfg_dict = _base_config()
    with patch("datahub.ingestion.source.fivetran.fivetran_log_api.create_engine"):
        src = FivetranSource.create(cfg_dict, ctx=PipelineContext(run_id="x"))
    assert isinstance(src.audit_log, FivetranLogAPI)


def test_rest_api_mode_constructs_FivetranLogRestReader():
    cfg_dict = _base_config(
        log_source="rest_api",
        api_config={"api_key": "k", "api_secret": "s"},
    )
    src = FivetranSource.create(cfg_dict, ctx=PipelineContext(run_id="x"))
    assert isinstance(src.audit_log, FivetranLogRestReader)
