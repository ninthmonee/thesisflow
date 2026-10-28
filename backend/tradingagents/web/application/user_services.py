"""Private per-account service instances and bounded process-local lifecycle."""

from collections import OrderedDict
from concurrent.futures import Future
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from threading import RLock
from types import SimpleNamespace

from tradingagents.web.domain.tasks import ResearchError


def close_services(bundle):
    for name in ("tracking_service", "themes_service", "intelligence_service", "professional_pulse_service"):
        getattr(bundle, name).close()


def has_background_work(bundle):
    def pending(value):
        if isinstance(value, Future):
            return not value.done()
        if isinstance(value, (list, tuple)):
            return any(pending(item) for item in value)
        if isinstance(value, dict):
            return any(pending(key) or pending(item) for key, item in value.items())
        return False

    for name in ("tracking_service", "themes_service", "intelligence_service", "professional_pulse_service"):
        service = getattr(bundle, name)
        lock = getattr(service, "_lock", None)
        if lock is not None:
            with lock:
                values = dict(getattr(service, "_pending", {}))
        else:
            values = dict(getattr(service, "_pending", {}))
        if pending(values):
            return True
    return False


class UserServiceRegistry:
    def __init__(self, local_workspace, local_services, factory, *, capacity=32):
        if capacity < 2:
            raise ValueError("user service capacity must be at least two")
        self.factory, self.capacity = factory, capacity
        self.lock = RLock()
        self.local_key = (local_workspace.workspace_id, local_workspace.user_id)
        self.items = OrderedDict({self.local_key: local_services})
        self.users = {self.local_key: 0}

    def get(self, workspace):
        key = (workspace.workspace_id, workspace.user_id)
        with self.lock:
            if key not in self.items:
                while len(self.items) >= self.capacity:
                    evict = next((candidate for candidate in self.items
                        if candidate != self.local_key and not self.users[candidate]
                        and not has_background_work(self.items[candidate])), None)
                    if evict is None:
                        raise ResearchError("user_services_busy", "服务正忙，请稍后重试。", 503)
                    close_services(self.items.pop(evict))
                    self.users.pop(evict)
                self.items[key], self.users[key] = self.factory(workspace), 0
            self.items.move_to_end(key)
            return self.items[key]

    @contextmanager
    def lease(self, workspace):
        key = (workspace.workspace_id, workspace.user_id)
        with self.lock:
            bundle = self.get(workspace)
            self.users[key] += 1
        try:
            yield bundle
        finally:
            with self.lock:
                self.users[key] -= 1

    def close(self):
        with self.lock:
            for bundle in self.items.values():
                close_services(bundle)
            self.items.clear()
            self.users.clear()


def _build_user_services(database, workspace, root, base_config, *, resources, secret_store=None,
    model_tester=None, instrument_search=None, market_provider=None, market_clock=None,
    alternative_providers=None, research_data_tester=None, runtime_probe=None):
    """No shared credentials, mutable market settings, or cached research views."""
    from tradingagents.web.application.data_settings import DataSettingsService
    from tradingagents.web.application.market_alternatives import AlternativeMarketService
    from tradingagents.web.application.market_briefs import MarketBriefGenerator
    from tradingagents.web.application.market_data import MarketDataService
    from tradingagents.web.application.market_intelligence import (
        MarketIntelligenceService,
        ProfessionalMarketPulseService,
    )
    from tradingagents.web.application.models import ModelConfigurationService
    from tradingagents.web.application.research import SAFE_RUNTIME_KEYS, ResearchService
    from tradingagents.web.application.research_context import ResearchContextService
    from tradingagents.web.application.research_data_connections import (
        ResearchDataConnectionService,
    )
    from tradingagents.web.application.runtime import create_selection_resolver
    from tradingagents.web.application.runtime_connections import RuntimeConnectionService
    from tradingagents.web.application.runtime_readiness import RuntimeReadinessService
    from tradingagents.web.application.themes import ThemesService
    from tradingagents.web.application.tracking import WatchlistTrackingService
    from tradingagents.web.application.viewpoints import ViewpointService
    from tradingagents.web.application.watchlists import WatchlistService

    private_root = root / "users" / sha256(f"{workspace.workspace_id}:{workspace.user_id}".encode()).hexdigest()[:32]
    config = {key: deepcopy(value) for key, value in base_config.items() if key in SAFE_RUNTIME_KEYS}
    models = ModelConfigurationService(database, workspace, secret_store=secret_store, tester=model_tester)
    runtimes = RuntimeConnectionService(database, workspace, secret_store=secret_store, probe=runtime_probe)
    watchlists = WatchlistService(database, workspace_id=workspace.workspace_id,
        owner_id=workspace.user_id, search_provider=instrument_search)
    sources = ResearchDataConnectionService(database, workspace, secret_store=secret_store, tester=research_data_tester)
    research = ResearchService(database, root / "research", config_snapshot_provider=lambda: deepcopy(config),
        data_snapshot_provider=sources.freeze_for_run, max_active_runs_per_owner=3, max_queued_runs_total=50)
    market = MarketDataService(private_root / "market-cache", provider=market_provider, clock=market_clock)
    intelligence = MarketIntelligenceService(market, private_root / "market-snapshots", clock=market_clock)
    resources.append(intelligence)
    themes = ThemesService(database, workspace, market, clock=market_clock)
    resources.append(themes)
    themes.ensure_defaults()
    tracking = WatchlistTrackingService(database, workspace, market, clock=market_clock)
    resources.append(tracking)
    alternatives = AlternativeMarketService(database, workspace, private_root / "alternative-cache",
        secret_store=secret_store, providers=alternative_providers, clock=market_clock)
    pulse = ProfessionalMarketPulseService(alternatives, private_root / "professional-pulse", clock=market_clock)
    resources.append(pulse)
    context = ResearchContextService(database, workspace, private_root / "research-context",
        market=intelligence, themes=themes, alternative=alternatives, clock=market_clock)

    def apply(settings):
        market.minute_cache_ttl_seconds = settings.minute_cache_ttl_seconds
        market.daily_cache_ttl_seconds = settings.daily_cache_ttl_seconds
        return intelligence.configure(max_workers=settings.aggregation_max_workers,
            fetch_timeout_seconds=settings.aggregation_timeout_seconds, cache_seconds=settings.aggregation_refresh_seconds)

    def invalidate():
        themes.invalidate()
        tracking.invalidate()
        return intelligence.invalidate_market_data_cache()

    data = DataSettingsService(database, workspace, private_root / "market-cache",
        snapshot_cache_root=private_root / "market-snapshots", apply_callback=apply,
        cache_invalidator=invalidate, capabilities_reader=market.capabilities, environment_reader=lambda _: None)
    data.apply_saved_settings()
    return SimpleNamespace(model_service=models, runtime_service=runtimes, watchlist_service=watchlists, research_service=research,
        research_data_service=sources, market_service=market, intelligence_service=intelligence,
        themes_service=themes, tracking_service=tracking, alternative_service=alternatives,
        professional_pulse_service=pulse, research_context_service=context, data_service=data,
        readiness_service=RuntimeReadinessService(database, workspace, clock=market_clock),
        viewpoint_service=ViewpointService(database, workspace, research, clock=market_clock),
        resolve_models=create_selection_resolver(models),
        market_brief_generator=MarketBriefGenerator(models, base_config=config))


def build_user_services(database, workspace, root, base_config, **options):
    resources = []
    try:
        return _build_user_services(database, workspace, root, base_config, resources=resources, **options)
    except BaseException:
        for service in reversed(resources):
            service.close()
        raise
