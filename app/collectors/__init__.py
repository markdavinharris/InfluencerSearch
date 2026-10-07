"""Collector registry. Modules are imported lazily so one platform's missing optional
dependency or broken import never blocks the others."""

import importlib

from app.collectors.base import Collector

REGISTRY: dict[str, str] = {
    "x": "app.collectors.x:XCollector",
    "bluesky": "app.collectors.bluesky:BlueskyCollector",
    "reddit": "app.collectors.reddit:RedditCollector",
    "youtube": "app.collectors.youtube:YouTubeCollector",
    "news": "app.collectors.news:NewsCollector",
    "podcasts": "app.collectors.podcasts:PodcastCollector",
}


def collector_class(name: str) -> type[Collector]:
    module, _, cls = REGISTRY[name].partition(":")
    return getattr(importlib.import_module(module), cls)  # type: ignore[no-any-return]
