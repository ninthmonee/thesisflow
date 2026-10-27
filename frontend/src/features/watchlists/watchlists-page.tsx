import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Input,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  message,
} from "antd";
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DeleteOutlined,
  EditOutlined,
  LineChartOutlined,
  PlusOutlined,
  ReloadOutlined,
  StarFilled,
  StarOutlined,
} from "@ant-design/icons";
import {
  Link,
  useLocation,
  useNavigate,
  useNavigationType,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addWatchlistItem,
  createWatchlist,
  deleteWatchlist,
  getWatchlistTracking,
  getWatchlistTrackingRules,
  listWatchlists,
  removeWatchlistItem,
  renameWatchlist,
  reorderWatchlistItems,
  searchInstruments,
  updateWatchlistObservation,
} from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  InstrumentResponse,
  WatchlistItemResponse,
  WatchlistTrackingItem,
} from "../../shared/api/generated/models";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import { EquityPanel } from "../market/equity-panel";
import { publicLink } from "../themes/theme-utils";
import { resolveEquitySelection } from "./equity-selection";
import {
  collectTrackingRows,
  isCompanyInstrument,
  readTrackingView,
  reduceTrackingSearch,
  trackingPercent,
  trackingSearchParams,
  trackingSelectionUrl,
  trackingSortLabels,
  visibleTrackingRows,
} from "./tracking-utils";
import type {
  TrackingMembership,
  TrackingRow,
  TrackingSearchAction,
  TrackingSort,
} from "./tracking-utils";
import shared from "../../shared/ui/workbench.module.css";
import styles from "./watchlists.module.css";

