"""SEDIF (L'Eau d'Île-de-France) water provider for AquaWatch.

Reverse engineers the Salesforce Aura API used by the SEDIF customer
portal. The portal only exposes DAILY totals (TYPE_PAS="JOURNEE") — there
is no hourly granularity, which is why leak detection (detection.py) works
on sustained daily consumption rather than a night-time flow window.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import re
import ssl
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import aiohttp
import certifi

from ..models import ConsumptionBatch, ConsumptionRecord, ContractInfo
from . import WaterProvider, register_provider
from .exceptions import AuthError, ScrapingError

_LOGGER = logging.getLogger(__name__)

_BASE_URL = "https://connexion.leaudiledefrance.fr"
_INTERMEDIATE_CERT = Path(__file__).parent / "gandi_intermediate.pem"
_AURA_URL = f"{_BASE_URL}/s/sfsites/aura"
_LOGIN_URL = f"{_BASE_URL}/s/login/"

_LOGIN_APP = "siteforce:loginApp2"
_COMMUNITY_APP = "siteforce:communityApp"

_LOGIN_APP2_RE = re.compile(
    r'"APPLICATION@markup://siteforce:loginApp2"\s*:\s*"([^"]+)"',
)
_COMMUNITY_APP_RE = re.compile(
    r'"APPLICATION@markup://siteforce:communityApp"\s*:\s*"([^"]+)"',
)
_FWUID_RE = re.compile(r'"fwuid"\s*:\s*"([^"]+)"')

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_ESTIMATION_TRUTHY = frozenset(("true", "1", "yes"))


class _TimeStep(enum.StrEnum):
    """Time granularity for consumption queries — only DAILY is used."""

    DAILY = "JOURNEE"


def _build_ssl_context() -> ssl.SSLContext:
    """Create an SSL context with the missing Gandi intermediate cert."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(_INTERMEDIATE_CERT))
    return ctx


def _strip_aura_wrapper(text: str) -> str:
    """Strip Salesforce Aura's CSRF wrappers from JSON responses."""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return text[start : end + 1]
    return text


