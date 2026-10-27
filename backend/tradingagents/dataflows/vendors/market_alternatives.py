"""Fixed-endpoint issuer/options/news adapters; no trading or environment-key fallback."""

from __future__ import annotations

import hashlib
import math
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from tradingagents.dataflows.errors import VendorUnavailableError

ISSUER_FUNDS = {"SOXX": "239705", "IVV": "239726"}
ISSUER_URL = "https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/get-fund-document"
_YAHOO_LOCK = threading.RLock()


class AlternativeVendorError(VendorUnavailableError):
    def __init__(self, code="provider_unavailable", status_code=503):
        super().__init__(code)
        self.code, self.status_code = code, status_code


def number(value, *, positive=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and (not positive or result > 0) else None


def parse_issuer_xml(content: bytes) -> list[dict]:
    """Read only the named Historical sheet, never evaluate XML entities/formulas."""
    if len(content) > 12_000_000 or re.search(rb"<!\s*(DOCTYPE|ENTITY)", content, re.I):
        raise AlternativeVendorError("source_schema_changed")
    try:
        text = content.decode("utf-8-sig")
        # The official workbook currently contains naked ampersands in prose.
        text = re.sub(r"&(?!#\d+;|#x[0-9a-fA-F]+;|amp;|lt;|gt;|quot;|apos;)", "&amp;", text)
        root = ET.fromstring(text)
        namespace = "{urn:schemas-microsoft-com:office:spreadsheet}"
        for sheet in root.findall(f"{namespace}Worksheet"):
            if sheet.get(namespace + "Name") != "Historical":
                continue
            table = sheet.find(namespace + "Table")
            if table is None:
                break
            rows = table.findall(namespace + "Row")
            if not rows:
                break
            header = None
            records = {}
            for row in rows:
                cells = []
                for cell in row.findall(namespace + "Cell"):
                    index = int(cell.get(namespace + "Index", str(len(cells) + 1)))
                    if not 1 <= index <= 100:
                        raise ValueError("invalid column")
                    while len(cells) < index - 1:
                        cells.append("")
                    data = cell.find(namespace + "Data")
                    cells.append("" if data is None else "".join(data.itertext()).strip())
                if header is None:
                    if {"As Of", "NAV per Share", "Shares Outstanding"}.issubset(cells):
                        header = {
                            name: cells.index(name)
                            for name in ("As Of", "NAV per Share", "Shares Outstanding")
                        }
                    continue
                if len(cells) <= max(header.values()):
                    continue
                try:
                    day = datetime.strptime(cells[header["As Of"]], "%b %d, %Y").date().isoformat()
                except ValueError:
                    continue
                nav = number(cells[header["NAV per Share"]], positive=True)
                shares = number(cells[header["Shares Outstanding"]], positive=True)
                if day in records and records[day] != {
                    "date": day,
                    "nav": nav,
                    "shares_outstanding": shares,
                }:
                    raise ValueError("conflicting dates")
                records[day] = {"date": day, "nav": nav, "shares_outstanding": shares}
            if header is not None and records:
                return sorted(records.values(), key=lambda row: row["date"])
    except (UnicodeError, ET.ParseError, ValueError, TypeError):
        pass
    raise AlternativeVendorError("source_schema_changed")


class OfficialHTTP:
    def __init__(self, session=None):
        if session is None:
            import requests

            session = requests.Session()
        self.session = session

    def request(self, url, *, headers=None, params=None, maximum=12_000_000):
        try:
            response = self.session.get(
                url,
                headers=headers or {},
                params=params or {},
                timeout=(5, 15),
                allow_redirects=False,
                stream=True,
            )
            if response.status_code == 401:
                raise AlternativeVendorError("authentication_failed", 409)
            if response.status_code == 403:
                raise AlternativeVendorError("data_permission_required", 409)
            if response.status_code == 429:
                raise AlternativeVendorError("rate_limited", 429)
            if response.status_code != 200:
                raise AlternativeVendorError()
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > maximum:
                    raise AlternativeVendorError("source_response_too_large")
            return bytes(data)
        except AlternativeVendorError:
            raise
        except Exception:
            raise AlternativeVendorError() from None
        finally:
            if "response" in locals():
                response.close()

    def json(self, url, *, headers=None, params=None):
        import json

        try:
            value = json.loads(self.request(url, headers=headers, params=params))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except AlternativeVendorError:
            raise
        except (ValueError, TypeError):
            raise AlternativeVendorError("source_schema_changed") from None


class IsharesHistoryProvider:
    def __init__(self, cache_root: Path, *, http=None):
        self.cache_root, self.http = Path(cache_root), http or OfficialHTTP()

    def history(self, symbol):
        if symbol not in ISSUER_FUNDS:
            raise AlternativeVendorError("issuer_symbol_not_supported", 422)
        content = self.http.request(
            ISSUER_URL,
            params={
                "appSubType": "ISHARES",
                "appType": "PRODUCT_PAGE",
                "component": "fundDownload",
                "locale": "en_US",
                "portfolioId": ISSUER_FUNDS[symbol],
                "targetSite": "us-ishares",
                "userType": "individual",
            },
        )
        records = parse_issuer_xml(content)
        # Retain the original workbook, including its personal-use notices, on this machine.
        root = self.cache_root / "issuer-originals"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = root / f"{symbol}-{uuid4()}.tmp"
        try:
            temporary.write_bytes(content)
            temporary.chmod(0o600)
            temporary.replace(root / f"{symbol}-latest.xml")
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "rows": records,
            "raw_sha256": hashlib.sha256(content).hexdigest(),
            "source_url": ISSUER_URL,
            "personal_use_only": True,
        }


