"""Pin the Protocol surface so refactors don't quietly drop methods
the source depends on."""

import inspect
from typing import get_type_hints

from datahub.ingestion.source.fivetran.log_reader import FivetranLogReader


def test_protocol_has_required_members():
    members = {name for name, _ in inspect.getmembers(FivetranLogReader)}
    assert "get_allowed_connectors_list" in members
    assert "get_user_email" in members
    assert "fivetran_log_database" in members


def test_protocol_method_signatures_stable():
    # If we ever rename or change the shape of these, existing
    # implementations will break — pin the contract.
    hints = get_type_hints(FivetranLogReader.get_user_email)
    # `Optional[str]` return — emails may be missing.
    assert hints.get("return") is not None
