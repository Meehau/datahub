import logging
from typing import Dict, Iterator, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from datahub.ingestion.source.fivetran.config import (
    FivetranAPIConfig,
)
from datahub.ingestion.source.fivetran.response_models import (
    FivetranConnectionDetails,
    FivetranDestinationDetails,
    FivetranListConnectionsResponse,
    FivetranListedConnection,
)

logger = logging.getLogger(__name__)


# Retry configuration constants
RETRY_MAX_TIMES = 3
RETRY_STATUS_CODES = [429, 500, 502, 503, 504]
RETRY_BACKOFF_FACTOR = 1
RETRY_ALLOWED_METHODS = ["GET"]


class FivetranAPIClient:
    """Client for interacting with the Fivetran REST API."""

    def __init__(self, config: FivetranAPIConfig) -> None:
        self.config = config
        self._session = self._create_session()
        self._destination_cache: Dict[str, FivetranDestinationDetails] = {}

    def _create_session(self) -> requests.Session:
        """
        Create a session with retry logic and basic authentication
        """
        requests_session = requests.Session()

        # Configure retry strategy for transient failures
        retry_strategy = Retry(
            total=RETRY_MAX_TIMES,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_CODES,
            allowed_methods=RETRY_ALLOWED_METHODS,
            raise_on_status=True,
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        requests_session.mount("http://", adapter)
        requests_session.mount("https://", adapter)

        # Set up basic authentication
        requests_session.auth = (
            self.config.api_key.get_secret_value(),
            self.config.api_secret.get_secret_value(),
        )
        requests_session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        return requests_session

    def get_connection_details_by_id(
        self, connection_id: str
    ) -> FivetranConnectionDetails:
        """
        Get details for a specific connection from the Fivetran API.

        Args:
            connection_id: The Fivetran connection ID to fetch details for.

        Returns:
            FivetranConnectionDetails: The parsed connection details.

        Raises:
            requests.HTTPError: If the API returns an HTTP error.
            ValueError: If the response is missing required fields or has non-success code.
            pydantic.ValidationError: If the response data doesn't match the expected schema.
        """
        response = self._session.get(
            f"{self.config.base_url}/v1/connections/{connection_id}",
            timeout=self.config.request_timeout_sec,
        )

        # Check for HTTP errors and raise HTTPError if needed
        response.raise_for_status()

        response_json = response.json()

        # Check response code at top level (e.g., "code": "Success")
        response_code = response_json.get("code")
        if response_code and response_code.lower() != "success":
            raise ValueError(
                f"Response code is not 'success' for connection_id {connection_id}. "
                f"Code: {response_code}, Response: {response_json}"
            )

        data = response_json.get("data", {})
        if not data:
            raise ValueError(
                f"Response missing 'data' field for connection_id {connection_id}"
            )

        # Use Pydantic's built-in parsing with extra="ignore" configured in the model
        # ValidationError will propagate if required fields are missing
        return FivetranConnectionDetails(**data)

    def get_destination_details_by_id(
        self, destination_id: str
    ) -> FivetranDestinationDetails:
        """Fetch destination metadata from `GET /v1/destinations/{id}`.

        Cached per instance so repeated lookups for the same destination during
        a single ingest issue at most one HTTP call.

        Raises:
            requests.HTTPError: transport-level failure (after retries).
            ValueError: API returned a non-Success code.
            pydantic.ValidationError: response shape doesn't match expectations.
        """
        cached = self._destination_cache.get(destination_id)
        if cached is not None:
            return cached

        response = self._session.get(
            f"{self.config.base_url}/v1/destinations/{destination_id}",
            timeout=self.config.request_timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()

        code = payload.get("code")
        if code != "Success":
            raise ValueError(
                f"Fivetran API returned non-success code {code!r} for "
                f"destination {destination_id!r}: {payload.get('message')}"
            )

        details = FivetranDestinationDetails.model_validate(payload["data"])
        self._destination_cache[destination_id] = details
        return details

    def list_connections(
        self, group_id: str, page_size: int = 100
    ) -> Iterator[FivetranListedConnection]:
        """Yield every connection in a Fivetran group, traversing pagination.

        Cursor-based pagination follows Fivetran's standard contract: `data.next_cursor`
        is the token for the next page; `None` ends iteration.
        """
        cursor: Optional[str] = None
        while True:
            params: Dict[str, object] = {"limit": page_size}
            if cursor is not None:
                params["cursor"] = cursor
            resp = self._session.get(
                f"{self.config.base_url}/v1/groups/{group_id}/connections",
                params=params,
                timeout=self.config.request_timeout_sec,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("code") != "Success":
                raise ValueError(
                    f"Fivetran API returned non-success code "
                    f"{payload.get('code')!r} for list_connections "
                    f"(group_id={group_id})"
                )
            page = FivetranListConnectionsResponse.model_validate(payload["data"])
            yield from page.items
            cursor = page.next_cursor
            if cursor is None:
                return
