import { useRef, useState } from "react";
import {
  Alert,
  Button,
  Collapse,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Table,
  Tag,
  Tooltip,
} from "antd";
import { PlusOutlined, PushpinFilled, PushpinOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  archiveTheme,
  cloneTheme,
  createTheme,
  getThemeAnalytics,
  listThemes,
  listThemeVersions,
  restoreTheme,
  searchInstruments,
  setThemePinned,
  updateTheme,
} from "../../shared/api/generated/client";
import type {
  BootstrapResponse,
  ThemeList,
  ThemeMemberInput,
  ThemeMemberMetric,
  ThemeMemberView,
  ThemeView,
  ThemeWrite,
} from "../../shared/api/generated/models";
import { queryKeys, scopeForWorkspace } from "../../shared/api/query-keys";
import { ApiError, shouldRetryQuery } from "../../shared/api/transport";
import { AttachResearchContextButton } from "../research/research-context";
import {
  addDraftMember,
  categoryNames,
  evidenceNames,
  exposureNames,
  matchesTheme,
  publicLink,
  taxonomyKey,
  taxonomyNames,
  themeDraft,
  themeVersionFromQuery,
  validateThemeMembers,
  validateThemeTaxonomy,
} from "./theme-utils";
import shared from "../../shared/ui/workbench.module.css";
import styles from "./themes.module.css";

function fail(error: unknown) {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : "操作未完成，请稍后重试";
}
function percent(value?: number | null) {
  return value == null ? "—" : (value > 0 ? "+" : "") + value.toFixed(2) + "%";
}
function quantity(value?: number | null) {
  return value == null
    ? "—"
    : new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(
        value,
      );
}
function priceClass(value?: number | null) {
  return value == null || value === 0 ? undefined : value > 0 ? styles.positive : styles.negative;
}
function Evidence({
  member,
}: {
  member: ThemeMemberInput & { evidence_quality: ThemeMemberMetric["evidence_quality"] };
}) {
  return (
    <div className={styles.sources}>
      <Tag color={member.evidence_quality === "documented" ? "blue" : "default"}>
        {evidenceNames[member.evidence_quality]}
      </Tag>
      {(member.evidence_urls ?? []).map((url, index) => {
        const safe = publicLink(url);
        return safe ? (
          <a key={safe} href={safe} target="_blank" rel="noreferrer">
            来源 {index + 1}
          </a>
        ) : null;
      })}
      <span>{member.verified_at ? "维护者核查 " + member.verified_at : "未记录核查日期"}</span>
    </div>
  );
}