class YahooOptionsProvider:
    def __init__(self, cache_root: Path):
        self.cache_root = Path(cache_root)

    def options(self, symbol, *, expiration=None, max_expirations=1):
        import yfinance as yf

        try:
            with _YAHOO_LOCK:
                root = self.cache_root / "yahoo-runtime"
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
                yf.set_tz_cache_location(str(root))
                ticker = yf.Ticker(symbol)
                available = sorted(ticker.options)
                selected = [expiration] if expiration else available[:max_expirations]
                if expiration and expiration not in available:
                    raise AlternativeVendorError("expiration_not_available", 422)
                contracts = []
                for day in selected:
                    chain = ticker.option_chain(day)
                    for kind, frame in (("call", chain.calls), ("put", chain.puts)):
                        for row in frame.to_dict(orient="records"):
                            stamp = row.get("lastTradeDate")
                            row["lastTradeDate"] = (
                                stamp.isoformat() if hasattr(stamp, "isoformat") else None
                            )
                            contracts.append({**row, "expiration": day, "contract_type": kind})
                return {
                    "expirations": available,
                    "selected_expirations": selected,
                    "contracts": contracts,
                    "truncated": len(available) > len(selected),
                }
        except AlternativeVendorError:
            raise
        except Exception:
            raise AlternativeVendorError() from None


def safe_page(url, *, host, path_prefix):
    try:
        parsed = urlsplit(url)
        valid = (
            isinstance(url, str)
            and parsed.scheme == "https"
            and parsed.hostname == host
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and parsed.path == path_prefix
        )
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise AlternativeVendorError("unsafe_pagination")
    parameters = [
        (key, value)
        for key, value in parse_qsl(parsed.query)
        if key.lower() not in {"apikey", "api_key", "token", "access_token", "authorization"}
    ]
    return urlunsplit(("https", host, parsed.path, urlencode(parameters), ""))


class MassiveProvider:
    def __init__(self, *, http=None):
        self.http = http or OfficialHTTP()

    def _pages(self, path, key, parameters, *, max_pages=2):
        url, pages, rows, seen = "https://api.massive.com" + path, 0, [], set()
        while url and pages < max_pages:
            if url in seen:
                raise AlternativeVendorError("unsafe_pagination")
            seen.add(url)
            data = self.http.json(
                url,
                headers={"Authorization": "Bearer " + key},
                params=parameters if pages == 0 else None,
            )
            if data.get("status") in {"ERROR", "NOT_AUTHORIZED", "NOT_ENTITLED"}:
                raise AlternativeVendorError("data_permission_required", 409)
            if not isinstance(data.get("results"), list):
                raise AlternativeVendorError("source_schema_changed")
            if not all(isinstance(row, dict) for row in data["results"]):
                raise AlternativeVendorError("source_schema_changed")
            rows.extend(data["results"])
            pages += 1
            next_url = data.get("next_url")
            url = (
                safe_page(next_url, host="api.massive.com", path_prefix=path) if next_url else None
            )
        return {"rows": rows, "truncated": bool(url), "pages": pages}

    def news(self, key, *, symbol=None, since=None, until=None, limit=100):
        parameters = {
            "published_utc.gte": since,
            "published_utc.lte": until,
            "limit": limit,
            "sort": "published_utc",
            "order": "desc",
        }
        if symbol:
            parameters["ticker"] = symbol
        return self._pages("/v2/reference/news", key, parameters)

    def options(self, key, symbol, *, expiration=None, max_expirations=1):
        parameters = {"limit": 250, "sort": "expiration_date", "order": "asc"}
        if expiration:
            parameters["expiration_date"] = expiration
        result = self._pages(f"/v3/snapshot/options/{symbol}", key, parameters)
        if not all(isinstance(row.get("details"), dict) for row in result["rows"]):
            raise AlternativeVendorError("source_schema_changed")
        available = sorted(
            {row["details"].get("expiration_date", "") for row in result["rows"]} - {""}
        )
        selected = [expiration] if expiration else available[:max_expirations]
        result["rows"] = [
            row
            for row in result["rows"]
            if row.get("details", {}).get("expiration_date") in selected
        ]
        return {
            **result,
            "expirations": available,
            "selected_expirations": selected,
            "truncated": result["truncated"] or len(available) > len(selected),
        }


