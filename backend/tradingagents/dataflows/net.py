"""HTTP helpers shared by the vendors."""

import logging
import re

import requests


class QueryCredentialFilter(logging.Filter):
    """Redact protocol-required credential queries even when HTTP debug logs are enabled."""

    def filter(self, record):
        message = record.getMessage()
        record.msg = re.sub(
            r"(?i)([?&](?:api_key|apikey|access_token|token)=)[^&\s\"']+", r"\1***", message
        )
        record.args = ()
        return True


def ensure_query_redaction():
    for name in ("urllib3.connectionpool", "requests.packages.urllib3.connectionpool"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, QueryCredentialFilter) for item in logger.filters):
            logger.addFilter(QueryCredentialFilter())


ensure_query_redaction()


def get_scrubbed(url: str, *, params: dict, timeout: float, secret: str, passthrough=()):
    """``requests.get`` plus ``raise_for_status``, with ``secret`` kept out of errors.

    Vendors that authenticate with a query parameter put the key in the URL, and
    requests quotes the full URL in HTTP, connection and timeout errors, so any
    log or traceback that records one would carry the key (#1324). A requests
    error is re-raised as the same class with the key replaced and nothing
    attached: no request or response (both hold the URL) and no exception chain,
    which is why this raises after the ``except`` block rather than inside it.
    Statuses in ``passthrough`` are returned for the caller to handle.
    """
    try:
        response = requests.get(url, params=params, timeout=timeout, allow_redirects=False)
        if 300 <= response.status_code < 400:
            raise requests.HTTPError(
                "Credentialed data endpoint redirected; request was not followed"
            )
        if response.status_code not in passthrough:
            response.raise_for_status()
        return response
    except requests.RequestException as exc:
        # URL encoding can transform the credential, making literal replacement unsafe.
        # Keep the exception category for routing but drop all upstream text and objects.
        error = type(exc)(
            "Credentialed data request failed; check connection, permission and quota"
        )
    raise error


def vendor_reachable(url: str, timeout: float = 5.0) -> bool:
    """Whether the vendor answers at all, for telling silence from an outage.

    A client that returns an empty result instead of raising leaves those two
    cases indistinguishable. Called only when a result is empty.
    """
    try:
        requests.head(url, timeout=timeout, allow_redirects=True)
        return True
    except requests.RequestException:
        return False