export function ThemesPage({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const scope = scopeForWorkspace(bootstrap.workspace),
    cache = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(""),
    [category, setCategory] = useState("all");
  const [taxonomy, setTaxonomy] = useState<string>();
  const [includeArchived, setIncludeArchived] = useState(false);
  const [error, setError] = useState<string>(),
    [busy, setBusy] = useState(false);
  const [pinningId, setPinningId] = useState<string>();
  const [researchTicker, setResearchTicker] = useState("");
  const preferenceInFlight = useRef(false);
  const [editor, setEditor] = useState<{ kind: "create" | "edit"; original?: ThemeView }>();
  const [members, setMembers] = useState<ThemeMemberInput[]>([]),
    [manualSymbol, setManualSymbol] = useState("");
  const [instrumentQuery, setInstrumentQuery] = useState(""),
    [draftError, setDraftError] = useState<string>();
  const [form] = Form.useForm<ThemeWrite>();
  const [modal, modalContext] = Modal.useModal();
  const [dirty, setDirty] = useState(false),
    [membersPage, setMembersPage] = useState(1);
  const savingInFlight = useRef(false);
  const forcedRefresh = useRef<string | undefined>(undefined);
  const [conflict, setConflict] = useState(false),
    [latestVersion, setLatestVersion] = useState<ThemeView>();
  const themes = useQuery({
    queryKey: queryKeys.resource(scope, "themes", { includeArchived }),
    queryFn: ({ signal }) => listThemes({ include_archived: includeArchived }, { signal }),
    retry: shouldRetryQuery,
  });
  const items = themes.data?.items ?? [],
    requestedId = params.get("theme");
  const selected = requestedId
    ? items.find((item) => item.id === requestedId)
    : (items.find((item) => !item.archived) ?? items[0]);
  const requestedWindow = params.get("window");
  const window = requestedWindow === "5D" || requestedWindow === "1M" ? requestedWindow : "1D";
  const requestedVersion = params.get("theme_version");
  const version = themeVersionFromQuery(requestedVersion);
  const documentedOnly = params.get("documented_only") === "true";
  const versions = useQuery({
    queryKey: queryKeys.resource(scope, "theme-versions", {
      id: selected?.id,
      revision: selected?.revision,
    }),
    queryFn: ({ signal }) => listThemeVersions(selected?.id ?? "", { signal }),
    enabled: !!selected,
    retry: shouldRetryQuery,
  });
  const displayed =
    requestedVersion !== null
      ? versions.data?.items.find((item) => item.version === version)
      : selected;
  const historical = !!selected && !!displayed && selected.version !== displayed.version;
  const analyticsIdentity = JSON.stringify([
    selected?.id,
    displayed?.version,
    window,
    documentedOnly,
  ]);
  const analytics = useQuery({
    queryKey: queryKeys.resource(scope, "theme-analytics", {
      id: selected?.id,
      version: displayed?.version,
      window,
      documentedOnly,
    }),
    queryFn: async ({ signal }) => {
      const identity = analyticsIdentity;
      const force = forcedRefresh.current === identity;
      const result = await getThemeAnalytics(
        selected?.id ?? "",
        {
          window,
          version: displayed?.version,
          documented_only: documentedOnly,
          ...(force ? { refresh: true } : {}),
        },
        { signal },
      );
      if (force && forcedRefresh.current === identity) forcedRefresh.current = undefined;
      return result;
    },
    enabled: !!selected && !!displayed && !displayed.archived,
    retry: shouldRetryQuery,
    staleTime: 30_000,
  });
  const instruments = useQuery({
    queryKey: queryKeys.resource(scope, "theme-instrument-search", { query: instrumentQuery }),
    queryFn: ({ signal }) => searchInstruments({ q: instrumentQuery }, { signal }),
    enabled: !!editor && !!instrumentQuery,
    retry: shouldRetryQuery,
  });
  function choose(id: string) {
    const next = new URLSearchParams(params);
    next.set("theme", id);
    next.delete("theme_version");
    setParams(next);
  }
  function openEditor(original?: ThemeView) {
    form.resetFields();
    form.setFieldsValue(themeDraft(original));
    setMembers(themeDraft(original).members ?? []);
    setDraftError(undefined);
    setManualSymbol("");
    setInstrumentQuery("");
    setEditor({ kind: original ? "edit" : "create", original });
    setConflict(false);
    setLatestVersion(undefined);
    setDirty(false);
    setMembersPage(1);
  }
  function closeEditor() {
    if (busy || savingInFlight.current) return;
    if (!dirty) {
      setEditor(undefined);
      return;
    }
    modal.confirm({
      title: "放弃未保存的主题修改？",
      content: "主题名称和个股调整尚未保存。",
      okText: "放弃修改",
      cancelText: "继续编辑",
      onOk: () => setEditor(undefined),
    });
  }
  function addMember(symbol: string, name = "") {
    try {
      const next = addDraftMember(members, symbol, name);
      setMembers(next);
      setMembersPage(Math.ceil(next.length / 6));
      setDirty(true);
      setManualSymbol("");
      setDraftError(undefined);
    } catch (failure) {
      setDraftError(fail(failure));
    }
  }
  function editMember(symbol: string, update: Partial<ThemeMemberInput>) {
    setDirty(true);
    setMembers((current) =>
      current.map((member) => (member.symbol === symbol ? { ...member, ...update } : member)),
    );
  }
  function removeDraftMember(symbol: string) {
    const next = members.filter((member) => member.symbol !== symbol);
    setMembers(next);
    setMembersPage((page) => Math.max(1, Math.min(page, Math.ceil(next.length / 6))));
    setDirty(true);
  }
  async function refresh() {
    await cache.invalidateQueries({ queryKey: queryKeys.resource(scope, "themes") });
  }
  async function save(values: ThemeWrite) {
    if (!editor || conflict || savingInFlight.current || busy) return;
    savingInFlight.current = true;
    setBusy(true);
    setDraftError(undefined);
    try {
      if (manualSymbol.trim()) throw new Error("代码输入框仍有待添加股票，请先点击“添加个股”。");
      validateThemeMembers(members);
      validateThemeTaxonomy(values.taxonomy_tags ?? []);
      const data = { ...values, members };
      const saved = editor.original
        ? await updateTheme(editor.original.id, { ...data, revision: editor.original.revision })
        : await createTheme(data);
      setEditor(undefined);
      form.resetFields();
      setMembers([]);
      setDirty(false);
      await refresh();
      choose(saved.id);
      await cache.invalidateQueries({ queryKey: queryKeys.resource(scope, "theme-analytics") });
    } catch (failure) {
      setDraftError(fail(failure));
      if (
        failure instanceof ApiError &&
        ["theme_revision_conflict", "theme_archived"].includes(failure.code)
      ) {
        setConflict(true);
        await refresh();
        const latest = cache.getQueryData<ThemeList>(
          queryKeys.resource(scope, "themes", { includeArchived }),
        );
        setLatestVersion(latest?.items.find((item) => item.id === editor.original?.id));
      }
    } finally {
      savingInFlight.current = false;
      setBusy(false);
    }
  }
  async function copySelected() {
    if (!selected) return;
    setBusy(true);
    setError(undefined);
    try {
      const copy = await cloneTheme(selected.id, {
        revision: selected.revision,
        name: selected.name + " · 我的观察",
      });
      await refresh();
      choose(copy.id);
    } catch (failure) {
      setError(fail(failure));
    } finally {
      setBusy(false);
    }
  }
  async function archiveSelected() {
    if (!selected) return;
    setBusy(true);
    setError(undefined);
    try {
      await archiveTheme(selected.id, { revision: selected.revision });
      await refresh();
      const next = new URLSearchParams(params);
      next.delete("theme");
      next.delete("theme_version");
      setParams(next);
    } catch (failure) {
      setError(fail(failure));
    } finally {
      setBusy(false);
    }
  }
  async function restoreSelected() {
    if (!selected) return;
    setBusy(true);
    setError(undefined);
    try {
      await restoreTheme(selected.id, { revision: selected.revision });
      await refresh();
    } catch (failure) {
      setError(fail(failure));
    } finally {
      setBusy(false);
    }
  }
  async function togglePin(item: ThemeView) {
    if (preferenceInFlight.current || busy || item.archived) return;
    preferenceInFlight.current = true;
    setPinningId(item.id);
    setError(undefined);
    // Retain the open detail when the server reorders the catalog.
    if (selected && !requestedId) {
      const next = new URLSearchParams(params);
      next.set("theme", selected.id);
      setParams(next);
    }
    try {
      await setThemePinned(item.id, { revision: item.revision, pinned: !item.pinned });
      await refresh();
    } catch (failure) {
      setError(fail(failure));
      if (failure instanceof ApiError && failure.status === 409) await refresh();
    } finally {
      preferenceInFlight.current = false;
      setPinningId(undefined);
    }
  }
  const data = analytics.data;
  const visible = items.filter(
    (item) =>
      matchesTheme(item, query) &&
      (category === "all" || item.category === category) &&
      (!taxonomy || item.taxonomy_tags?.some((tag) => taxonomyKey(tag) === taxonomy)),
  );
  const taxonomyOptions = [
    ...new Map(
      items.flatMap((item) =>
        (item.taxonomy_tags ?? []).map(
          (tag) =>
            [
              taxonomyKey(tag),
              { value: taxonomyKey(tag), label: taxonomyNames[tag.kind] + " · " + tag.label },
            ] as const,
        ),
      ),
    ).values(),
  ];
  return (
    <div className={shared.stack}>
      {modalContext}
      <div className={shared.heading}>
        <small>THEMATIC RESEARCH · SUPPLY CHAINS</small>
        <h1>主题与关键产业链</h1>
        <p>
          行业之外，用可配置观察样本跟踪细分技术、关键材料与需求环节。同一家公司可以属于多个主题。
        </p>
      </div>
      {(error || themes.isError) && (
        <Alert type="error" showIcon title={error ?? fail(themes.error)} />
      )}
      <div className={styles.layout}>
        <section className={shared.panel}>
          <div className={shared.toolbar}>
            <h2>我的主题</h2>
            <Button
              disabled={busy || !!pinningId}
              icon={<PlusOutlined />}
              onClick={() => openEditor()}
            >
              新建
            </Button>
          </div>
          <div className={shared.body}>
            <Input.Search
              aria-label="搜索主题、材料或股票"
              placeholder="磷化铟 / InP / 存储 / mRNA"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              allowClear
            />
            <Select
              aria-label="主题分类"
              value={category}
              onChange={setCategory}
              style={{ width: "100%", marginTop: 10 }}
              options={[
                { value: "all", label: "全部分类" },
                ...Object.entries(categoryNames).map(([value, label]) => ({ value, label })),
              ]}
            />
            <Select
              aria-label="按多维分类标签筛选主题"
              value={taxonomy}
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="行业 / 主题 / 材料 / 产业链标签"
              onChange={setTaxonomy}
              style={{ width: "100%", marginTop: 10 }}
              options={taxonomyOptions}
            />
            <Button type="link" onClick={() => setIncludeArchived((value) => !value)}>
              {includeArchived ? "隐藏已删除" : "显示已删除"}
            </Button>
          </div>
          <div className={styles.catalog}>
            {themes.isPending ? (
              <Spin />
            ) : (
              visible.map((item) => (
                <div
                  key={item.id}
                  className={styles.theme + (item.id === selected?.id ? " " + styles.selected : "")}
                >
                  <button
                    type="button"
                    onClick={() => choose(item.id)}
                    className={styles.themeChoice}
                    aria-current={item.id === selected?.id ? "true" : undefined}
                  >
                    <strong>
                      {item.name}
                      {item.archived ? " · 已删除" : ""}
                    </strong>
                    <small>
                      {categoryNames[item.category ?? "custom"]} · {(item.members ?? []).length}{" "}
                      只股票 · v{item.version}
                    </small>
                  </button>
                  <Tooltip title={item.pinned ? "取消置顶" : "置顶主题"}>
                    <Button
                      type="text"
                      size="small"
                      className={styles.pin + (item.pinned ? " " + styles.pinned : "")}
                      icon={item.pinned ? <PushpinFilled /> : <PushpinOutlined />}
                      aria-label={(item.pinned ? "取消置顶：" : "置顶：") + item.name}
                      aria-pressed={!!item.pinned}
                      loading={pinningId === item.id}
                      disabled={busy || !!pinningId || item.archived}
                      onClick={() => {
                        void togglePin(item);
                      }}
                    />
                  </Tooltip>
                </div>
              ))
            )}
            {!themes.isPending && !visible.length && (
              <p className={shared.muted}>没有匹配主题，可创建自己的产业链。</p>
            )}
          </div>
        </section>
        <div className={shared.stack}>
          {!selected ? (
            <Alert
              type="info"
              title={
                themes.isPending
                  ? "正在读取主题…"
                  : requestedId
                    ? "主题不存在或已删除，可显示已删除主题后恢复。"
                    : "创建主题并加入观察成员后开始分析。"
              }
            />
          ) : (
            <>
              <section className={shared.panel}>
                <div className={shared.toolbar}>
                  <div>
                    <h2>{displayed?.name ?? selected.name}</h2>
                    <Tag>
                      {selected.is_builtin ? "可编辑预置" : "自定义"} · v{displayed?.version ?? "—"}
                    </Tag>
                  </div>
                  <div className={shared.actions}>
                    <Select
                      aria-label="主题配置版本"
                      value={requestedVersion ?? "latest"}
                      loading={versions.isFetching}
                      style={{ minWidth: 180 }}
                      options={[
                        { value: "latest", label: "最新配置 · v" + selected.version },
                        ...(versions.data?.items ?? []).map((item) => ({
                          value: String(item.version),
                          label:
                            "v" +
                            item.version +
                            (item.archived
                              ? " · 删除记录"
                              : " · " + new Date(item.effective_from).toLocaleDateString()),
                        })),
                      ]}
                      onChange={(value) => {
                        const next = new URLSearchParams(params);
                        next.set("theme", selected.id);
                        if (value === "latest") next.delete("theme_version");
                        else next.set("theme_version", value);
                        setParams(next);
                      }}
                    />
                    <Select
                      aria-label="主题收益窗口"
                      value={window}
                      options={[
                        { value: "1D", label: "1交易日" },
                        { value: "5D", label: "5交易日" },
                        { value: "1M", label: "1个月" },
                      ]}
                      onChange={(value) => {
                        const next = new URLSearchParams(params);
                        next.set("window", value);
                        setParams(next);
                      }}
                    />
                    <Button
                      icon={<ReloadOutlined />}
                      loading={analytics.isFetching}
                      disabled={!displayed || displayed.archived}
                      onClick={() => {
                        forcedRefresh.current = analyticsIdentity;
                        void analytics.refetch();
                      }}
                    >
                      刷新
                    </Button>
                    <Button
                      disabled={
                        busy || !!pinningId || selected.archived || historical || !displayed
                      }
                      onClick={() => openEditor(selected)}
                    >
                      编辑主题与个股
                    </Button>
                    <Button
                      disabled={busy || !!pinningId || historical || !displayed}
                      onClick={() => {
                        void copySelected();
                      }}
                    >
                      复制
                    </Button>
                    {selected.archived ? (
                      <Button
                        disabled={busy || !!pinningId}
                        onClick={() => {
                          void restoreSelected();
                        }}
                      >
                        恢复主题
                      </Button>
                    ) : (
                      <Popconfirm
                        title={"删除主题“" + selected.name + "”？"}
                        description="将从列表隐藏，可在“显示已删除”中恢复。历史版本和个股研究保留。"
                        okText="删除主题"
                        cancelText="取消"
                        onConfirm={() => {
                          void archiveSelected();
                        }}
                      >
                        <Button danger disabled={busy || !!pinningId || historical || !displayed}>
                          删除主题
                        </Button>
                      </Popconfirm>
                    )}
                  </div>
                </div>
                <div className={shared.body}>
                  <p>{displayed?.description}</p>
                  <p className={shared.muted}>
                    {displayed?.scope_notice} · 配置版本生效{" "}
                    {displayed ? new Date(displayed.effective_from).toLocaleString() : "—"}
                  </p>
                  <p className={shared.muted}>{displayed?.evidence_notice}</p>
                  {displayed && !displayed.archived && (
                    <div className={shared.actions}>
                      <Input
                        aria-label="主题背景的目标公司股票"
                        placeholder="输入要研究的公司代码"
                        value={researchTicker}
                        onChange={(event) => setResearchTicker(event.target.value.toUpperCase())}
                        style={{ maxWidth: 240 }}
                      />
                      <AttachResearchContextButton
                        ticker={researchTicker}
                        body={{
                          kind: "theme",
                          theme_id: displayed.id,
                          theme_version: displayed.version,
                        }}
                        label={`附加主题 v${displayed.version} 到研究`}
                      />
                    </div>
                  )}
                  {(displayed?.aliases ?? []).map((alias) => (
                    <Tag key={alias}>{alias}</Tag>
                  ))}
                  {(displayed?.taxonomy_tags ?? []).map((tag) => (
                    <Tag key={taxonomyKey(tag)} color="blue">
                      {taxonomyNames[tag.kind]} · {tag.label}
                    </Tag>
                  ))}
                  <div style={{ marginTop: 12 }}>
                    <Select
                      aria-label="主题关系证据范围"
                      value={documentedOnly ? "documented" : "all"}
                      style={{ minWidth: 260 }}
                      options={[
                        { value: "all", label: "全部观察成员" },
                        { value: "documented", label: "仅维护者证据齐全的成员" },
                      ]}
                      onChange={(value) => {
                        const next = new URLSearchParams(params);
                        if (value === "documented") next.set("documented_only", "true");
                        else next.delete("documented_only");
                        setParams(next);
                      }}
                    />
                  </div>
                </div>
              </section>
              {historical && (
                <Alert
                  type="info"
                  title="历史配置只读：成员固定在所选版本，行情仍为最近完整交易日窗口，不是当时行情回放或历史回测。返回最新配置后可编辑。"
                />
              )}
              {!displayed && (
                <Alert
                  type={versions.isError || !versions.isFetching ? "error" : "info"}
                  title={
                    versions.isError
                      ? fail(versions.error)
                      : versions.isFetching
                        ? "正在读取所选配置版本…"
                        : "所选配置版本不存在，请选择其他版本。"
                  }
                />
              )}
              {displayed?.archived && (
                <Alert
                  type="info"
                  title="此版本为删除记录，保留配置与成员历史。可选择此前活动版本查看统计，或恢复当前主题。"
                />
              )}
              {analytics.isError && <Alert type="error" title={fail(analytics.error)} />}
              {analytics.isPending && displayed && !displayed.archived && (
                <div className={shared.body}>
                  <Spin /> 正在读取成员行情与共同交易日…
                </div>
              )}
              {data && displayed && !displayed.archived && (
                <>
                  <div className={styles.metrics}>
                    <div className={styles.metric}>
                      <small>观察样本等权收益</small>
                      <strong className={priceClass(data.return_percent)}>
                        {percent(data.return_percent)}
                      </strong>
                    </div>
                    <div className={styles.metric}>
                      <small>相对 SPY · 百分点</small>
                      <strong className={priceClass(data.relative_return_pp)}>
                        {data.relative_return_pp == null
                          ? "—"
                          : data.relative_return_pp.toFixed(2) + " pp"}
                      </strong>
                    </div>
                    <div className={styles.metric}>
                      <small>有效覆盖</small>
                      <strong>
                        {data.available_count}/{data.requested_count}
                      </strong>
                    </div>
                    <div className={styles.metric}>
                      <small>样本日 RVOL 中位数</small>
                      <strong>
                        {data.median_daily_rvol == null
                          ? "—"
                          : data.median_daily_rvol.toFixed(2) + "×"}
                      </strong>
                    </div>
                  </div>
                  <section className={shared.panel}>
                    <div className={shared.toolbar}>
                      <h2>成员价格表现</h2>
                      <Tag color={data.quality === "complete" ? "green" : "gold"}>
                        {data.quality}
                      </Tag>
                    </div>
                    <div className={styles.heatmap}>
                      {data.members.map((member) => (
                        <div
                          key={member.symbol}
                          className={styles.tile}
                          style={{
                            background:
                              member.return_percent == null
                                ? "#17222c"
                                : member.return_percent >= 0
                                  ? "#15473b"
                                  : "#4b2530",
                          }}
                        >
                          <Link to={"/stocks?symbol=" + encodeURIComponent(member.symbol)}>
                            {member.symbol}
                          </Link>
                          <strong>{percent(member.return_percent)}</strong>
                        </div>
                      ))}
                    </div>
                    <p className={shared.note}>
                      等面积色块只表示成员价格收益，不是市值或资金流。比较{" "}
                      {data.window_start ?? "—"} → {data.window_end ?? "—"}；{data.source ?? "—"} /{" "}
                      {data.feed ?? "—"}。 固定配置 v{data.version}；原始成员{" "}
                      {data.original_member_count}，证据筛选排除 {data.excluded_count}，行情覆盖{" "}
                      {data.available_count}/{data.requested_count}。
                    </p>
                    <div className={shared.table}>
                      <Table<ThemeMemberMetric>
                        rowKey="symbol"
                        size="small"
                        pagination={{ pageSize: 15, showSizeChanger: false }}
                        dataSource={data.members}
                        scroll={{ x: 950 }}
                        columns={[
                          {
                            title: "成员 / 产业链环节",
                            key: "member",
                            render: (_, member) => (
                              <>
                                <Link to={"/stocks?symbol=" + encodeURIComponent(member.symbol)}>
                                  {member.symbol}
                                </Link>
                                <div>{member.name}</div>
                                <small className={shared.muted}>{member.role}</small>
                              </>
                            ),
                          },
                          {
                            title: "主题关系",
                            key: "exposure",
                            render: (_, member) => (
                              <>
                                <Tag>{exposureNames[member.exposure]}</Tag>
                                <Evidence member={member} />
                              </>
                            ),
                          },
                          {
                            title: "收益",
                            key: "return",
                            sorter: (left, right) =>
                              (left.return_percent ?? -Infinity) -
                              (right.return_percent ?? -Infinity),
                            render: (_, member) => (
                              <span className={priceClass(member.return_percent)}>
                                {percent(member.return_percent)}
                              </span>
                            ),
                          },
                          {
                            title: "相对SPY",
                            key: "relative",
                            render: (_, member) =>
                              member.relative_return_pp == null
                                ? "—"
                                : member.relative_return_pp.toFixed(2) + " pp",
                          },
                          {
                            title: "日RVOL",
                            key: "rvol",
                            render: (_, member) => (
                              <>
                                {member.daily_rvol == null
                                  ? "—"
                                  : member.daily_rvol.toFixed(2) + "×"}
                                <div className={shared.muted}>{member.rvol_sample_count}/20日</div>
                              </>
                            ),
                          },
                          {
                            title: "成交额估算 USD",
                            key: "turnover",
                            render: (_, member) => quantity(member.turnover_estimate_usd),
                          },
                          {
                            title: "数据状态",
                            key: "state",
                            render: (_, member) => (
                              <>
                                <Tag>{member.quality}</Tag>
                                <div className={shared.muted}>
                                  {member.reason ?? member.as_of ?? "—"}
                                </div>
                              </>
                            ),
                          },
                        ]}
                      />
                    </div>
                    <div className={shared.body}>
                      <p className={shared.muted}>{data.membership_basis}</p>
                      <p className={shared.muted}>{data.method}</p>
                      <p className={shared.muted}>{data.attention_method}</p>
                      {data.warnings.map((warning) => (
                        <p key={warning} className={shared.muted}>
                          {warning}
                        </p>
                      ))}
                    </div>
                  </section>
                </>
              )}
              <Collapse
                items={[
                  {
                    key: "relationships",
                    label: "关系证据目录 · 独立于行情数据状态",
                    children: displayed ? (
                      <>
                        <p className={shared.muted}>{displayed.evidence_notice}</p>
                        <Table<ThemeMemberView>
                          rowKey="symbol"
                          size="small"
                          dataSource={displayed.members}
                          pagination={{ defaultPageSize: 10, showSizeChanger: true }}
                          columns={[
                            { title: "代码", dataIndex: "symbol" },
                            { title: "名称", dataIndex: "name" },
                            { title: "产业链环节", dataIndex: "role" },
                            {
                              title: "维护者声明关系",
                              key: "relationship",
                              render: (_, member) =>
                                exposureNames[member.exposure ?? "user_unverified"],
                            },
                            {
                              title: "关系证据完整度",
                              key: "evidence",
                              render: (_, member) => <Evidence member={member} />,
                            },
                          ]}
                          scroll={{ x: 750 }}
                        />
                      </>
                    ) : (
                      "选择有效配置版本后查看关系目录。"
                    ),
                  },
                  {
                    key: "versions",
                    label: "主题版本与样本范围",
                    children: (
                      <div>
                        {versions.isError
                          ? fail(versions.error)
                          : (versions.data?.items ?? []).map((version) => (
                              <p key={version.version}>
                                v{version.version} ·{" "}
                                {new Date(version.effective_from).toLocaleString()} ·{" "}
                                {(version.members ?? [])
                                  .map((member) => member.symbol)
                                  .join(" / ") || "空观察样本"}
                              </p>
                            ))}
                      </div>
                    ),
                  },
                ]}
              />
            </>
          )}
        </div>
      </div>
      <Modal
        title={editor?.kind === "edit" ? "编辑主题与观察成员" : "创建研究主题"}
        open={!!editor}
        width={1100}
        onCancel={closeEditor}
        maskClosable={!busy}
        keyboard={!busy}
        closable={!busy}
        footer={null}
        destroyOnHidden
      >
        <div className={styles.editor}>
          {draftError && <Alert type="error" title={draftError} />}
          {conflict && (
            <Alert
              type="warning"
              title="服务器已有新版本，当前草稿尚未覆盖它。"
              description="可复制保存当前草稿内容，再载入最新版本。若主题已删除，请关闭窗口查看已删除列表。"
              action={
                latestVersion && (
                  <Button onClick={() => openEditor(latestVersion)}>
                    载入最新版本（替换草稿）
                  </Button>
                )
              }
            />
          )}
          <Form
            name="theme-editor"
            form={form}
            layout="vertical"
            disabled={busy}
            onValuesChange={() => setDirty(true)}
            onFinish={(values) => {
              void save(values);
            }}
          >
            <div className={shared.columns}>
              <Form.Item name="name" label="主题名称" rules={[{ required: true, max: 120 }]}>
                <Input />
              </Form.Item>
              <Form.Item name="category" label="主题分类">
                <Select
                  options={Object.entries(categoryNames).map(([value, label]) => ({
                    value,
                    label,
                  }))}
                />
              </Form.Item>
            </div>
            <Form.Item name="description" label="研究范围与产业链边界">
              <Input.TextArea rows={2} maxLength={2000} />
            </Form.Item>
            <Form.Item name="aliases" label="别名与关键词">
              <Select mode="tags" tokenSeparators={[",", "，"]} />
            </Form.Item>
            <Form.List name="taxonomy_tags">
              {(fields, { add, remove }) => (
                <div>
                  <p>多维分类标签：行业、研究主题、材料和产业链环节可同时存在。</p>
                  {fields.map((field) => (
                    <div className={styles.editorActions} key={field.key}>
                      <Form.Item name={[field.name, "kind"]} rules={[{ required: true }]}>
                        <Select
                          aria-label={"标签" + (field.name + 1) + "维度"}
                          style={{ width: 160 }}
                          options={Object.entries(taxonomyNames).map(([value, label]) => ({
                            value,
                            label,
                          }))}
                        />
                      </Form.Item>
                      <Form.Item
                        name={[field.name, "label"]}
                        rules={[{ required: true, whitespace: true, max: 80 }]}
                      >
                        <Input
                          aria-label={"标签" + (field.name + 1) + "名称"}
                          maxLength={80}
                          placeholder="如 半导体 / AI算力 / InP / 衬底"
                        />
                      </Form.Item>
                      <Button
                        onClick={() => {
                          remove(field.name);
                          setDirty(true);
                        }}
                      >
                        移除标签
                      </Button>
                    </div>
                  ))}
                  <Button
                    disabled={fields.length >= 24}
                    onClick={() => {
                      add({ kind: "theme", label: "" });
                      setDirty(true);
                    }}
                  >
                    添加分类标签（{fields.length}/24）
                  </Button>
                </div>
              )}
            </Form.List>
            <div className={styles.editorActions}>
              <Input
                aria-label="手工添加主题成员代码"
                value={manualSymbol}
                onChange={(event) => {
                  setManualSymbol(event.target.value);
                  setDirty(true);
                }}
                onPressEnter={(event) => {
                  event.preventDefault();
                  addMember(manualSymbol);
                }}
                placeholder="证券代码，如 NVDA / AXTI"
                style={{ width: 230 }}
              />
              <Button onClick={() => addMember(manualSymbol)}>添加个股</Button>
              <Input.Search
                aria-label="搜索主题成员证券"
                placeholder="按代码/公司名搜索"
                onSearch={(value, event) => {
                  event?.preventDefault();
                  setInstrumentQuery(value.trim());
                }}
                style={{ width: 280 }}
              />
            </div>
            {!!instrumentQuery && (
              <div className={styles.editorActions}>
                {instruments.isFetching ? (
                  <Spin />
                ) : instruments.isError ? (
                  <span>{fail(instruments.error)}，也可手工添加代码。</span>
                ) : (
                  instruments.data?.items.map((instrument) => (
                    <Button
                      key={instrument.id}
                      size="small"
                      disabled={members.some((member) => member.symbol === instrument.symbol)}
                      onClick={() => addMember(instrument.symbol, instrument.name)}
                    >
                      {instrument.symbol} · {instrument.name}
                    </Button>
                  ))
                )}
              </div>
            )}
            <Table<ThemeMemberInput>
              rowKey="symbol"
              size="small"
              dataSource={members}
              pagination={{
                current: membersPage,
                onChange: setMembersPage,
                pageSize: 6,
                showSizeChanger: false,
              }}
              scroll={{ x: 900 }}
              columns={[
                { title: "证券", dataIndex: "symbol", width: 90 },
                {
                  title: "显示名称",
                  key: "name",
                  render: (_, member) => (
                    <Input
                      aria-label={member.symbol + "名称"}
                      value={member.name}
                      onChange={(event) => editMember(member.symbol, { name: event.target.value })}
                    />
                  ),
                },
                {
                  title: "产业链环节",
                  key: "role",
                  render: (_, member) => (
                    <Input
                      aria-label={member.symbol + "产业链环节"}
                      value={member.role}
                      onChange={(event) => editMember(member.symbol, { role: event.target.value })}
                    />
                  ),
                },
                {
                  title: "业务关系",
                  key: "exposure",
                  render: (_, member) => (
                    <Select
                      aria-label={member.symbol + "业务关系"}
                      value={member.exposure}
                      style={{ width: 170 }}
                      options={Object.entries(exposureNames).map(([value, label]) => ({
                        value,
                        label,
                      }))}
                      onChange={(value) => editMember(member.symbol, { exposure: value })}
                    />
                  ),
                },
                {
                  title: "来源证据",
                  key: "source",
                  render: (_, member) => (
                    <Input.TextArea
                      aria-label={member.symbol + "证据链接"}
                      value={(member.evidence_urls ?? []).join("\n")}
                      rows={2}
                      onChange={(event) =>
                        editMember(member.symbol, {
                          evidence_urls: event.target.value
                            .split("\n")
                            .map((value) => value.trim())
                            .filter(Boolean),
                        })
                      }
                    />
                  ),
                },
                {
                  title: "维护者核查日期",
                  key: "verified_at",
                  width: 170,
                  render: (_, member) => (
                    <Input
                      type="date"
                      aria-label={member.symbol + "维护者核查日期"}
                      value={member.verified_at ?? ""}
                      onChange={(event) =>
                        editMember(member.symbol, { verified_at: event.target.value || null })
                      }
                    />
                  ),
                },
                {
                  title: "",
                  key: "remove",
                  render: (_, member) => (
                    <Button
                      danger
                      size="small"
                      aria-label={"删除个股：" + member.symbol}
                      onClick={() => removeDraftMember(member.symbol)}
                    >
                      删除个股
                    </Button>
                  ),
                },
              ]}
            />
            <p className={shared.muted}>
              当前关注 {members.length} 只股票。{" "}
              保存创建新版本，保留历史。关系类型、维护者核查日期和公开链接均完整时显示“维护者证据齐全”；这是维护记录完整度，不表示平台验证，不把多元业务公司称为纯主题公司。
            </p>
            <Button type="primary" htmlType="submit" loading={busy} disabled={conflict}>
              保存主题版本
            </Button>
          </Form>
        </div>
      </Modal>
    </div>
  );
}