class IntrinioFlowsProvider:
    def __init__(self, *, http=None):
        self.http = http or OfficialHTTP()

    def flows(self, key, symbol, *, since, until):
        path = f"/etfg/etfs/{symbol}/fund_flows"
        parameters = {"start_date": since, "end_date": until, "page_size": 100}
        rows, token, seen = [], None, set()
        for _ in range(4):
            if token:
                if token in seen:
                    raise AlternativeVendorError("unsafe_pagination")
                seen.add(token)
                parameters["next_page"] = token
            data = self.http.json(
                "https://api-v2.intrinio.com" + path,
                headers={"Authorization": "Bearer " + key},
                params=parameters,
            )
            if not isinstance(data.get("fund_flows"), list):
                raise AlternativeVendorError("source_schema_changed")
            if not all(isinstance(row, dict) for row in data["fund_flows"]):
                raise AlternativeVendorError("source_schema_changed")
            rows.extend(data["fund_flows"])
            token = data.get("next_page")
            if token is not None and (not isinstance(token, str) or len(token) > 8192):
                raise AlternativeVendorError("source_schema_changed")
            if not token:
                break
        return {"rows": rows, "truncated": bool(token)}


class AlpacaOptionsProvider:
    def __init__(self, *, http=None):
        self.http = http or OfficialHTTP()

    def options(
        self, key, secret, symbol, *, feed="indicative", expiration=None, max_expirations=1
    ):
        path = f"/v1beta1/options/snapshots/{symbol}"
        parameters = {"feed": feed, "limit": 1000}
        if expiration:
            parameters["expiration_date"] = expiration
        rows, token, seen = {}, None, set()
        for _ in range(2):
            if token:
                if token in seen:
                    raise AlternativeVendorError("unsafe_pagination")
                seen.add(token)
                parameters["page_token"] = token
            data = self.http.json(
                "https://data.alpaca.markets" + path,
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
                params=parameters,
            )
            if not isinstance(data.get("snapshots"), dict):
                raise AlternativeVendorError("source_schema_changed")
            if not all(
                isinstance(name, str) and isinstance(row, dict)
                for name, row in data["snapshots"].items()
            ):
                raise AlternativeVendorError("source_schema_changed")
            rows.update(data["snapshots"])
            token = data.get("next_page_token")
            if token is not None and (not isinstance(token, str) or len(token) > 8192):
                raise AlternativeVendorError("source_schema_changed")
            if not token:
                break
        expirations = sorted({parsed[0] for name in rows if (parsed := parse_occ_symbol(name))})
        selected = [expiration] if expiration else expirations[:max_expirations]
        return {
            "rows": {
                name: row
                for name, row in rows.items()
                if (parsed := parse_occ_symbol(name)) and parsed[0] in selected
            },
            "expirations": expirations,
            "selected_expirations": selected,
            "truncated": bool(token) or len(expirations) > len(selected),
        }


def parse_occ_symbol(value):
    match = re.fullmatch(r"(?:O:)?[A-Z.\-]+(\d{6})([CP])(\d{8})", value)
    if not match:
        return None
    try:
        day = datetime.strptime(match[1], "%y%m%d").date().isoformat()
        return day, "call" if match[2] == "C" else "put", int(match[3]) / 1000
    except ValueError:
        return None
