"""tests/providers/test_sedif.py"""

import re
from datetime import date

import pytest
from aioresponses import aioresponses
from yarl import URL

from custom_components.aquawatch.providers.exceptions import AuthError
from custom_components.aquawatch.providers.sedif import (
    _AURA_URL,
    _BASE_URL,
    _LOGIN_URL,
    SedifProvider,
)

AURA_URL_RE = re.compile(re.escape(_AURA_URL))

_LOGIN_HTML = """
<html><script>
var data = {"fwuid":"abc123","APPLICATION@markup://siteforce:loginApp2":"hash456"};
</script></html>
"""

_LOGIN_HTML_NO_FWUID = "<html><body>nothing here</body></html>"

_COMMUNITY_HTML = """
<html><script>
var data = {"fwuid":"fw_community","APPLICATION@markup://siteforce:communityApp":"comm_hash"};
</script></html>
"""

_LOGIN_AURA_RESPONSE = {
    "actions": [{"state": "SUCCESS", "returnValue": None}],
    "events": [
        {
            "descriptor": "markup://aura:clientRedirect",
            "attributes": {"values": {"url": "https://example.com/frontdoor"}},
        },
    ],
}

_CONTRACTS_RESPONSE = {
    "actions": [
        {"state": "SUCCESS", "returnValue": {"returnValue": ["contract-1"]}},
    ],
}

_CONTRACT_DETAILS_RESPONSE = {
    "actions": [
        {
            "state": "SUCCESS",
            "returnValue": {
                "returnValue": {
                    "compteInfo": [{"ELEMB": "CTR-001", "ELEMA": "PDS-001"}],
                },
            },
        },
    ],
}

_GET_DATA_RESPONSE = {
    "actions": [
        {
            "state": "SUCCESS",
            "returnValue": {
                "returnValue": {
                    "prixMoyenEau": 4.2345,
                    "data": {
                        "CONSOMMATION": [
                            {
                                "DATE_INDEX": "2024-03-15 00:00:00",
                                "CONSOMMATION": "0.150",
                                "VALEUR_INDEX": "100.000",
                                "FLAG_ESTIMATION": "false",
                            },
                            {
                                "DATE_INDEX": "2024-03-16 00:00:00",
                                "CONSOMMATION": "0.200",
                                "VALEUR_INDEX": "100.200",
                                "FLAG_ESTIMATION": "true",
                            },
                        ],
                    },
                },
            },
        },
    ],
}


async def _authenticated_provider() -> SedifProvider:
    provider = SedifProvider()
    provider._authenticated = True
    provider._fwuid = "fw1"
    return provider


async def test_get_login_context_extracts_fwuid() -> None:
    provider = SedifProvider()
    with aioresponses() as m:
        m.get(_LOGIN_URL, body=_LOGIN_HTML, status=200)
        await provider._get_login_context()
        assert provider._fwuid == "abc123"
    await provider.async_close()


async def test_get_login_context_raises_without_fwuid() -> None:
    provider = SedifProvider()
    with aioresponses() as m:
        m.get(_LOGIN_URL, body=_LOGIN_HTML_NO_FWUID, status=200)
        with pytest.raises(AuthError, match="fwuid"):
            await provider._get_login_context()
    await provider.async_close()


async def test_extract_aura_token_found() -> None:
    provider = SedifProvider()
    provider._session.cookie_jar.update_cookies(
        {"__Host-ERIC-123": "token_value"}, URL(_BASE_URL)
    )
    assert provider._extract_aura_token() == "token_value"
    await provider.async_close()


async def test_authenticate_success() -> None:
    provider = SedifProvider()
    provider._session.cookie_jar.update_cookies(
        {"__Host-ERIC-abc": "csrf_token"}, URL(_BASE_URL)
    )
    with aioresponses() as m:
        m.get(_LOGIN_URL, body=_LOGIN_HTML, status=200)
        m.post(AURA_URL_RE, payload=_LOGIN_AURA_RESPONSE, status=200, repeat=True)
        m.get("https://example.com/frontdoor", body="", status=200)
        m.get(f"{_BASE_URL}/s/", body=_COMMUNITY_HTML, status=200)

        await provider.async_authenticate("user@example.com", "pw")

        assert provider._authenticated is True
        assert provider._aura_token == "csrf_token"  # noqa: S105
    await provider.async_close()


async def test_authenticate_raises_on_failure() -> None:
    provider = SedifProvider()
    with aioresponses() as m:
        m.get(_LOGIN_URL, body=_LOGIN_HTML, status=200)
        m.post(
            AURA_URL_RE,
            payload={"actions": [{"state": "ERROR", "error": []}], "events": []},
            status=200,
            repeat=True,
        )
        with pytest.raises(AuthError, match="Login failed"):
            await provider.async_authenticate("user@example.com", "wrong")
    await provider.async_close()


async def test_list_contracts_returns_contract_info() -> None:
    provider = await _authenticated_provider()
    with aioresponses() as m:
        m.post(AURA_URL_RE, payload=_CONTRACTS_RESPONSE, status=200)
        contracts = await provider.async_list_contracts()
        assert len(contracts) == 1
        assert contracts[0].contract_id == "contract-1"
    await provider.async_close()


async def test_get_daily_consumption_parses_records() -> None:
    provider = await _authenticated_provider()
    with aioresponses() as m:
        m.post(AURA_URL_RE, payload=_CONTRACT_DETAILS_RESPONSE, status=200)
        m.post(AURA_URL_RE, payload=_GET_DATA_RESPONSE, status=200)

        batch = await provider.async_get_daily_consumption(
            "contract-1", date(2024, 3, 15), date(2024, 3, 17)
        )

        assert len(batch.records) == 2
        assert batch.records[0].liters == pytest.approx(150.0)
        assert batch.records[0].record_date == date(2024, 3, 15)
        assert batch.records[1].is_estimated is True
        assert batch.price_per_m3 == pytest.approx(4.2345)
    await provider.async_close()


async def test_ssl_context_not_built_synchronously_at_construction() -> None:
    # Building an SSLContext does blocking file I/O (reading certifi's
    # bundle + our intermediate cert), which must never happen directly in
    # __init__ since that runs on HA's event loop, not in an executor.
    provider = SedifProvider()
    assert provider._ssl_context is None
    await provider.async_close()


async def test_ssl_context_built_lazily_on_first_use() -> None:
    provider = SedifProvider()
    with aioresponses() as m:
        m.get(_LOGIN_URL, body=_LOGIN_HTML, status=200)
        await provider._get_login_context()
    assert provider._ssl_context is not None
    await provider.async_close()
