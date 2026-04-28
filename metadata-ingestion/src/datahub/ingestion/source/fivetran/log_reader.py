"""Protocol for the log-reading subsystem of the Fivetran source.

`FivetranSource` consumes only the methods/properties listed here. Two
implementations exist:
- `FivetranLogAPI` (database-based; default, backward-compatible)
- `FivetranLogRestReader` (REST-API-based; opt-in via `log_source: rest_api`)

Adding methods here is a contract change — every implementation must
implement them. Prefer narrow, source-facing methods over leaking the
underlying access mechanism.
"""

from typing import List, Optional, Protocol, runtime_checkable

from datahub.configuration.common import AllowDenyPattern
from datahub.ingestion.api.source import SourceReport
from datahub.ingestion.source.fivetran.data_classes import Connector


@runtime_checkable
class FivetranLogReader(Protocol):
    """Source-facing interface for reading Fivetran log data."""

    @property
    def fivetran_log_database(self) -> str:
        """Default destination database name used when no per-destination
        override is provided. For REST-mode readers this is the value
        that would seed `destination_details.database` if discovery is
        also disabled — typically empty string or a sentinel.
        """
        ...

    def get_allowed_connectors_list(
        self,
        connector_patterns: AllowDenyPattern,
        destination_patterns: AllowDenyPattern,
        report: SourceReport,
        syncs_interval: int,
    ) -> List[Connector]:
        """Return all connectors that pass the supplied filters, with
        their lineage and run history populated.
        """
        ...

    def get_user_email(self, user_id: Optional[str]) -> Optional[str]:
        """Return the email for a given user_id, or None if not known."""
        ...
