"""X (Twitter) read budget.

The monthly post-read budget (X_MONTHLY_POST_BUDGET, ~80% of the tier cap) is split evenly
across the days left in the month and the active topics. Reads are recorded in two places:

- `x_budget(month, posts_read)` — the monthly total, compared against the X dashboard.
- `api_quota(platform='x', scope=<topic slug>, day)` — per-topic daily spend, so running
  collect twice in a day doesn't double a topic's allocation.

Every tweet object returned (in `data` and in `includes.tweets`) counts as a read.
"""

import calendar
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.collectors.base import quota_add
from app.models import api_quota, x_budget


def month_start(d: date) -> date:
    return d.replace(day=1)


def days_left_in_month(today: date) -> int:
    """Including today."""
    return calendar.monthrange(today.year, today.month)[1] - today.day + 1


def topic_daily_allocation(
    monthly_budget: int,
    month_used: int,
    used_today_all_topics: int,
    today: date,
    n_topics: int,
) -> int:
    """Posts one topic may read today.

    Computed from the budget remaining at the *start* of today so that the first topic to
    run doesn't shrink the others' share.
    """
    if n_topics <= 0:
        return 0
    used_before_today = max(0, month_used - used_today_all_topics)
    remaining = max(0, monthly_budget - used_before_today)
    return remaining // days_left_in_month(today) // n_topics


@dataclass
class BudgetStatus:
    month: date
    monthly_budget: int
    posts_read: int
    used_today_all_topics: int
    daily_allocation_per_topic: int
    n_topics: int

    @property
    def remaining(self) -> int:
        return max(0, self.monthly_budget - self.posts_read)


class XBudget:
    """DB-backed budget tracker. Each `spend` call commits immediately."""

    def __init__(self, engine: Engine, monthly_budget: int, n_topics: int) -> None:
        self.engine = engine
        self.monthly_budget = monthly_budget
        self.n_topics = n_topics

    def status(self, today: date) -> BudgetStatus:
        with self.engine.connect() as conn:
            month_used = conn.execute(
                select(x_budget.c.posts_read).where(x_budget.c.month == month_start(today))
            ).scalar()
            today_used = conn.execute(
                select(func.coalesce(func.sum(api_quota.c.units), 0)).where(
                    api_quota.c.platform == "x", api_quota.c.day == today
                )
            ).scalar_one()
        month_used = int(month_used or 0)
        return BudgetStatus(
            month=month_start(today),
            monthly_budget=self.monthly_budget,
            posts_read=month_used,
            used_today_all_topics=int(today_used),
            daily_allocation_per_topic=topic_daily_allocation(
                self.monthly_budget, month_used, int(today_used), today, self.n_topics
            ),
            n_topics=self.n_topics,
        )

    def used_today(self, topic_slug: str, today: date) -> int:
        with self.engine.connect() as conn:
            v = conn.execute(
                select(api_quota.c.units).where(
                    api_quota.c.platform == "x",
                    api_quota.c.scope == topic_slug,
                    api_quota.c.day == today,
                )
            ).scalar()
        return int(v or 0)

    def remaining_today(self, topic_slug: str, today: date) -> int:
        st = self.status(today)
        topic_left = st.daily_allocation_per_topic - self.used_today(topic_slug, today)
        return max(0, min(topic_left, st.remaining))

    def spend(self, topic_slug: str, today: date, n: int) -> None:
        if n <= 0:
            return
        with self.engine.begin() as conn:
            stmt = pg_insert(x_budget).values(month=month_start(today), posts_read=n)
            stmt = stmt.on_conflict_do_update(
                index_elements=[x_budget.c.month],
                set_={"posts_read": x_budget.c.posts_read + n, "updated_at": func.now()},
            )
            conn.execute(stmt)
            quota_add(conn, "x", topic_slug, today, n)