const observationLabels: Record<string, string> = {
  above_range: "上破观察区间",
  below_range: "下破观察区间",
  near_upper: "接近区间上沿",
  near_lower: "接近区间下沿",
  inside_range: "区间内",
  unavailable: "观察数据不足",
};
const trendLabels: Record<string, string> = {
  uptrend: "向上排列",
  downtrend: "向下排列",
  mixed: "趋势交错",
  unknown: "趋势未知",
};
function failure(error: unknown) {
  return error instanceof ApiError ? error.message : "操作未完成，请稍后重试";
}
function number(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value)
    ? "—"
    : value.toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
}
function color(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) || value === 0
    ? undefined
    : value > 0
      ? styles.positive
      : styles.negative;
}
function time(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return "—";
  return (
    new Intl.DateTimeFormat("zh-CN", {
      timeZone: "America/New_York",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(value)) + " ET"
  );
}

export function WatchlistsPage({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const scope = scopeForWorkspace(bootstrap.workspace),
    cache = useQueryClient();
  const navigate = useNavigate(),
    { instrumentId } = useParams();
  const [params, setParams] = useSearchParams();
  const view = readTrackingView(params);
  const location = useLocation(),
    navigationType = useNavigationType();
  const [filterState, setFilterState] = useState(() => ({
    draft: view.query,
    urlValue: view.query,
    ownCommits: [] as string[],
  }));
  const searchSync = useRef(filterState);
  const updateSearch = useCallback((action: TrackingSearchAction) => {
    const next = reduceTrackingSearch(searchSync.current, action);
    searchSync.current = next;
    setFilterState(next);
  }, []);
  useEffect(() => {
    updateSearch({ type: "url", value: view.query, pop: navigationType === "POP" });
  }, [location.key, navigationType, updateSearch, view.query]);
  const [notices, noticeContext] = message.useMessage();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<string>();
  const inFlight = useRef(false),
    forceTracking = useRef(false);
  const [groupEditor, setGroupEditor] = useState<"new" | "rename" | null>(null);
  const [groupName, setGroupName] = useState("");
  const [undo, setUndo] = useState<{ groupId: string; instrumentId: string; symbol: string }>();
  const [adding, setAdding] = useState(false),
    [addGroupId, setAddGroupId] = useState<string>();
  const [addModalClosed, setAddModalClosed] = useState(true);
  const [search, setSearch] = useState(""),
    [searchInput, setSearchInput] = useState("");
  const [viewedInstrument, setViewedInstrument] = useState<InstrumentResponse>();
  const [noteEditor, setNoteEditor] = useState<TrackingRow>(),
    [noteDraft, setNoteDraft] = useState("");
  const [noteConflict, setNoteConflict] = useState<{ latest?: WatchlistItemResponse }>();
  const [removeEditor, setRemoveEditor] = useState<TrackingRow>();
  const [rulesItem, setRulesItem] = useState<WatchlistTrackingItem | null | undefined>();
  const chartAnchor = useRef<HTMLDivElement>(null);
  const [scrollTarget, setScrollTarget] = useState<string>();
  const groups = useQuery({
    queryKey: queryKeys.resource(scope, "watchlists"),
    queryFn: ({ signal }) => listWatchlists({ signal }),
    retry: shouldRetryQuery,
  });
  const allGroups = groups.data ?? [];
  const groupId = params.get("group");
  const selected =
    groupId === "all"
      ? undefined
      : (allGroups.find((group) => group.id === groupId) ?? allGroups[0]);
  const rows = collectTrackingRows(allGroups, selected?.id);
  const inventory = collectTrackingRows(allGroups);
  const equitySelection = resolveEquitySelection({
    instrumentId,
    explicitSymbol: params.get("symbol"),
    groups: groups.data,
    selectedGroupId: selected?.id,
    status: groups.status,
  });
  const symbol = equitySelection.state === "ready" ? equitySelection.symbol : undefined;
  const selectedInstrument =
    equitySelection.state === "ready"
      ? (equitySelection.instrument ??
        (viewedInstrument?.symbol === symbol ? viewedInstrument : undefined))
      : undefined;
  useEffect(() => {
    if (!scrollTarget || symbol !== scrollTarget || !view.chart || !addModalClosed) return;
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => {
        chartAnchor.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        setScrollTarget(undefined);
      });
    });
    return () => {
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
    };
  }, [addModalClosed, scrollTarget, symbol, view.chart]);
  const tracking = useQuery({
    queryKey: queryKeys.resource(scope, "watchlist-tracking", {
      group: selected?.id ?? "all",
      revisions: allGroups
        .filter((group) => !selected || group.id === selected.id)
        .map((group) => [group.id, group.revision]),
    }),
    queryFn: async ({ signal }) => {
      const refresh = forceTracking.current;
      try {
        return await getWatchlistTracking(
          { watchlist_id: selected?.id, limit: 100, refresh },
          { signal },
        );
      } finally {
        if (refresh) forceTracking.current = false;
      }
    },
    enabled: groups.isSuccess && rows.length > 0,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    retry: shouldRetryQuery,
  });
  const metrics = new Map((tracking.data?.items ?? []).map((item) => [item.instrument_id, item]));
  const latestSourceAt = (tracking.data?.items ?? []).reduce<string | undefined>((latest, item) => {
    const stamp = item.meta.source_received_at;
    return stamp &&
      Number.isFinite(Date.parse(stamp)) &&
      (!latest || Date.parse(stamp) > Date.parse(latest))
      ? stamp
      : latest;
  }, undefined);
  const visible = visibleTrackingRows(rows, {
    query: filterState.draft,
    favoritesOnly: view.favoritesOnly,
    sort: view.sort,
    metrics,
  });
  const results = useQuery({
    queryKey: queryKeys.resource(scope, "instrument-search", { q: search }),
    queryFn: ({ signal }) => searchInstruments({ q: search }, { signal }),
    enabled: adding && !!search,
    retry: shouldRetryQuery,
    staleTime: 30_000,
  });
  const rules = useQuery({
    queryKey: queryKeys.resource(scope, "watchlist-tracking-rules"),
    queryFn: ({ signal }) => getWatchlistTrackingRules({ signal }),
    enabled: rulesItem !== undefined && !tracking.data?.rules,
    staleTime: 300_000,
    retry: shouldRetryQuery,
  });
  const currentRules = tracking.data?.rules ?? rules.data;
  const addGroup = allGroups.find((group) => group.id === addGroupId) ?? selected ?? allGroups[0];
  const selectionOptions = rows.map((row) => ({
    value: row.instrument.id,
    label: `${row.instrument.symbol} · ${row.instrument.name}`,
  }));
  const selectionValue = selectedInstrument?.id ?? (symbol ? `symbol:${symbol}` : undefined);
  if (symbol && !selectionOptions.some((item) => item.value === selectionValue))
    selectionOptions.unshift({
      value: selectionValue ?? `symbol:${symbol}`,
      label: `${symbol} · ${selectedInstrument?.name ?? "当前查看，未在此分组"}`,
    });

  function changeView(values: Record<string, string | undefined>) {
    const query = Object.hasOwn(values, "q") ? (values.q ?? "") : searchSync.current.draft;
    const next = trackingSearchParams(params, query);
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined || value === "") next.delete(key);
      else next.set(key, value);
    }
    updateSearch({ type: "commit", value: query });
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  }
  function chooseInstrument(instrument: InstrumentResponse, chart?: boolean) {
    setViewedInstrument(instrument);
    const query = searchSync.current.draft;
    updateSearch({ type: "commit", value: query });
    setScrollTarget(chart ? instrument.symbol : undefined);
    void navigate(trackingSelectionUrl(trackingSearchParams(params, query), instrument, chart));
  }
  async function refresh(includeMarket = true) {
    const refreshes = [
      cache.invalidateQueries({ queryKey: queryKeys.resource(scope, "watchlists") }),
    ];
    if (includeMarket)
      refreshes.push(
        cache.invalidateQueries({ queryKey: [...queryKeys.scope(scope), "watchlist-tracking"] }),
      );
    await Promise.all(refreshes);
  }
  async function act(action: () => Promise<unknown>, success: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(undefined);
    try {
      await action();
      await refresh(false);
      void notices.success(success);
    } catch (problem) {
      setError(failure(problem));
      if (problem instanceof ApiError && problem.status === 409) await refresh(false);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  function saveGroup() {
    const name = groupName.trim();
    if (!name) {
      setError("请输入分组名称");
      return;
    }
    void act(
      async () => {
        if (groupEditor === "new") {
          const group = await createWatchlist({ name });
          changeView({ group: group.id });
        } else if (selected)
          await renameWatchlist(selected.id, { name, revision: selected.revision });
        setGroupEditor(null);
        setGroupName("");
      },
      groupEditor === "new" ? "分组已创建" : "分组已重命名",
    );
  }
  function moveItem(row: TrackingRow, offset: number) {
    if (!selected) return;
    const item = row.memberships.find((entry) => entry.groupId === selected.id);
    if (!item) return;
    const ids = selected.items.map((entry) => entry.id),
      index = ids.indexOf(item.itemId),
      next = index + offset;
    const a = ids[index],
      b = ids[next];
    if (!a || !b) return;
    ids[index] = b;
    ids[next] = a;
    void act(
      () => reorderWatchlistItems(selected.id, { item_ids: ids, revision: selected.revision }),
      "分组顺序已保存",
    );
  }
  function remove(row: TrackingRow, relation: TrackingMembership) {
    void act(async () => {
      const group = allGroups.find((item) => item.id === relation.groupId);
      if (!group)
        throw new ApiError({
          status: 404,
          code: "group_missing",
          message: "分组已不存在，请刷新列表",
        });
      await removeWatchlistItem(group.id, relation.itemId, { revision: group.revision });
      setUndo({
        groupId: group.id,
        instrumentId: row.instrument.id,
        symbol: row.instrument.symbol,
      });
      setRemoveEditor(undefined);
    }, "已移除该分组中的关注，备注与研究记录保留");
  }
  function favorite(row: TrackingRow) {
    void act(
      () =>
        updateWatchlistObservation(row.instrument.id, {
          revision: row.observationRevision,
          favorite: !row.favorite,
        }),
      row.favorite ? "已取消收藏" : "已收藏；在所有分组共享",
    );
  }
  function saveNote() {
    if (!noteEditor || noteConflict) return;
    void act(async () => {
      try {
        await updateWatchlistObservation(noteEditor.instrument.id, {
          revision: noteEditor.observationRevision,
          notes: noteDraft,
        });
        setNoteEditor(undefined);
      } catch (problem) {
        if (problem instanceof ApiError && problem.code === "observation_revision_conflict") {
          const latest = await cache.fetchQuery({
            queryKey: queryKeys.resource(scope, "watchlists"),
            queryFn: () => listWatchlists(),
            staleTime: 0,
          });
          setNoteConflict({
            latest: latest
              .flatMap((group) => group.items)
              .find((item) => item.instrument.id === noteEditor.instrument.id),
          });
        }
        throw problem;
      }
    }, "备注已保存；同一证券跨分组共享");
  }

  return (
    <div className={shared.stack}>
      {noticeContext}
      <div className={shared.heading}>
        <small>EQUITY TRACKER · RESEARCH WORKSPACE</small>
        <h1>股票与ETF跟踪</h1>
        <p>自己的关注列表、真实日级数据与公开规则观察。图表和核心个股研究在同一工作台切换。</p>
      </div>
      {(error || groups.isError) && (
        <Alert
          type="error"
          showIcon
          title={error ?? "关注列表读取失败"}
          action={
            <Button
              onClick={() => {
                setError(undefined);
                void refresh();
              }}
            >
              刷新列表
            </Button>
          }
        />
      )}
      <section className={shared.panel}>
        <div className={styles.selectionBar}>
          <div className={styles.selection}>
            <small>查看标的</small>
            <Select
              aria-label="切换股票或ETF图表"
              showSearch
              optionFilterProp="label"
              value={selectionValue}
              options={selectionOptions}
              loading={groups.isPending}
              placeholder="选择关注的股票或ETF"
              onChange={(id: string) => {
                const instrument = inventory.find((row) => row.instrument.id === id)?.instrument;
                if (instrument) chooseInstrument(instrument);
              }}
            />
          </div>
          <label className={styles.chartToggle} htmlFor="watchlist-chart-toggle">
            <Switch
              id="watchlist-chart-toggle"
              checked={view.chart}
              onChange={(checked) => changeView({ chart: checked ? "1" : "0" })}
            />
            显示图表
          </label>
          <div className={shared.actions}>
            {isCompanyInstrument(selectedInstrument) && (
              <Link
                to={`/research/new?ticker=${encodeURIComponent(selectedInstrument?.symbol ?? "")}`}
              >
                <Button>TradingAgents个股研究</Button>
              </Link>
            )}
            <Button
              type="primary"
              icon={<PlusOutlined />}
              disabled={busy || !allGroups.length}
              onClick={() => {
                setAdding(true);
                setAddModalClosed(false);
                setScrollTarget(undefined);
                setSearch("");
                setSearchInput("");
                setAddGroupId(selected?.id ?? allGroups[0]?.id);
              }}
            >
              添加股票或ETF
            </Button>
          </div>
        </div>
        <div className={styles.selectionNote}>
          {selectedInstrument?.asset_type === "etf"
            ? "ETF查看行情与技术观察；公司基本面研究仅用于股票。"
            : "选择证券保留现有图表周期、指标和比较条件；收藏与备注在各分组间共享。"}
        </div>
      </section>
      {undo && (
        <Alert
          type="info"
          showIcon
          closable
          title={`已从分组移除 ${undo.symbol}，收藏和备注保留`}
          onClose={() => setUndo(undefined)}
          action={
            <Button
              size="small"
              disabled={busy}
              onClick={() => {
                const group = allGroups.find((entry) => entry.id === undo.groupId);
                if (!group) {
                  setError("原分组已不存在，无法撤销");
                  return;
                }
                void act(async () => {
                  await addWatchlistItem(group.id, {
                    instrument_id: undo.instrumentId,
                    revision: group.revision,
                  });
                  setUndo(undefined);
                }, "已恢复关注到原分组末尾");
              }}
            >
              撤销移除
            </Button>
          }
        />
      )}
      <section className={shared.panel}>
        <div className={shared.toolbar}>
          <div>
            <h2>跟踪列表</h2>
            <span className={shared.muted}>
              {visible.length} / {rows.length} 个证券 · {selected?.name ?? "全部分组，证券去重"}
            </span>
          </div>
          <div className={shared.actions}>
            <Button size="small" onClick={() => setRulesItem(null)}>
              规则依据
            </Button>
            <Button
              icon={<ReloadOutlined />}
              loading={tracking.isFetching || groups.isFetching}
              onClick={() => {
                forceTracking.current = true;
                void refresh();
              }}
            >
              刷新数据
            </Button>
          </div>
        </div>
        <div className={styles.filters}>
          <Input.Search
            aria-label="搜索跟踪列表代码公司或备注"
            placeholder="代码、公司或备注 · 回车保留筛选"
            value={filterState.draft}
            allowClear
            onChange={(event) => {
              const value = event.target.value;
              updateSearch({ type: "type", value });
              if (!value) changeView({ q: undefined });
            }}
            onSearch={(value) => {
              updateSearch({ type: "type", value });
              changeView({ q: value });
            }}
          />
          <Select
            aria-label="筛选关注分组"
            value={selected?.id ?? "all"}
            options={[
              { value: "all", label: "全部分组" },
              ...allGroups.map((group) => ({ value: group.id, label: group.name })),
            ]}
            onChange={(group: string) => changeView({ group })}
          />
          <Button
            icon={view.favoritesOnly ? <StarFilled /> : <StarOutlined />}
            type={view.favoritesOnly ? "primary" : "default"}
            onClick={() => changeView({ favorites: view.favoritesOnly ? undefined : "1" })}
          >
            只看收藏
          </Button>
          <Select
            aria-label="跟踪列表排序"
            value={view.sort}
            options={Object.entries(trackingSortLabels).map(([value, label]) => ({ value, label }))}
            onChange={(sort: TrackingSort) => changeView({ sort })}
          />
        </div>
        <div className={styles.groupActions}>
          <Button
            size="small"
            onClick={() => {
              setGroupEditor("new");
              setGroupName("");
            }}
            disabled={busy}
          >
            新建分组
          </Button>
          <Button
            size="small"
            onClick={() => {
              if (selected) {
                setGroupEditor("rename");
                setGroupName(selected.name);
              }
            }}
            disabled={busy || !selected}
          >
            重命名分组
          </Button>
          {selected && (
            <Popconfirm
              title={`删除分组“${selected.name}”？`}
              description="仅移除这个分组及其关注关系，其他分组、收藏备注和研究报告保留。"
              onConfirm={() => {
                void act(async () => {
                  await deleteWatchlist(selected.id, { revision: selected.revision });
                  changeView({ group: undefined });
                  setUndo(undefined);
                }, "分组已删除");
              }}
            >
              <Button size="small" danger disabled={busy || allGroups.length <= 1}>
                删除分组
              </Button>
            </Popconfirm>
          )}
          <span className={shared.muted}>
            同一证券跨分组仅一份收藏与备注。选择单一分组和原顺序可调整排列。
          </span>
        </div>
        {tracking.isError && (
          <div className={styles.dataNotice}>
            <Alert
              type="warning"
              showIcon
              title="跟踪行情暂时不可用"
              description={failure(tracking.error) + "；关注关系与备注仍可管理，不用模拟值替代。"}
            />
          </div>
        )}
        {tracking.data?.truncated && (
          <div className={styles.dataNotice}>
            <Alert
              type="info"
              title={`当前数据窗口返回${tracking.data.returned_count}/${tracking.data.total_count}个证券。未覆盖项保持空值，可切换到较小分组查看。`}
            />
          </div>
        )}
        <Table<TrackingRow>
          className={styles.trackingTable}
          rowKey={(row) => row.instrument.id}
          size="small"
          loading={groups.isPending}
          dataSource={visible}
          scroll={{ x: 1400 }}
          pagination={{ defaultPageSize: 15, showSizeChanger: true, pageSizeOptions: [15, 30, 50] }}
          rowClassName={(row) => (row.instrument.symbol === symbol ? (styles.activeRow ?? "") : "")}
          onRow={(row) => ({
            "aria-selected": row.instrument.symbol === symbol,
            onClick: (event) => {
              if (
                event.target instanceof Element &&
                event.target.closest("button,a,input,textarea,[role=button]")
              )
                return;
              chooseInstrument(row.instrument);
            },
            tabIndex: 0,
            onKeyDown: (event) => {
              if (event.target === event.currentTarget && event.key === "Enter")
                chooseInstrument(row.instrument);
            },
          })}
          locale={{
            emptyText:
              filterState.draft || view.favoritesOnly
                ? "没有符合筛选的标的，可清除搜索或关闭只看收藏。"
                : "还没有关注证券，点击右上角添加股票或ETF。",
          }}
          columns={[
            {
              title: "收藏",
              key: "favorite",
              width: 62,
              fixed: "left",
              render: (_, row) => (
                <Button
                  type="text"
                  size="small"
                  className={row.favorite ? styles.favorite : undefined}
                  aria-label={`${row.favorite ? "取消收藏" : "收藏"} ${row.instrument.symbol}`}
                  aria-pressed={row.favorite}
                  icon={row.favorite ? <StarFilled /> : <StarOutlined />}
                  disabled={busy}
                  onClick={(event) => {
                    event.stopPropagation();
                    favorite(row);
                  }}
                />
              ),
            },
            {
              title: "证券 / 分组",
              key: "instrument",
              width: 195,
              fixed: "left",
              render: (_, row) => (
                <div>
                  <strong className={shared.mono}>{row.instrument.symbol}</strong>{" "}
                  <Tag>
                    {row.instrument.asset_type === "etf"
                      ? "ETF"
                      : row.instrument.asset_type === "stock"
                        ? "股票"
                        : row.instrument.asset_type}
                  </Tag>
                  <div className={styles.company}>{row.instrument.name}</div>
                  <small className={shared.muted}>
                    {row.memberships.map((group) => group.groupName).join(" / ")}
                  </small>
                </div>
              ),
            },
            {
              title: "最近完整日收盘",
              key: "price",
              width: 125,
              render: (_, row) => {
                const item = metrics.get(row.instrument.id);
                return (
                  <div>
                    <strong className={styles.price}>{number(item?.price)}</strong>
                    <small className={styles.cellMeta}>
                      {row.instrument.currency} · {item?.meta.as_of ?? "日期未知"}
                    </small>
                    {item && item.meta.quality !== "complete" && (
                      <Tag>{item.meta.quality === "stale" ? "过期数据" : "覆盖不完整"}</Tag>
                    )}
                  </div>
                );
              },
            },
            {
              title: "日涨跌幅",
              key: "change",
              width: 105,
              render: (_, row) => {
                const item = metrics.get(row.instrument.id);
                return (
                  <div>
                    <strong className={color(item?.change_percent)}>
                      {trackingPercent(item?.change_percent)}
                    </strong>
                    <small className={styles.cellMeta}>{number(item?.change)}</small>
                  </div>
                );
              },
            },
            {
              title: "技术观察",
              key: "observation",
              width: 145,
              render: (_, row) => {
                const item = metrics.get(row.instrument.id);
                return item ? (
                  <>
                    <Tag>{observationLabels[item.observation] ?? item.observation}</Tag>
                    <small className={styles.cellMeta}>
                      {trendLabels[item.trend] ?? item.trend}
                    </small>
                  </>
                ) : (
                  <span className={shared.muted}>
                    {tracking.isFetching ? "读取中…" : "尚未获取观察数据"}
                  </span>
                );
              },
            },
            {
              title: "观察区间",
              key: "range",
              width: 148,
              render: (_, row) => {
                const item = metrics.get(row.instrument.id);
                return (
                  <>
                    <span className={shared.mono}>
                      {number(item?.range_low)} – {number(item?.range_high)}
                    </span>
                    <small className={styles.cellMeta}>
                      MA20 {number(item?.ma20)} / MA50 {number(item?.ma50)}
                    </small>
                  </>
                );
              },
            },
            {
              title: "机械距离参考",
              key: "levels",
              width: 142,
              render: (_, row) => {
                const item = metrics.get(row.instrument.id);
                return (
                  <>
                    <span>
                      目标 {number(item?.eligible_for_levels ? item.reference_target : null)}
                    </span>
                    <small className={styles.cellMeta}>
                      保护参考 {number(item?.eligible_for_levels ? item.protection_level : null)}
                    </small>
                  </>
                );
              },
            },
            {
              title: "技术波动",
              key: "volatility",
              width: 103,
              render: (_, row) => {
                const item = metrics.get(row.instrument.id);
                return (
                  <>
                    <span>
                      {item?.technical_volatility_level == null
                        ? "—"
                        : `${item.technical_volatility_level} / 5`}
                    </span>
                    <small className={styles.cellMeta}>
                      ATR {trackingPercent(item?.atr_percent, false)}
                    </small>
                  </>
                );
              },
            },
            {
              title: "我的备注",
              key: "notes",
              width: 200,
              render: (_, row) => (
                <button
                  type="button"
                  className={styles.noteButton}
                  onClick={(event) => {
                    event.stopPropagation();
                    setError(undefined);
                    setNoteEditor(row);
                    setNoteDraft(row.notes);
                    setNoteConflict(undefined);
                  }}
                >
                  <span>{row.notes || "添加观察备注"}</span>
                  <EditOutlined />
                </button>
              ),
            },
            {
              title: "操作",
              key: "actions",
              width: 173,
              fixed: "right",
              render: (_, row) => (
                <div className={styles.rowActions}>
                  <Button
                    size="small"
                    icon={<LineChartOutlined />}
                    onClick={() => chooseInstrument(row.instrument, true)}
                  >
                    图表
                  </Button>
                  <Button
                    size="small"
                    onClick={() => setRulesItem(metrics.get(row.instrument.id) ?? null)}
                  >
                    依据
                  </Button>
                  <Button
                    size="small"
                    disabled={!isCompanyInstrument(row.instrument)}
                    onClick={() => {
                      void navigate(
                        `/research/new?ticker=${encodeURIComponent(row.instrument.symbol)}`,
                      );
                    }}
                  >
                    研究
                  </Button>
                  {selected && view.sort === "manual" && (
                    <>
                      <Tooltip title="在原分组中上移">
                        <Button
                          size="small"
                          icon={<ArrowUpOutlined />}
                          aria-label={`${row.instrument.symbol} 上移`}
                          disabled={busy || row.memberships[0]?.position === 0}
                          onClick={() => moveItem(row, -1)}
                        />
                      </Tooltip>
                      <Tooltip title="在原分组中下移">
                        <Button
                          size="small"
                          icon={<ArrowDownOutlined />}
                          aria-label={`${row.instrument.symbol} 下移`}
                          disabled={
                            busy || row.memberships[0]?.position === selected.items.length - 1
                          }
                          onClick={() => moveItem(row, 1)}
                        />
                      </Tooltip>
                    </>
                  )}
                  <Button
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    aria-label={`移除 ${row.instrument.symbol}`}
                    disabled={busy}
                    onClick={() => setRemoveEditor(row)}
                  />
                </div>
              ),
            },
          ]}
        />
        <div className={styles.dataFooter}>
          <span>价格口径：最近已结束的美股交易日日线收盘，非实时逐笔报价。</span>
          {tracking.data?.refresh_throttled && <Tag>刷新间隔内复用缓存 · 来源时间见各行依据</Tag>}
          <span>
            {tracking.data
              ? `${tracking.data.returned_count}个数据项 · 最近来源接收 ${time(latestSourceAt)} · 聚合读取 ${time(tracking.data.received_at)}`
              : tracking.isFetching
                ? "正在读取真实日线与技术观察…"
                : "未获取行情数据"}
          </span>
        </div>
        {tracking.data && <p className={styles.scopeNotice}>{tracking.data.scope_notice}</p>}
      </section>
      <div ref={chartAnchor} className={styles.chartAnchor}>
        {view.chart &&
          (equitySelection.state === "ready" ? (
            <EquityPanel
              key={equitySelection.symbol}
              bootstrap={bootstrap}
              instrument={selectedInstrument}
              symbol={equitySelection.symbol}
              showSymbolSearch={false}
            />
          ) : (
            <section className={shared.panel}>
              <div className={shared.body}>
                {equitySelection.state === "loading" ? (
                  <Spin />
                ) : (
                  <Alert
                    type="warning"
                    showIcon
                    title="尚未确认当前证券"
                    description="请从跟踪列表选择股票或ETF；图表不会自动换成其他证券。"
                  />
                )}
              </div>
            </section>
          ))}
      </div>
      <Modal
        title={groupEditor === "new" ? "新建自选分组" : "重命名自选分组"}
        open={groupEditor !== null}
        confirmLoading={busy}
        onCancel={() => {
          if (!busy) setGroupEditor(null);
        }}
        onOk={saveGroup}
        destroyOnHidden
      >
        {error && <Alert type="error" title={error} />}
        <label htmlFor="watchlist-name">分组名称</label>
        <Input
          id="watchlist-name"
          value={groupName}
          disabled={busy}
          maxLength={120}
          onChange={(event) => setGroupName(event.target.value)}
          onPressEnter={saveGroup}
          autoComplete="off"
          style={{ marginTop: 10 }}
        />
      </Modal>
      <Modal
        title="添加股票或ETF"
        open={adding}
        width={760}
        footer={
          <Button onClick={() => setAdding(false)} disabled={busy}>
            完成
          </Button>
        }
        onCancel={() => {
          if (!busy) setAdding(false);
        }}
        destroyOnHidden
        afterClose={() => setAddModalClosed(true)}
      >
        {error && <Alert type="error" showIcon title={error} />}
        <div className={styles.addControls}>
          <label htmlFor="watchlist-add-group">添加到分组</label>
          <Select
            id="watchlist-add-group"
            aria-label="添加证券的目标分组"
            value={addGroup?.id}
            options={allGroups.map((group) => ({ value: group.id, label: group.name }))}
            onChange={setAddGroupId}
            disabled={busy}
          />
        </div>
        <Input.Search
          aria-label="搜索股票或ETF目录"
          placeholder="输入证券代码或公司名称"
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          enterButton="搜索目录"
          loading={results.isFetching}
          onSearch={(value) => setSearch(value.trim())}
          allowClear
        />
        <p className={shared.muted}>
          从真实证券目录选择。加入当前目标组时去重；也可只查看行情而不添加。
        </p>
        {results.isError && <Alert type="warning" title="目录搜索暂时失败，请稍后重试。" />}
        {results.data && (
          <>
            <p className={shared.muted}>
              来源 {results.data.source} · {results.data.quality}
              {results.data.warning ? ` · ${results.data.warning}` : ""}
            </p>
            {!results.data.items.length && <p>没有匹配的证券，请尝试完整代码或公司名称。</p>}
            <div className={styles.searchResults}>
              {results.data.items.map((instrument) => {
                const exists = addGroup?.items.some((item) => item.instrument.id === instrument.id);
                return (
                  <div key={instrument.id} className={styles.searchResult}>
                    <div>
                      <strong>
                        {instrument.symbol} · {instrument.name}
                      </strong>
                      <small>
                        {instrument.exchange} · {instrument.asset_type} · {instrument.currency}
                      </small>
                    </div>
                    <div className={shared.actions}>
                      <Button
                        size="small"
                        disabled={busy}
                        onClick={() => {
                          chooseInstrument(instrument, true);
                          setAdding(false);
                        }}
                      >
                        只查看行情
                      </Button>
                      <Button
                        size="small"
                        type="primary"
                        disabled={busy || !addGroup || exists}
                        onClick={() => {
                          if (addGroup)
                            void act(
                              () =>
                                addWatchlistItem(addGroup.id, {
                                  instrument_id: instrument.id,
                                  revision: addGroup.revision,
                                }),
                              "已加入目标分组",
                            );
                        }}
                      >
                        {exists ? "已在目标组" : "加入分组"}
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </Modal>
      <Modal
        title={`${noteEditor?.instrument.symbol ?? ""} · 观察备注`}
        open={!!noteEditor}
        confirmLoading={busy}
        onOk={saveNote}
        okButtonProps={{ disabled: !!noteConflict || noteDraft.length > 2000 }}
        onCancel={() => {
          if (!busy) setNoteEditor(undefined);
        }}
        destroyOnHidden
      >
        <p className={shared.muted}>
          同一证券的收藏与备注跨分组共享；移除关注不删除备注或研究报告。
        </p>
        {error && <Alert type="error" title={error} />}
        {noteConflict && (
          <Alert
            type="warning"
            title="备注已有新版本；你的草稿仍保留"
            description={
              noteConflict.latest ? (
                <div>
                  <p>服务器当前备注：{noteConflict.latest.notes || "（空）"}</p>
                  <Button
                    size="small"
                    onClick={() => {
                      if (noteEditor && noteConflict.latest) {
                        setNoteEditor({
                          ...noteEditor,
                          observationRevision: noteConflict.latest.observation_revision ?? 0,
                        });
                        setNoteConflict(undefined);
                        setError(undefined);
                      }
                    }}
                  >
                    采用最新版本号，保留我的草稿
                  </Button>
                </div>
              ) : (
                "当前已不再关注此证券，请先恢复关注；可复制草稿保留。"
              )
            }
          />
        )}
        <Input.TextArea
          aria-label="编辑证券观察备注"
          value={noteDraft}
          onChange={(event) => setNoteDraft(event.target.value)}
          maxLength={2000}
          showCount
          rows={7}
          disabled={busy}
        />
      </Modal>
      <Modal
        title={`移除关注 · ${removeEditor?.instrument.symbol ?? ""}`}
        open={!!removeEditor}
        footer={null}
        onCancel={() => {
          if (!busy) setRemoveEditor(undefined);
        }}
        destroyOnHidden
      >
        <p>选择要移除的分组关系；其他分组、收藏、备注、历史研究和行情数据保留。</p>
        {error && <Alert type="error" title={error} />}
        {removeEditor?.memberships.map((relation) => (
          <div className={styles.removeRow} key={relation.groupId}>
            <span>{relation.groupName}</span>
            <Button danger loading={busy} onClick={() => remove(removeEditor, relation)}>
              从此分组移除
            </Button>
          </div>
        ))}
      </Modal>
      <Modal
        title={rulesItem ? `${rulesItem.symbol} · 技术观察依据` : "跟踪规则与数据口径"}
        open={rulesItem !== undefined}
        width={850}
        centered
        classNames={{
          container: styles.rulesDialog,
          header: styles.rulesDialogHeader,
          body: styles.rulesDialogBody,
        }}
        footer={null}
        onCancel={() => setRulesItem(undefined)}
        destroyOnHidden
      >
        {rulesItem && (
          <>
            <div className={styles.ruleSummary}>
              <Tag>{observationLabels[rulesItem.observation]}</Tag>
              <Tag>{trendLabels[rulesItem.trend]}</Tag>
              <Tag>{rulesItem.meta.quality}</Tag>
            </div>
            <p>
              数据日 {rulesItem.meta.as_of ?? "未知"} · 对比日{" "}
              {rulesItem.meta.previous_as_of ?? "未知"} · {rulesItem.meta.source ?? "未知来源"} /{" "}
              {rulesItem.meta.feed ?? "未知feed"}
            </p>
            <p>
              来源接收 {time(rulesItem.meta.source_received_at)} · 本次聚合{" "}
              {time(rulesItem.meta.received_at)}
            </p>
            <div className={styles.qualityBlock}>
              <div>
                <strong>数据质量与计算窗口</strong>
                <small>来源原始质量与观察窗口复核分别标注。</small>
              </div>
              <div className={styles.qualityTags}>
                <Tag>来源 {rulesItem.meta.source_quality ?? "未知"}</Tag>
                <Tag>窗口复核 {rulesItem.meta.quality}</Tag>
                <Tag>缓存 {rulesItem.meta.cache ?? "未知"}</Tag>
              </div>
              <dl className={styles.coverage}>
                <div>
                  <dt>要求 / 有效交易日</dt>
                  <dd>
                    {rulesItem.meta.coverage.required_sessions ??
                      currentRules?.required_sessions ??
                      "—"}{" "}
                    / {rulesItem.meta.coverage.available_sessions}
                  </dd>
                </div>
                <div>
                  <dt>缺失 / 无效价格</dt>
                  <dd>
                    {rulesItem.meta.coverage.missing_sessions} /{" "}
                    {rulesItem.meta.coverage.invalid_price_sessions}
                  </dd>
                </div>
                <div>
                  <dt>重复日 / 剔除未收盘</dt>
                  <dd>
                    {rulesItem.meta.coverage.duplicate_sessions} /{" "}
                    {rulesItem.meta.coverage.excluded_unclosed}
                  </dd>
                </div>
                <div>
                  <dt>计算窗口</dt>
                  <dd>
                    {rulesItem.meta.coverage.window_start ?? "—"} →{" "}
                    {rulesItem.meta.coverage.window_end ?? "—"}
                  </dd>
                </div>
                <div>
                  <dt>应有最新完整交易日</dt>
                  <dd>{rulesItem.meta.coverage.expected_latest_session ?? "未知"}</dd>
                </div>
              </dl>
              {rulesItem.meta.source_warning && (
                <Alert type="warning" title={rulesItem.meta.source_warning} />
              )}
            </div>
            {rulesItem.rule_reasons.map((reason) => (
              <p key={reason}>{reason}</p>
            ))}
            {(rulesItem.meta.warnings ?? []).map((warning) => (
              <Alert key={warning} type="warning" title={warning} />
            ))}
          </>
        )}
        {currentRules ? (
          <>
            <p className={shared.muted}>
              {currentRules.version} · 公开固定规则 · 至少{currentRules.required_sessions}
              个完整交易日；本轮不提供规则参数修改。
            </p>
            {currentRules.formulas.map((rule) => (
              <div className={styles.rule} key={rule.id}>
                <strong>{rule.label}</strong>
                <p>{rule.description}</p>
              </div>
            ))}
            <div className={styles.rule}>
              <strong>指标参考资料</strong>
              <p>
                以下资料用于解释指标；接近区间阈值、ATR距离参数和波动分级属于本产品固定规则，不代表资料来源发布的评级。
              </p>
              <div className={shared.actions}>
                {currentRules.references.map((reference, index) => {
                  const url = publicLink(reference);
                  return url ? (
                    <a href={url} key={url} target="_blank" rel="noreferrer">
                      参考资料 {index + 1}
                    </a>
                  ) : null;
                })}
              </div>
            </div>
            <Alert type="info" title={currentRules.scope_notice} />
          </>
        ) : rules.isFetching ? (
          <Spin />
        ) : (
          <Alert type="warning" title="规则说明暂时无法读取，请稍后重试。" />
        )}
      </Modal>
    </div>
  );
}
