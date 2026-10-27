"""Deterministic daily technical observations; no providers, model calls or trading."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from exchange_calendars import get_calendar

from tradingagents.web.api.tracking_schemas import TrackingCoverage, TrackingMeta, TrackingRules
from tradingagents.web.domain.technical_methods import wilder_smoothing

REQUIRED_SESSIONS = 65


def tracking_rules():
    definitions = [
        (
            "price",
            "价格与交易日",
            "price为最近已结束XNYS常规交易日close；previous_close为该日前一XNYS完整日close。不是盘中价或供应商官方昨收；仅provider_native口径。",
        ),
        (
            "averages",
            "MA20 / MA50",
            "MA(N)=截至as_of的最近N个连续完整交易日close算术均值；窗口不足、缺日或非正close则该均线为空。趋势仅C>MA20>MA50为uptrend，C<MA20<MA50为downtrend，其余mixed。",
        ),
        (
            "atr",
            "ATR14 · Wilder",
            "固定取截至as_of的65个连续完整日OHLC，产生64个TR；TR=max(H−L,abs(H−前日C),abs(L−前日C))。首14个TR算术均值为种子，此后ATR=(13×前ATR+本TR)/14；非无限历史种子，可能与其他终端略异。",
        ),
        (
            "volatility",
            "技术波动等级1–5",
            "ATR%=100×ATR14/C；<1为1，1≤x<2为2，2≤x<3为3，3≤x<5为4，x≥5为5。仅本产品固定技术波动分档，非信用/适当性/组合风险等级。",
        ),
        (
            "channel",
            "技术观察区间",
            "上沿=max(as_of日前20个连续完整日high)，下沿=min(同期low)，不含as_of当日。C>上沿为above_range，C<下沿为below_range；区间内距最近边界≤0.5ATR且ATR>0时near_upper/near_lower，其余inside_range。通道窗口不足、缺日或OHLC无效则不画通道。",
        ),
        (
            "reference",
            "波动参考目标与保护参考",
            "仅above_range且C>MA20>MA50时目标=C+2ATR、保护=C−1.5ATR；仅below_range且C<MA20<MA50时目标=C−2ATR、保护=C+1.5ATR。须ATR>0、两参考均为正且方向正确，否则都为空；这些是机械距离参考，不是价格预测或下单指令。",
        ),
        (
            "quality",
            "缺失与可用性",
            "目标/保护须65日OHLC窗口完整、无缺口/脏价/重复证据且数据未过期；保留原source_quality。原partial仅在确认由未收盘柱造成、完整已收盘数据和来源覆盖均通过复核时可恢复窗口complete。未知原因partial、stale、无效元数据不生成目标/保护。",
        ),
    ]
    return TrackingRules(
        formulas=[
            {"id": key, "label": label, "description": description}
            for key, label, description in definitions
        ],
        references=[
            "https://www.tradingview.com/support/solutions/43000501823-average-true-range-atr/",
            "https://www.tradingview.com/support/solutions/43000734653-how-are-adr-and-atr-calculated/",
            "https://www.tradingview.com/support/solutions/43000502253-donchian-channels-dc/",
        ],
    )


def positive(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def valid_ohlc(row):
    if not isinstance(row, Mapping) or not all(
        positive(row.get(key)) for key in ("open", "high", "low", "close")
    ):
        return False
    return (
        row["low"]
        <= min(row["open"], row["close"])
        <= max(row["open"], row["close"])
        <= row["high"]
    )


def wilder_atr(rows, period=14):
    """Use all supplied, already validated contiguous rows; seed exactly period TRs."""
    if len(rows) < period + 1 or not all(valid_ohlc(row) for row in rows):
        return None
    ranges = [
        max(
            row["high"] - row["low"],
            abs(row["high"] - previous["close"]),
            abs(row["low"] - previous["close"]),
        )
        for previous, row in zip(rows, rows[1:], strict=False)
    ]
    value = wilder_smoothing(ranges, period)[-1]
    return value if math.isfinite(value) and value >= 0 else None


def volatility_level(atr_percent):
    if atr_percent is None or not math.isfinite(atr_percent) or atr_percent < 0:
        return None
    return 1 + sum(atr_percent >= threshold for threshold in (1, 2, 3, 5))


def closed_sessions(now):
    if now.tzinfo is None:
        raise ValueError("Tracking clock must have timezone")
    now = now.astimezone(UTC)
    day = now.astimezone(ZoneInfo("America/New_York")).date()
    calendar = get_calendar("XNYS", start=day - timedelta(days=550), end=day + timedelta(days=10))
    return [
        stamp.strftime("%Y-%m-%d")
        for stamp in calendar.sessions_in_range(day - timedelta(days=500), day)
        if calendar.session_close(stamp).to_pydatetime() <= now
    ]


def _timestamp(value):
    try:
        stamp = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        return stamp.astimezone(UTC) if stamp.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def analyze_tracking(response, now):
    """Return only observed/derived values; lack of coverage never becomes a zero."""
    now = now.astimezone(UTC) if now.tzinfo is not None else now
    sessions = closed_sessions(now)
    latest_expected = sessions[-1] if sessions else None
    response = response if isinstance(response, Mapping) else {}
    raw_meta = response.get("meta") if isinstance(response.get("meta"), Mapping) else {}
    source = raw_meta.get("source") if isinstance(raw_meta.get("source"), str) else None
    feed = raw_meta.get("feed") if isinstance(raw_meta.get("feed"), str) else None
    source_quality = raw_meta.get("quality") if isinstance(raw_meta.get("quality"), str) else None
    source_warning = raw_meta.get("warning") if isinstance(raw_meta.get("warning"), str) else None
    source_received = _timestamp(raw_meta.get("received_at"))
    fields = dict.fromkeys(
        (
            "price",
            "previous_close",
            "change",
            "change_percent",
            "ma20",
            "ma50",
            "atr14",
            "atr_percent",
            "range_low",
            "range_high",
            "reference_target",
            "protection_level",
            "technical_volatility_level",
        )
    )
    fields.update(
        trend="unknown", observation="unavailable", eligible_for_levels=False, rule_reasons=[]
    )
    coverage = TrackingCoverage(
        available_sessions=0,
        missing_sessions=65,
        invalid_price_sessions=0,
        duplicate_sessions=0,
        excluded_unclosed=0,
        window_start=None,
        window_end=None,
        expected_latest_session=latest_expected,
    )
    meta = TrackingMeta(
        source=source,
        feed=feed,
        as_of=None,
        previous_as_of=None,
        received_at=now,
        source_received_at=source_received,
        source_quality=source_quality,
        source_warning=source_warning,
        quality="unavailable",
        coverage=coverage,
        warnings=["价格为最近完整日收盘；技术观察及波动距离不是估值、买卖建议或自动交易。"],
    )
    fields["meta"] = meta
    if not (
        source
        and feed
        and response.get("interval") == "1d"
        and response.get("session") == "regular"
        and response.get("adjustment") == "provider_native"
        and source_quality in {"complete", "partial", "stale"}
    ):
        fields["rule_reasons"].append(
            "来源/feed/日线/常规时段/调整口径或数据质量不可验证，未生成观察。"
        )
        return fields
    bars = response.get("bars") if isinstance(response.get("bars"), list) else []
    local_day = now.astimezone(ZoneInfo("America/New_York")).date()
    calendar = get_calendar(
        "XNYS", start=local_day - timedelta(days=550), end=local_day + timedelta(days=10)
    )
    today_unclosed = (
        calendar.is_session(local_day) and calendar.session_close(local_day).to_pydatetime() > now
    )
    session_set = set(sessions)
    by_day, duplicates, invalid_rows, nonclosed = {}, set(), 0, 0
    for row in bars:
        if not isinstance(row, Mapping):
            invalid_rows += 1
            continue
        day = row.get("date")
        if not isinstance(day, str):
            invalid_rows += 1
            continue
        try:
            parsed = datetime.strptime(day, "%Y-%m-%d").date().isoformat()
            if parsed != day:
                raise ValueError
        except ValueError:
            invalid_rows += 1
            continue
        if day not in session_set:
            if day == local_day.isoformat() and today_unclosed:
                nonclosed += 1
                if (
                    nonclosed > 1
                    or row.get("session") != "regular"
                    or row.get("is_complete") is not False
                ):
                    invalid_rows += 1
            else:
                invalid_rows += 1
            continue
        if row.get("session") != "regular" or row.get("is_complete") is not True:
            invalid_rows += 1
            continue
        if day in by_day:
            duplicates.add(day)
            by_day[day] = None
        else:
            by_day[day] = row
    coverage.excluded_unclosed = nonclosed
    if not by_day:
        fields["rule_reasons"].append("来源未返回已完成的XNYS日线。")
        return fields
    as_of = max(by_day)
    meta.as_of = as_of
    position = sessions.index(as_of)
    window = sessions[max(0, position - 64) : position + 1]
    window_rows = [by_day.get(day) for day in window]
    coverage.window_start, coverage.window_end = window[0], window[-1]
    coverage.available_sessions = sum(row is not None for row in window_rows)
    coverage.missing_sessions = 65 - coverage.available_sessions
    coverage.invalid_price_sessions = sum(
        row is not None and not valid_ohlc(row) for row in window_rows
    )
    coverage.duplicate_sessions = len(duplicates)
    latest = by_day.get(as_of)
    previous_day = sessions[position - 1] if position > 0 else None
    previous = by_day.get(previous_day)
    fields["price"] = (
        latest["close"] if latest is not None and positive(latest.get("close")) else None
    )
    fields["previous_close"] = (
        previous["close"] if previous is not None and positive(previous.get("close")) else None
    )
    meta.previous_as_of = previous_day if fields["previous_close"] is not None else None
    price, prior = fields["price"], fields["previous_close"]
    if price is not None and prior is not None:
        fields["change"] = price - prior
        percent = (price / prior - 1) * 100
        fields["change_percent"] = percent if math.isfinite(percent) else None
    for period, field in ((20, "ma20"), (50, "ma50")):
        values = [
            by_day.get(day, {}).get("close") if by_day.get(day) is not None else None
            for day in window[-period:]
        ]
        if len(values) == period and all(positive(value) for value in values):
            fields[field] = mean(values)
    complete_window = len(window_rows) == 65 and all(valid_ohlc(row) for row in window_rows)
    if complete_window:
        fields["atr14"] = wilder_atr(window_rows)
        atr_percent = (
            fields["atr14"] / price * 100 if price and fields["atr14"] is not None else None
        )
        fields["atr_percent"] = (
            atr_percent if atr_percent is not None and math.isfinite(atr_percent) else None
        )
        fields["technical_volatility_level"] = volatility_level(fields["atr_percent"])
    ma20, ma50, atr = fields["ma20"], fields["ma50"], fields["atr14"]
    if price is not None and ma20 is not None and ma50 is not None:
        fields["trend"] = (
            "uptrend" if price > ma20 > ma50 else "downtrend" if price < ma20 < ma50 else "mixed"
        )
    prior_twenty = [by_day.get(day) for day in window[-21:-1]]
    if len(prior_twenty) == 20 and all(valid_ohlc(row) for row in prior_twenty):
        lower, upper = (
            min(row["low"] for row in prior_twenty),
            max(row["high"] for row in prior_twenty),
        )
        fields["range_low"], fields["range_high"] = lower, upper
        if price is not None:
            observation = (
                "above_range"
                if price > upper
                else "below_range"
                if price < lower
                else "inside_range"
            )
            if observation == "inside_range" and atr is not None and atr > 0 and upper > lower:
                low_distance, high_distance = price - lower, upper - price
                if low_distance != high_distance and min(low_distance, high_distance) <= 0.5 * atr:
                    observation = "near_lower" if low_distance < high_distance else "near_upper"
            fields["observation"] = observation
    raw_coverage = raw_meta.get("coverage") if isinstance(raw_meta.get("coverage"), Mapping) else {}
    closed_rows = [row for row in by_day.values() if row is not None]
    closed_dates = set(by_day)
    expected_raw_dates = set(sessions[sessions.index(min(by_day)) : position + 1])
    safe_partial = (
        source_quality == "partial"
        and nonclosed == 1
        and invalid_rows == 0
        and not duplicates
        and raw_coverage.get("dropped_count") == 0
        and raw_coverage.get("duplicate_count") == 0
        and closed_dates == expected_raw_dates
        and all(valid_ohlc(row) for row in closed_rows)
        and all(
            isinstance(row.get("volume"), (int, float))
            and not isinstance(row.get("volume"), bool)
            and math.isfinite(row["volume"])
            and row["volume"] >= 0
            for row in closed_rows
        )
    )
    stale = (
        as_of != latest_expected or source_quality == "stale" or raw_meta.get("cache") == "stale"
    )
    as_of_close = calendar.session_close(as_of).to_pydatetime()
    valid_received = source_received is not None and as_of_close <= source_received <= now
    valid_derived = (
        fields["atr14"] is not None
        and fields["atr_percent"] is not None
        and (prior is None or fields["change_percent"] is not None)
    )
    healthy = (
        complete_window
        and not duplicates
        and invalid_rows == 0
        and valid_received
        and valid_derived
        and (source_quality == "complete" or safe_partial)
        and raw_coverage.get("dropped_count", 0) == 0
        and raw_coverage.get("duplicate_count", 0) == 0
    )
    meta.quality = "stale" if stale else "complete" if healthy else "partial"
    if stale:
        fields["rule_reasons"].append("来源落后于最近已结束交易日或被标记过期；目标/保护参考为空。")
    if not complete_window:
        fields["rule_reasons"].append(
            f"固定65日窗口缺少{coverage.missing_sessions}个日期、{coverage.invalid_price_sessions}个日期含无效OHLC；不足或缺口不生成目标/保护。"
        )
    if (
        duplicates
        or invalid_rows
        or raw_coverage.get("dropped_count", 0)
        or raw_coverage.get("duplicate_count", 0)
    ):
        fields["rule_reasons"].append("来源有重复、无效或被丢弃行的证据，未据此生成目标/保护。")
    if source_quality == "partial":
        fields["rule_reasons"].append(
            "原始partial经复核仅涉及排除的未完日柱，已收盘窗口完整。"
            if safe_partial
            else "来源partial原因不能完整排除，保留部分数据状态，不生成目标/保护。"
        )
    if not valid_received:
        fields["rule_reasons"].append(
            "来源接收时刻未知、超前或早于所声称完整日的收盘时刻，时效元数据未通过校验。"
        )
    if complete_window and not valid_derived:
        fields["rule_reasons"].append("派生数值超出有限数值范围，保留缺失并暂停目标/保护参考。")
    scenario = (
        1
        if fields["observation"] == "above_range" and fields["trend"] == "uptrend"
        else -1
        if fields["observation"] == "below_range" and fields["trend"] == "downtrend"
        else 0
    )
    if (
        meta.quality == "complete"
        and scenario
        and price is not None
        and atr is not None
        and atr > 0
    ):
        target, protection = price + scenario * 2 * atr, price - scenario * 1.5 * atr
        if (
            positive(target)
            and positive(protection)
            and (target - price) * scenario > 0
            and (price - protection) * scenario > 0
        ):
            fields["reference_target"], fields["protection_level"] = target, protection
            fields["eligible_for_levels"] = True
            fields["rule_reasons"].append(
                "一致趋势与区间突破满足；目标/保护仅为close锚定的±2ATR/反侧1.5ATR机械距离。"
            )
    if not fields["eligible_for_levels"] and meta.quality == "complete":
        fields["rule_reasons"].append(
            "未满足一致趋势与突破场景，或参考值非正/零ATR；不生成目标/保护。"
        )
    fields["rule_reasons"].append(
        "MA为收盘均线；ATR衡量幅度而非涨跌方向，技术波动等级不等于信用风险。"
    )
    return fields
