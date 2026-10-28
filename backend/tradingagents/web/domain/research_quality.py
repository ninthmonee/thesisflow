"""Deterministic source-field checks, never inferred from an agent's report."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit

METHODS = {
    "get_stock_data": ("market", ("symbol", "start_date", "end_date")),
    "get_verified_market_snapshot": ("market", ("symbol", "as_of_date", "look_back_days")),
    "get_indicators": ("technical", ("symbol", "indicator", "as_of_date", "look_back_days")),
    "get_fundamentals": ("fundamentals", ("symbol", "as_of_date")),
    "get_balance_sheet": ("fundamentals", ("symbol", "freq", "as_of_date")),
    "get_cashflow": ("fundamentals", ("symbol", "freq", "as_of_date")),
    "get_income_statement": ("fundamentals", ("symbol", "freq", "as_of_date")),
    "get_news": ("news", ("symbol", "start_date", "end_date")),
    "get_stocktwits_messages": ("social", ("symbol", "start_date", "end_date")),
    "get_reddit_posts": ("social", ("symbol", "start_date", "end_date")),
    "get_global_news": ("news", ("as_of_date", "look_back_days", "limit")),
    "get_insider_transactions": ("fundamentals", ("symbol", "as_of_date")),
    "get_macro_indicators": ("macro", ("indicator", "as_of_date", "look_back_days")),
    "get_prediction_markets": ("prediction", ("topic", "limit")),
}
MAX_BODY_BYTES = 48_000
MAX_RESPONSE_CHARS = 2_000_000
_PROVIDERS = {
    "yfinance",
    "alpha_vantage",
    "sec_edgar",
    "fred",
    "polymarket",
    "stocktwits",
    "reddit",
}
_FINANCIAL_FIELDS = {
    "totalassets",
    "currentassets",
    "totalcurrentassets",
    "cashandequivalents",
    "cashandcashequivalentsatcarryingvalue",
    "totalliabilities",
    "currentliabilities",
    "totalcurrentliabilities",
    "stockholdersequity",
    "totalshareholderequity",
    "revenue",
    "totalrevenue",
    "costofrevenue",
    "grossprofit",
    "grossprofits",
    "operatingincome",
    "netincome",
    "dilutedeps",
    "operatingcashflow",
    "investingcashflow",
    "financingcashflow",
    "capitalexpenditure",
    "capitalexpenditures",
    "marketcap",
    "marketcapitalization",
    "peratiottm",
    "peratio",
    "forwardpe",
    "pricetobook",
    "eps",
    "epsttm",
    "ebitda",
    "profitmargin",
    "profitmargins",
    "operatingmargin",
    "returnonequity",
    "returnonassets",
    "debttoequity",
    "currentratio",
    "bookvalue",
    "freecashflow",
    "cashflowfrominvestment",
    "cashflowfromfinancing",
    "totalstockholderequity",
    "ordinarysharesnumber",
}


def issue(code, message):
    return {"code": code, "message": message}


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def bar_day(value):
    """A complete date or ISO timestamp, never a date prefix followed by junk."""
    if day(value):
        return value
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?", value
    ):
        return None
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return None


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def public_text(value, limit=300):
    if not isinstance(value, str):
        return None
    value = re.sub(
        r"https?://[^\s<>]+", lambda match: public_url(match[0]) or "[redacted-url]", value
    )
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted-contact]", value)
    value = re.sub(
        r"(?i)(?:api[_ -]?key|token|authorization|secret|password|user[_ -]?agent|cookie|contact)\s*[:=]\s*[^\s,;]+",
        "[redacted]",
        value,
    )
    value = re.sub(r"(?i)\b(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]+)\b", "[redacted]", value)
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()
    return value[:limit] or None


def public_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError:
        return None


def safe_request(method, args, kwargs, symbol):
    category, names = METHODS.get(method, ("unknown", ()))
    values = {name: args[index] for index, name in enumerate(names) if index < len(args)}
    values.update({name: kwargs[name] for name in names if name in kwargs})
    result = {}
    for key, value in values.items():
        if key == "symbol":
            if value == symbol:
                result[key] = symbol
        elif key.endswith("date"):
            if (valid := day(value)) is not None:
                result[key] = valid
        elif key in {"look_back_days", "limit"}:
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5000:
                result[key] = value
        elif (
            key == "freq"
            and value in {"quarterly", "annual"}
            or key == "indicator"
            and isinstance(value, str)
            and re.fullmatch(r"[A-Za-z0-9_]{1,40}", value)
        ):
            result[key] = value
        # Free-form topics, unexpected parameters, credentials and headers are not persisted.
    return category, result


def _latest_closed(analysis_date, received_at):
    from exchange_calendars import get_calendar

    calendar = get_calendar("XNYS")
    cutoff = date.fromisoformat(analysis_date)
    sessions = calendar.sessions_in_range(cutoff - timedelta(days=20), cutoff)
    closed = [
        stamp for stamp in sessions if calendar.session_close(stamp).to_pydatetime() <= received_at
    ]
    return closed[-1].strftime("%Y-%m-%d") if closed else None


def _ohlcv(rows, requested, received_at):
    from exchange_calendars import get_calendar

    calendar = get_calendar("XNYS")
    valid, seen, invalid = [], set(), False
    for row in rows:
        stamp = bar_day(row.get("date"))
        values = {
            name: number(row.get(name)) for name in ("open", "high", "low", "close", "volume")
        }
        if (
            stamp is None
            or stamp in seen
            or stamp > requested
            or not calendar.is_session(stamp)
            or any(
                values[name] is None or values[name] <= 0
                for name in ("open", "high", "low", "close")
            )
            or values["volume"] is None
            or values["volume"] < 0
            or not values["low"]
            <= min(values["open"], values["close"])
            <= max(values["open"], values["close"])
            <= values["high"]
        ):
            invalid = True
            continue
        seen.add(stamp)
        valid.append({"date": stamp, **values})
    valid.sort(key=lambda row: row["date"])
    expected = _latest_closed(requested, received_at)
    verified = bool(valid) and not invalid and valid[-1]["date"] == expected
    return valid[-300:], verified, expected


def _market(method, text, requested, received_at):
    if method == "get_verified_market_snapshot":
        stamp = re.search(r"^- Latest trading row used: (\d{4}-\d{2}-\d{2})$", text, re.MULTILINE)
        fields = dict(
            re.findall(r"^\| (Open|High|Low|Close|Volume) \| ([^|]+) \|$", text, re.MULTILINE)
        )
        rows = [
            {
                "date": stamp[1] if stamp else None,
                **{key.lower(): value.strip() for key, value in fields.items()},
            }
        ]
    else:
        lines = [line for line in text.splitlines() if line and not line.startswith("#")]
        reader = csv.DictReader(io.StringIO("\n".join(lines)))
        rows = []
        for row in reader:
            lowered = {str(key).lower(): value for key, value in row.items() if key is not None}
            rows.append(
                {
                    "date": lowered.get("date", lowered.get("timestamp")),
                    **{
                        name: lowered.get(name)
                        for name in ("open", "high", "low", "close", "volume")
                    },
                }
            )
            if len(rows) > 5000:
                return (
                    {},
                    "unknown",
                    None,
                    [issue("market_rows_exceeded", "行情行数超出本批证据校验上限")],
                )
    rows, verified, expected = _ohlcv(rows, requested, received_at)
    return (
        {"rows": rows, "expected_closed_session": expected},
        "validated" if verified else "unknown",
        rows[-1]["date"] if rows else None,
        []
        if verified
        else [
            issue("market_not_validated", "OHLCV缺失、无效、重复、日期越界或未覆盖最近已结束交易日")
        ],
    )


def _financial(text, payload):
    facts = []
    if isinstance(payload, dict):
        reports = payload.get("quarterlyReports", payload.get("annualReports", [payload]))
        for row in reports[:20] if isinstance(reports, list) else []:
            if not isinstance(row, dict):
                continue
            period = day(row.get("fiscalDateEnding"))
            for key, value in row.items():
                if (
                    isinstance(key, str)
                    and re.sub(r"[^a-z]", "", key.lower()) in _FINANCIAL_FIELDS
                    and (parsed := number(value)) is not None
                ):
                    facts.append(
                        {
                            "field": re.sub(r"[^a-z]", "", key.lower()),
                            "period": period,
                            "value": parsed,
                        }
                    )
    else:
        lines = [line for line in text.splitlines() if line and not line.startswith("#")]
        table = list(csv.reader(lines))
        if table and any(day(cell[:10]) for cell in table[0][1:]):
            for row in table[1:80]:
                if not row:
                    continue
                label = re.sub(r"[^a-z]", "", row[0].lower())
                if label not in _FINANCIAL_FIELDS:
                    continue
                for index, cell in enumerate(row[1:21], 1):
                    if (parsed := number(cell)) is not None:
                        period = day(table[0][index][:10]) if index < len(table[0]) else None
                        facts.append({"field": label, "period": period, "value": parsed})
        else:
            for line in lines[:100]:
                if ":" not in line:
                    continue
                label, value = line.split(":", 1)
                if re.sub(r"[^a-z]", "", label.lower()) in _FINANCIAL_FIELDS:
                    match = re.fullmatch(r"\s*([-+\d.eE]+)(?: [A-Z]{3})?\s*", value)
                    if match and (parsed := number(match[1])) is not None:
                        facts.append(
                            {
                                "field": re.sub(r"[^a-z]", "", label.lower()),
                                "period": None,
                                "value": parsed,
                            }
                        )
    facts = facts[:300]
    periods = [row["period"] for row in facts if row["period"]]
    return (
        {"facts": facts},
        "validated"
        if len(facts) >= 2 and periods
        else "available"
        if len(facts) >= 2
        else "unknown",
        max(periods) if periods else None,
    )


def _news(text, payload, requested):
    articles = []
    feed = payload.get("feed") if isinstance(payload, dict) else None
    if isinstance(feed, list):
        for row in feed[:100]:
            if not isinstance(row, dict):
                continue
            title, url = public_text(row.get("title")), public_url(row.get("url"))
            stamp = row.get("time_published")
            try:
                published = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
            except (TypeError, ValueError):
                published = None
            if title and url and (published is None or published.date().isoformat() <= requested):
                articles.append(
                    {
                        "title": title,
                        "url": url,
                        "published_at": published.isoformat() if published else None,
                    }
                )
    else:
        blocks = re.split(r"(?m)^### ", text)
        for block in blocks[1:101]:
            lines = block.splitlines()
            title = public_text(lines[0]) if lines else None
            url = next(
                (public_url(line[6:].strip()) for line in lines if line.startswith("Link: ")), None
            )
            if title and url:
                articles.append({"title": title, "url": url, "published_at": None})
    dates = [row["published_at"] for row in articles if row["published_at"]]
    validated = bool(articles) and len(dates) == len(articles)
    return (
        {"articles": articles},
        "validated" if validated else "available" if articles else "unknown",
        max(dates) if dates else None,
    )


def _social(text, requested, start):
    posts = []
    for stamp, content in re.findall(r"(?m)^\s*\[(\d{4}-\d{2}-\d{2})[^\]\n]*\]\s+([^\n]+)", text):
        if (
            day(stamp)
            and (not start or stamp >= start)
            and stamp <= requested
            and (safe := public_text(content, 280))
        ):
            posts.append({"date": stamp, "excerpt": safe})
    posts = posts[:60]
    return (
        {"posts": posts},
        "available" if posts else "unknown",
        max((row["date"] for row in posts), default=None),
    )


def normalize_observation(
    method,
    provider,
    args,
    kwargs,
    response,
    outcome,
    error_type,
    *,
    symbol,
    analysis_date,
    received_at,
    redactions=(),
):
    category, request = safe_request(method, args, kwargs, symbol)
    details = {
        "method": method if method in METHODS else "unknown",
        "category": category,
        "provider": provider if provider in _PROVIDERS else "unknown",
        "request": request,
        "requested_as_of": request.get("as_of_date", request.get("end_date", analysis_date)),
        "data_as_of": None,
        "status": "unknown",
        "response_hash": None,
        "truncated": False,
        "issues": [],
        "price_validated": False,
    }
    if outcome != "success":
        code = (
            "provider_not_configured"
            if error_type and "NotConfigured" in error_type
            else "provider_request_failed"
        )
        details.update(
            status="error", issues=[issue(code, "此实际来源未返回可用结果；未保存上游异常正文")]
        )
        return details, None
    if isinstance(response, str):
        raw, payload = response, None
        with suppress(ValueError, TypeError):
            payload = json.loads(raw)
    elif isinstance(response, (dict, list)):
        try:
            raw, payload = json.dumps(response, ensure_ascii=False, allow_nan=False), response
        except (ValueError, TypeError):
            raw, payload = "", None
    else:
        raw, payload = "", None
    details["response_hash"] = hashlib.sha256(raw.encode()).hexdigest() if raw else None
    if len(raw) > MAX_RESPONSE_CHARS:
        details.update(
            truncated=True,
            issues=[issue("response_limit", "响应超出解析上限，仅保留哈希，未保存原文")],
        )
        return details, None
    for secret in redactions:
        if isinstance(secret, str) and secret:
            for variant in {
                secret,
                quote(secret, safe=""),
                quote_plus(secret),
                json.dumps(secret)[1:-1],
            }:
                raw = raw.replace(variant, "[redacted]")
    payload = None
    with suppress(ValueError, TypeError):
        payload = json.loads(raw)
    if not raw.strip() or raw.lstrip().startswith(("DATA_UNAVAILABLE", "NO_DATA_AVAILABLE", "<")):
        details.update(
            status="unavailable",
            issues=[issue("source_unavailable", "来源返回空值、不可用或历史时点保护说明")],
        )
        return details, None
    body = {}
    try:
        if category == "market":
            if request.get("symbol") != symbol or not re.fullmatch(
                r"[A-Z][A-Z0-9-]*(?:\.[AB])?", symbol
            ):
                details["issues"] = [
                    issue("market_scope_unknown", "关键价格未满足同证券美股校验范围")
                ]
            else:
                body, details["status"], details["data_as_of"], details["issues"] = _market(
                    method, raw, analysis_date, received_at
                )
                details["price_validated"] = details["status"] == "validated"
        elif category == "fundamentals":
            body, details["status"], details["data_as_of"] = _financial(raw, payload)
        elif category == "news":
            body, details["status"], details["data_as_of"] = _news(raw, payload, analysis_date)
        elif category == "social":
            body, details["status"], details["data_as_of"] = _social(
                raw, analysis_date, request.get("start_date")
            )
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, csv.Error):
        body = {}
        details.update(
            status="unknown",
            price_validated=False,
            data_as_of=None,
            issues=[issue("unrecognized_fields", "字段或日历未通过校验；未保存未知响应正文")],
        )
    if (
        category == "fundamentals"
        and details["data_as_of"]
        and details["data_as_of"] > analysis_date
    ):
        body, details["status"], details["data_as_of"] = {}, "unknown", None
        details["issues"] = [
            issue("future_financial_period", "财报期间晚于研究日期，未作为可用证据")
        ]
    if provider not in _PROVIDERS:
        body = {}
        details.update(
            status="unknown",
            price_validated=False,
            data_as_of=None,
            issues=[issue("source_unknown", "实际来源不在已识别适配器范围，未提升证据资格")],
        )
    if details["status"] == "unknown" and not details["issues"]:
        details["issues"] = [
            issue("unrecognized_fields", "响应结构未通过本批字段校验，不以文本存在证明证据充分")
        ]
    encoded = (
        json.dumps(body, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
        if body
        else b""
    )
    if len(encoded) > MAX_BODY_BYTES:
        details.update(
            status="unknown",
            truncated=True,
            price_validated=False,
            issues=[issue("body_limit", "白名单正文超出保存上限，仅保留哈希")],
        )
        return details, None
    return details, encoded.decode() if encoded else None


def qualify(records, analysts, *, capture_started, checked_at=None, capture_issues=(), required_categories=None):
    required = sorted(
        {
            {
                "market": "market",
                "fundamentals": "fundamentals",
                "news": "news",
                "social": "social",
            }[name]
            for name in analysts
            if name in {"market", "fundamentals", "news", "social"}
        }
        | {"market"}
    )
    if required_categories is not None:
        required = sorted(set(required_categories) | {"market"})
    available = sorted(
        {row["category"] for row in records if row.get("status") in {"validated", "available"}}
    )
    missing = [key for key in required if key not in available]
    price = any(row.get("price_validated") is True for row in records)
    problems = list(capture_issues)
    if not capture_started:
        status = "unverified"
        problems.append(
            issue("legacy_no_capture", "此研究没有工具证据捕获记录，不能由模型报告反推充分性")
        )
    elif not price:
        status = "insufficient"
        problems.append(
            issue("verified_price_missing", "缺少通过日期及OHLCV字段校验的关键价格证据")
        )
    else:
        verified = {row["category"] for row in records if row.get("status") == "validated"}
        status = (
            "usable"
            if not missing
            and set(required) <= verified
            and not problems
            and all(row.get("status") == "validated" for row in records)
            else "degraded"
        )
    if missing:
        problems.append(
            issue(
                "required_source_missing", "缺少本次已选研究维度的可解析来源：" + "、".join(missing)
            )
        )
    if any(row.get("status") in {"unknown", "error", "unavailable"} for row in records):
        problems.append(issue("source_gaps", "实际请求包含失败、不可用或未知结构；逐条见证据记录"))
    problems.append(
        issue(
            "proposal_not_validated",
            "模型报告内的价格目标与仓位尚未独立校验，不发布为有效结构化价格/仓位",
        )
    )
    return {
        "status": status,
        "rule_version": "research-quality-v1",
        "checked_at": checked_at,
        "evidence_count": len(records),
        "validated_price": price,
        "eligible_for_rating": capture_started and price and not missing and not capture_issues,
        "eligible_for_levels": False,
        "eligible_for_sizing": False,
        "required_categories": required,
        "available_categories": available,
        "missing_categories": missing,
        "issues": problems,
    }