def _parse_record(raw: dict[str, Any]) -> ConsumptionRecord:
    record_date = (
        datetime.strptime(raw["DATE_INDEX"], "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=UTC)
        .date()
    )
    return ConsumptionRecord(
        record_date=record_date,
        liters=float(raw["CONSOMMATION"]) * 1000,
        cumulative_index_m3=float(raw["VALEUR_INDEX"]),
        is_estimated=str(raw.get("FLAG_ESTIMATION", "")).lower()
        in _ESTIMATION_TRUTHY,
    )


@register_provider
class SedifProvider(WaterProvider):
    """Water provider for L'Eau d'Île-de-France (SEDIF)."""

    provider_id = "sedif"
    display_name = "L'Eau d'Île-de-France (SEDIF)"
    available = True

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._external_session = session is not None
        self._session = session or aiohttp.ClientSession()
        # Built lazily via an executor on first use: constructing an
        # SSLContext does blocking file I/O (reading the certifi bundle and
        # our bundled intermediate cert), which must not happen directly on
        # the event loop -- HA's own blocking-call detector flags it
        # otherwise.
        self._ssl_context: ssl.SSLContext | None = None
        self._default_headers = {
            "User-Agent": _USER_AGENT,
            "Origin": _BASE_URL,
        }
        self._fwuid: str | None = None
        self._aura_token: str | None = None
        self._app_loaded: dict[str, str] | None = None
        self._authenticated = False
        self._username: str | None = None
        self._password: str | None = None

    async def async_authenticate(self, email: str, password: str) -> None:
        self._username = email
        self._password = password
        await self._login()

    async def async_list_contracts(self) -> list[ContractInfo]:
        await self._ensure_authenticated()
        contract_ids = await self._get_contracts()
        contracts = []
        for cid in contract_ids:
            # `cid` itself (from listCurrentUserActiveContrats) is an opaque
            # Salesforce record ID, not the number shown on the actual SEDIF
            # portal -- that human-readable number lives at
            # getContratDetails()'s response under contrat.Name (confirmed
            # against a real account; contrat.Id there is the same kind of
            # opaque identifier as `cid`). Falls back to a shortened `cid`
            # if that field is ever missing, rather than failing outright.
            details = await self._get_contract_details(cid)
            contract_number = details.get("contrat", {}).get("Name")
            label = (
                f"Contrat {contract_number}"
                if contract_number
                else f"Contrat …{cid[-8:]}"
            )
            contracts.append(ContractInfo(contract_id=cid, label=label))
        return contracts

    async def async_get_raw_contract_details(self, contract_id: str) -> dict[str, Any]:
        """Return the unprocessed getContratDetails response (for debugging).

        Not part of the WaterProvider interface -- SEDIF-specific, used by
        diagnostics.py to help identify which field holds the human-readable
        contract number (distinct from the opaque contract_id returned by
        listCurrentUserActiveContrats).
        """
        await self._ensure_authenticated()
        return await self._get_contract_details(contract_id)

    async def async_get_daily_consumption(
        self, contract_id: str, start: date, end: date
    ) -> ConsumptionBatch:
        result = await self._get_consumption_raw(contract_id, start, end)
        data = result.get("data", {})
        records = [_parse_record(raw) for raw in data.get("CONSOMMATION", [])]
        return ConsumptionBatch(
            records=records,
            price_per_m3=float(result.get("prixMoyenEau", 0)),
        )

    async def _get_consumption_raw(
        self, contract_id: str, start: date, end: date
    ) -> dict[str, Any]:
        await self._ensure_authenticated()
        details = await self._get_contract_details(contract_id)
        compte_info = details.get("compteInfo", [])
        if not compte_info:
            msg = "No meter information found for contract"
            raise ScrapingError(msg)
        meter = compte_info[0]
        numero_compteur = meter["ELEMB"]
        id_pds = meter["ELEMA"]

        return await self._apex_action(
            "LTN015_ICL_ContratConsoHisto",
            "getData",
            params={
                "contractId": contract_id,
                "TYPE_PAS": _TimeStep.DAILY.value,
                "DATE_DEBUT": start.isoformat(),
                "DATE_FIN": end.isoformat(),
                "NUMERO_COMPTEUR": numero_compteur,
                "ID_PDS": id_pds,
            },
            page_uri="/espace-particuliers/s/historique",
        )

    async def async_close(self) -> None:
        if not self._external_session:
            await self._session.close()

    # -- internal helpers -----------------------------------------------

    async def _async_ensure_ssl_context(self) -> ssl.SSLContext:
        if self._ssl_context is None:
            loop = asyncio.get_running_loop()
            self._ssl_context = await loop.run_in_executor(None, _build_ssl_context)
        return self._ssl_context

    async def _get_login_context(self) -> None:
        ssl_context = await self._async_ensure_ssl_context()
        async with self._session.get(
            _LOGIN_URL, ssl=ssl_context, headers=self._default_headers
        ) as resp:
            resp.raise_for_status()
            html = await resp.text()

        match = _FWUID_RE.search(html)
        if match:
            self._fwuid = match.group(1)

        match = _LOGIN_APP2_RE.search(html)
        if match:
            self._app_loaded = {
                "APPLICATION@markup://siteforce:loginApp2": match.group(1),
            }

        if not self._fwuid:
            msg = "Could not extract fwuid from login page"
            raise AuthError(msg)

    def _extract_aura_token(self) -> str | None:
        for cookie in self._session.cookie_jar:
            if "ERIC" in cookie.key:
                return cookie.value
        return None

    def _build_aura_context(self, app: str = _COMMUNITY_APP) -> dict[str, Any]:
        loaded = dict(self._app_loaded) if self._app_loaded else {}
        return {
            "mode": "PROD",
            "fwuid": self._fwuid,
            "app": app,
            "loaded": loaded,
            "dn": [],
            "globals": {},
            "uad": True,
        }

    async def _aura_call_raw(
        self,
        actions: list[dict[str, Any]],
        app: str = _COMMUNITY_APP,
        page_uri: str = "/espace-particuliers/s/",
    ) -> dict[str, Any]:
        descriptors = []
        for a in actions:
            desc = a.get("descriptor", "")
            if "ApexActionController" in desc:
                descriptors.append("aura.ApexAction.execute=1")
            else:
                short = desc.split("/")[-1].replace("ACTION$", ".")
                descriptors.append(f"other.{short}=1")

        query_parts = ["r=0"]
        seen: set[str] = set()
        for d in descriptors:
            if d not in seen:
                query_parts.append(d)
                seen.add(d)

        url = f"{_AURA_URL}?{'&'.join(query_parts)}"

        data = aiohttp.FormData()
        data.add_field("message", json.dumps({"actions": actions}))
        data.add_field("aura.context", json.dumps(self._build_aura_context(app)))
        data.add_field("aura.pageURI", page_uri)
        data.add_field("aura.token", self._aura_token or "undefined")

        headers = {
            **self._default_headers,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        ssl_context = await self._async_ensure_ssl_context()
        async with self._session.post(
            url, data=data, headers=headers, ssl=ssl_context
        ) as resp:
            resp.raise_for_status()
            text = await resp.text()
            text = _strip_aura_wrapper(text)
            result: dict[str, Any] = json.loads(text)

        ctx = result.get("context", {})
        if ctx.get("fwuid"):
            self._fwuid = ctx["fwuid"]
        loaded = ctx.get("loaded")
        if loaded:
            self._app_loaded = loaded

        return result

    async def _aura_call(
        self, actions: list[dict[str, Any]], **kwargs: Any
    ) -> list[dict[str, Any]]:
        raw = await self._aura_call_raw(actions, **kwargs)
        result: list[dict[str, Any]] = raw.get("actions", [])
        return result

    async def _apex_action(
        self,
        classname: str,
        method: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        action_params: dict[str, Any] = {
            "namespace": "",
            "classname": classname,
            "method": method,
            "cacheable": False,
            "isContinuation": False,
        }
        if params:
            action_params["params"] = params

        action = {
            "id": "1;a",
            "descriptor": "aura://ApexActionController/ACTION$execute",
            "callingDescriptor": "UNKNOWN",
            "params": action_params,
        }

        results = await self._aura_call([action], **kwargs)
        if not results:
            msg = f"No response for {classname}.{method}"
            raise ScrapingError(msg)

        result = results[0]
        if result.get("state") != "SUCCESS":
            error = result.get("error", [])
            msg = f"{classname}.{method} failed: {error}"
            raise ScrapingError(msg)

        rv = result.get("returnValue", {})
        if isinstance(rv, dict) and "returnValue" in rv:
            return rv["returnValue"]
        return rv

    async def _login(self) -> None:
        await self._get_login_context()

        action = {
            "id": "1;a",
            "descriptor": "apex://LightningLoginFormController/ACTION$login",
            "callingDescriptor": "UNKNOWN",
            "params": {
                "username": self._username,
                "password": self._password,
                "startUrl": "/espace-particuliers/s/",
            },
        }

        response = await self._aura_call_raw(
            [action],
            app=_LOGIN_APP,
            page_uri="/espace-particuliers/s/login",
        )

        actions = response.get("actions", [])
        if actions:
            result = actions[0]
            if result.get("state") != "SUCCESS":
                msg = f"Login failed: {result.get('error', [])}"
                raise AuthError(msg)
            return_value = result.get("returnValue")
            if isinstance(return_value, str) and return_value:
                msg = f"Login failed: {return_value}"
                raise AuthError(msg)

        await self._complete_login(response)

    async def _complete_login(self, login_response: dict[str, Any]) -> None:
        events = login_response.get("events", [])
        redirect_url = None
        for event in events:
            if event.get("descriptor") == "markup://aura:clientRedirect":
                redirect_url = event["attributes"]["values"]["url"]
                break

        if not redirect_url:
            msg = "No redirect URL in login response"
            raise AuthError(msg)

        ssl_context = await self._async_ensure_ssl_context()
        async with self._session.get(
            redirect_url, ssl=ssl_context, headers=self._default_headers
        ) as resp:
            resp.raise_for_status()

        async with self._session.get(
            f"{_BASE_URL}/s/", ssl=ssl_context, headers=self._default_headers
        ) as resp:
            resp.raise_for_status()
            html = await resp.text()

        self._aura_token = self._extract_aura_token()
        if not self._aura_token:
            msg = "Could not extract CSRF token after login"
            raise AuthError(msg)

        match = _COMMUNITY_APP_RE.search(html)
        if match:
            self._app_loaded = {
                "APPLICATION@markup://siteforce:communityApp": match.group(1),
            }

        match = _FWUID_RE.search(html)
        if match:
            self._fwuid = match.group(1)

        self._authenticated = True

    async def _ensure_authenticated(self) -> None:
        if not self._authenticated:
            await self._login()

    async def _get_contracts(self) -> list[str]:
        result = await self._apex_action(
            "LTN009_ICL_ContratsGroupements",
            "listCurrentUserActiveContrats",
        )
        if isinstance(result, list):
            return result
        return []

    async def _get_contract_details(self, contract_id: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._apex_action(
            "LTN008_ICL_ContratDetails",
            "getContratDetails",
            params={"contratId": contract_id},
        )
        return result
