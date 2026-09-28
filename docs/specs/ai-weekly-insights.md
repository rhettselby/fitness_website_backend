# AI Weekly Insights — Spec (draft)

**Status:** Draft for discussion · **Last updated:** 2026-09-27

Every Monday, each active user gets a short, personal recap of their past week, e.g.
*"You ran 142 minutes this week, 30% more than your 4-week average, and moved up to #2 in
Morning Crew."* Python computes the numbers and Claude picks the interesting ones and writes
the copy. Users see it as a card in the app, and later possibly by email.

---

## 1. Goals and non-goals

**Goals**
- A recap that feels personal and specific: records, streaks, real changes. Not "Great job this week!"
- Every number shown is correct. Numbers come from our database, never from the model's arithmetic.
- No noticeable load on the single Celery worker (`Procfile`: `--concurrency 1`, Beat embedded).
- Cheap enough to run for everyone each week, with a kill switch.
- Idempotent: a Beat double-fire or a retry never produces two insights for the same user and week.

**Non-goals (v1)**
- Coaching, training plans, or medical, injury, nutrition, or weight advice.
- Chatting with your data or on-demand insights.
- Push notifications (the app is web-only today).

---

## 2. What data we actually have

The insight can only be as good as the stored data. What exists today:

| Model | Useful fields | Notes |
|---|---|---|
| `Cardio` | `activity`, `duration` (min), `date`, `score`, `verified`, `image` | Wearable rows are prefixed: `"Strava: Run"`, `"Whoop: Running"` |
| `Gym` | `activity`, `exercises`, `date`, `score`, `verified` | `exercises` = `[{name, sets: [{reps, weight}]}]` (from `Add_Workout/index.tsx`). The weight unit is not recorded. |
| `Sport` | `sport`, `duration`, `level` (recreational/competitive) | |
| `Booze` | `number_of_drinks`, `drinks` | See open question on booze |
| `Like`, `Comment` | generic FK to any workout | Social signal |
| `WeeklyScore` | `user`, `week_start`, `score` | Archived by `reset_scores` Monday 08:00 UTC |
| `FitnessGroup` | `members` | Group rank = `WeeklyScore` filtered to members (same as the history API) |
| `WearableConnection` | `is_active`, `last_sync` | Needed to avoid false "you did less" claims (§5.4) |

**Gap: we compute rich wearable metrics during sync and then throw them away.**
`wearables/views.py` reads Strava `average_speed`, Whoop `strain`, and Oura `intensity` to
compute points, but only `activity`, `duration`, and `score` are saved. So:

- ✅ "You ran **30% longer** this week" (minutes) is possible today.
- ❌ "You ran **30% farther**" or "your pace improved 12s/mi" is not, because we don't store distance or pace.

→ Recommend adding nullable `distance_m`, `avg_speed`, and `intensity` fields to `Cardio`/`Sport`
now (Phase 1.5) so history accumulates before the Phase 2 insights need it.

---

## 3. Insight catalog (brainstorm)

Each idea is a **fact generator**: a pure function that returns zero or more facts with a
salience score (§5.2). Claude chooses among the top-ranked facts. Phase tags: **v1** = ship first,
**v2** = needs normalization or more data, **v3** = new surface.

### Volume and trends
| Insight | Example copy | Needs | Phase |
|---|---|---|---|
| Minutes per activity vs 4-week avg | "142 run minutes, 30% above your 4-week average" | Cardio + activity normalization | v1 |
| Sessions and active days | "5 workouts across 4 days, your most active week since July" | all workout types | v1 |
| Points vs your history | "1,240 pts, your 2nd-best week ever" | `WeeklyScore` | v1 |
| Mix shift | "Strength was 60% of your sessions, up from 25%" | type counts | v1 |

### Consistency
| Insight | Example | Needs | Phase |
|---|---|---|---|
| Weekly streak | "6 weeks in a row with 3+ workouts" | history | v1 |
| Comeback | "First workout in 3 weeks. Welcome back." | history | v1 |
| Day streak | "Active 7 days straight" (fact only, no advice) | dates | v1 |

### Personal records
| Insight | Example | Needs | Phase |
|---|---|---|---|
| Longest session per activity | "Your 95-min ride was your longest ever" | all-time max by normalized activity | v1 |
| Heaviest set per exercise | "Bench PR: 185 × 5" | `Gym.exercises` + name normalization | v1 |
| Session volume PR | "Your heaviest session: 12,400 total lbs lifted" | Σ reps × weight | v1 |
| Estimated 1RM trend (Epley) | "Squat e1RM up 10 lb this month" | exercise normalization | v2 |
| Best week ever (points) | "New personal best: 1,610 pts" | `WeeklyScore` | v1 |

### Variety
| Insight | Example | Needs | Phase |
|---|---|---|---|
| New activity | "First pickleball session logged 🎾" | all-time activity set | v1 |
| Neglected exercise | "No squats in 4 weeks" | reliable exercise names | v2 |
| Muscle-group balance | "Lots of push, not much pull" | exercise → muscle taxonomy | v3 |

### Timing
| Insight | Example | Needs | Phase |
|---|---|---|---|
| Time-of-day habit | "4 of 5 workouts before 8am, early bird" | `date` in Pacific (no per-user TZ stored) | v1 |
| Day-of-week pattern | "Sundays are your big day" | 8+ weeks of history | v2 |

### Social and competition
| Insight | Example | Needs | Phase |
|---|---|---|---|
| Group rank + change | "Up 3 spots to #2 in Morning Crew" | `WeeklyScore` × groups | v1 |
| Gap to next rank | "Just 40 pts behind {rival}" | same | v1 |
| Most-liked workout | "Your Saturday run got 6 likes" | `Like` / `Comment` | v1 |
| Group recap (one per group) | "Morning Crew logged 2,300 minutes; biggest jump: {rival}" | new model | v3 |

### Points coaching (also a product lever)
| Insight | Example | Needs | Phase |
|---|---|---|---|
| Missed photo bonus | "3 workouts had no photo, 150 bonus pts left on the table" | `verified`, `image`, `VERIFICATION_BONUS` | v1 |

### Forward-looking nudge (always one, computed in Python)
| Insight | Example | Phase |
|---|---|---|
| Streak keeper | "3 workouts by Sunday keeps your 6-week streak alive" | v1 |
| Near-record | "2 more workouts would make September your best month" | v1 |
| Rank chase | "~200 pts would have tied #1 this week" | v1 |

### Wearable metrics (blocked on the §2 gap)
| Insight | Example | Needs | Phase |
|---|---|---|---|
| Distance / pace | "Avg pace 8:42/mi, 12s faster than last month" | `distance_m`, `avg_speed` | v2 |
| Strain | "Your hardest Whoop session: strain 16.2" | `intensity` | v2 |
| Intensity mix | "Half your Oura sessions were 'hard'" | `intensity` | v2 |

---

## 4. Core design principle

**Python computes facts. Claude selects and phrases them. A validator checks Claude used only our numbers.**

```
workouts ──► facts.py (pure, tested) ──► top-N facts JSON ──► Claude ──► validator ──► WeeklyInsight
                                                   │                        │ fail
                                                   └──── template_insight ◄─┘
```

This gives us:
- **Correct numbers.** "30%" is computed once, in code, with sensible thresholds.
- **A free fallback.** Every fact has a human-readable `text`, so a template insight is always
  available if Claude refuses, errors, or fails validation.
- **Testability.** `facts.py` is pure functions that are easy to unit test without the API.

---

## 5. Architecture

### 5.1 New Django app: `insights/`

```
insights/
  models.py      WeeklyInsight, InsightBatch
  activity.py    normalize_activity(), normalize_exercise()
  facts.py       build_facts(...) -> list[Fact]         (pure; no DB, no API)
  loaders.py     bulk-load one week + baseline for many users (no N+1)
  prompt.py      SYSTEM_PROMPT, INSIGHT_FORMAT, build_params(facts)
  validate.py    validate(output, facts), template_insight(facts)
  tasks.py       submit_weekly_insights, poll_insight_batches, regenerate_insight
  views.py / urls.py / admin.py / tests.py
```

A separate app keeps `groups/tasks.py` from growing further (it already hosts
`verify_workout_image`). Reuse `groups.tasks.previous_monday_pacific()` so insight weeks line
up exactly with leaderboard weeks.

**Activity normalization** (`activity.py`) is the key helper for v1: strip the `Strava: ` or `Whoop: `
prefix, lowercase, keep only alphanumerics, then map synonyms, e.g. `run/running/virtualrun/trailrun → run`,
`ride/cycling/spin → cycling`. Reuse the lists already in `wearables/views.py` (`CARDIO`,
`SPEED_INCLUDED`) where possible.

### 5.2 Facts

```python
@dataclass
class Fact:
    id: str           # stable, e.g. "delta_minutes_run", "pr_heaviest_set_bench_press"
    category: str     # volume | consistency | record | variety | timing | social | points | nudge
    text: str         # correct, display-ready: "Run minutes: 142 this week vs 109 avg over the previous 4 weeks (+30%)"
    salience: float   # 0–1, ranks what gets sent to Claude
```

**Salience defaults** (constants, tune after dry run): record 0.9 · streak milestone (3/5/10/…) 0.8 ·
rank change ≥ 2 places 0.75 · delta ≥ 25% → 0.6 + min(|Δ|,100)/400 · new activity 0.6 ·
≥ 3 likes 0.5 · missed photo bonus ≥ 2 0.4 · plain totals 0.3. Send the top 12 plus one nudge.

**Delta guardrails**
- Baseline = mean of the previous 4 weeks, *including zero weeks*.
- Only show a % when the baseline is ≥ 30 min (activity minutes) or ≥ 2 sessions. Otherwise use
  absolute numbers ("20 more minutes"). This avoids "+400%" from 5 → 25 min.
- Show "2×" / "3×" instead of percentages above +100%.

### 5.3 Data model

```python
class InsightBatch(models.Model):
    anthropic_batch_id = models.CharField(max_length=100, unique=True)
    week_start = models.DateField(unique=True)          # one batch per week → idempotent submit
    status = models.CharField(max_length=20, default="submitted")  # submitted | ended | abandoned
    request_count = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)


class WeeklyInsight(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="weekly_insights")
    week_start = models.DateField()                     # Pacific Monday, same as WeeklyScore
    status = models.CharField(max_length=20, default="pending")    # pending | ready | skipped
    source = models.CharField(max_length=20, blank=True)           # claude | template
    headline = models.CharField(max_length=120, blank=True)
    body = models.TextField(blank=True)
    highlights = models.JSONField(default=list)         # [{label, value}]
    suggestion = models.CharField(max_length=200, blank=True)
    facts = models.JSONField(default=dict)              # exact payload sent, for debugging and regenerating
    batch = models.ForeignKey(InsightBatch, null=True, blank=True, on_delete=models.SET_NULL)
    model = models.CharField(max_length=64, blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    error = models.TextField(blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    rating = models.SmallIntegerField(null=True, blank=True)       # +1 / -1
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "week_start"], name="uniq_insight_user_week")]
```

`Profile` gets `ai_insights_enabled = models.BooleanField(default=True)`, which is separate from
`email_notifications` because one controls AI processing and the other controls email.

### 5.4 Pipeline

**Recommendation: use the Message Batches API rather than one Claude call per user.**

| | Per-user fan-out (like `send_monthly_reminders`) | **Batches API (recommended)** |
|---|---|---|
| Worker time | N sequential API calls on a **concurrency-1** worker, which delays wearable syncs | One submit call plus cheap polls |
| Cost | Standard | **50% off** |
| Latency | Minutes | Usually < 1 h, max 24 h (fine for a weekly recap) |
| Complexity | Simplest | +1 model, +1 polling task |
| Refusal fallbacks | Server-side `fallbacks` available | Not supported on batches, so we use the template fallback |

**1. `submit_weekly_insights`**: Beat, Monday 13:00 UTC (6am PDT / 5am PST)
- Runs five hours after `reset_scores` (08:00 UTC), so this week's `WeeklyScore` rows exist, and gives
  late Sunday-night wearable syncs time to land.
- `week_start = previous_monday_pacific()`; return early if an `InsightBatch` already exists for it.
- Eligible users: `is_active`, `profile.ai_insights_enabled`, and ≥ 1 workout in the week or the 4
  weeks before (lapsed users get a comeback insight). Honor `INSIGHTS_ALLOWLIST` during the dry run.
- Bulk-load all workouts for the 5-week window in one query per type (Cardio, Gym, Sport, Booze), plus
  `WeeklyScore`, like and comment counts, and all-time per-user maxima for records. Group them in Python.
  No per-user queries, so we don't reintroduce the N+1 fixed in 366565f.
- `build_facts()` per user. If there are no facts, set `status=skipped`.
- **Wearable-outage guard:** if a user has a `WearableConnection` with `is_active=False` or
  `last_sync` older than 48 h, drop negative volume deltas. A silent Whoop disconnect should
  never become "you worked out 80% less."
- `bulk_create(..., ignore_conflicts=True)` pending `WeeklyInsight` rows, then submit one batch with
  `custom_id=f"insight-{insight.id}"`, and save the `InsightBatch`.

**2. `poll_insight_batches`**: Beat, every 15 min at minutes 3, 18, 33, 48 (off the wearable-sync ticks)
- For each `InsightBatch(status="submitted")`: `retrieve()`. If it hasn't ended, return.
- Once ended, stream `results()` and key each result by `custom_id` (results arrive in any order).
  Validate each one (§5.6) and write `ready` + `source="claude"`, or fall back to the template.
- Any pending row with no result, or with a result of type `errored`, `expired`, or `canceled`,
  gets the template fallback.
- If still unended after 25 h: cancel, apply the template fallback to all pending rows, and set `status=abandoned`.

**3. `regenerate_insight(insight_id)`**: admin action, synchronous `messages.create`
- Same `build_params()`, reusing the stored `facts`. Useful for prompt iteration and support.
- Error handling copies the chain in `verify_workout_image` (auth → raise; 429, connection
  errors, or 5xx → `self.retry`; 400/404 → template fallback).

Beat entries (`settings.CELERY_BEAT_SCHEDULE`):
```python
'weekly-insights-submit': {
    'task': 'insights.tasks.submit_weekly_insights',
    'schedule': crontab(minute=0, hour=13, day_of_week=1),
},
'weekly-insights-poll': {
    'task': 'insights.tasks.poll_insight_batches',
    'schedule': crontab(minute='3,18,33,48'),
},
```

### 5.5 The Claude request

```python
MODEL = "claude-opus-5"

params = {
    "model": MODEL,
    "max_tokens": 16000,                        # a ceiling, not a cost; room for adaptive thinking
    "output_config": {"effort": "low", "format": INSIGHT_FORMAT},
    "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
    "messages": [{"role": "user", "content": json.dumps(payload, sort_keys=True)}],
}
```

- **Model:** `claude-opus-5` by default. `claude-sonnet-5` costs about 40% as much. That's a
  product decision to make after the dry run (§10).
- **Thinking:** adaptive thinking is on by default on Opus 5, and `effort: "low"` keeps it short.
  Raise it to `"medium"` only if the dry run shows weak fact selection.
- **Caching:** Opus 5 caches prefixes of 512+ tokens, so the stable system prompt can cache.
  In batches this is best-effort. Check `usage.cache_read_input_tokens`.
- `sort_keys=True` keeps the prompt deterministic.

**Structured output**, in the same style as `VERDICT_FORMAT`:
```python
INSIGHT_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "headline":   {"type": "string"},
            "body":       {"type": "string"},
            "highlights": {"type": "array", "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "value": {"type": "string"}, "fact_id": {"type": "string"}},
                "required": ["label", "value", "fact_id"],
                "additionalProperties": False,
            }},
            "suggestion": {"type": "string"},
            "fact_ids":   {"type": "array", "items": {"type": "string"}},
        },
        "required": ["headline", "body", "highlights", "suggestion", "fact_ids"],
        "additionalProperties": False,
    },
}
```
Structured outputs don't support `maxLength`, so the validator enforces length limits.

**User payload** (the example is trimmed):
```json
{
  "week": {"start": "2026-09-21", "end": "2026-09-27"},
  "user": {"name": "rhett", "weeks_on_app": 14},
  "facts": [
    {"id": "delta_minutes_run", "category": "volume", "text": "Run minutes: 142 this week vs 109 avg over the previous 4 weeks (+30%)"},
    {"id": "rank_morning_crew", "category": "social", "text": "Ranked #2 of 7 in Morning Crew, up from #5 last week"},
    {"id": "pr_heaviest_set_bench_press", "category": "record", "text": "Bench press: 185 × 5, heaviest set ever (previous best 175 × 5)"},
    {"id": "nudge_streak", "category": "nudge", "text": "3 workouts by Sunday Oct 4 extends the 6-week streak to 7"}
  ]
}
```

**System prompt (v0):**
```text
You write a short weekly recap for a user of a social fitness app where friends earn
points for workouts and compete on group leaderboards.

You receive the user's facts for the past week. Each fact has an id and a pre-computed,
correct statement. Pick the 1–3 most interesting facts (records, streaks, and big changes
beat routine totals) and write:
- headline: one punchy line, under 90 characters
- body: 2–3 sentences in second person that tie the chosen facts together
- highlights: up to 3 stat chips, each quoting a number from a fact
- suggestion: the nudge fact, phrased as a friendly goal for next week
- fact_ids: every fact id you used

Use only numbers that appear in the facts, written as digits exactly as given; don't
calculate new ones. Be specific, casual and a little playful, with no generic
motivational filler. After a light week, be warm rather than guilt-inducing. No medical,
injury, nutrition, weight-loss or body-image commentary. If drinks come up, mention them
neutrally and never encourage drinking more. Refer to other people only with the
{rival} token when a fact contains it.
```

### 5.6 Validation and fallback

Any failed check produces `template_insight(facts)`, with `source="template"` and the error recorded:

1. `stop_reason == "end_turn"`. A `refusal` or `max_tokens` stop means no usable output.
2. The output parses as JSON.
3. Lengths: headline ≤ 90 characters, body ≤ 400, suggestion ≤ 160, ≤ 3 highlights.
4. `fact_ids` ⊆ the ids we sent.
5. **Number grounding:** every numeric token (`\d[\d,.:]*%?`) in headline, body, highlights,
   and suggestion appears in the text of a fact we sent. This one check catches most hallucinations.
6. Replace `{rival}` tokens with the real username.

`template_insight`: headline = the top fact's `text`, trimmed; body = the next two facts; suggestion = the nudge.
It's plain, but always correct.

---

## 6. API

JWT auth plus `JsonResponse`, matching `groups/views.py`. Import an existing `get_user_from_token`
rather than adding another copy; five views modules already define one.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/insights/latest/` | `{"insight": {...}}` or `{"insight": null}` (only `status=ready`) |
| GET | `/api/insights/?limit=8` | `{"insights": [...]}` newest first |
| POST | `/api/insights/<id>/read/` | sets `read_at` |
| POST | `/api/insights/<id>/rating/` | body `{"rating": 1 \| -1}` |

`ai_insights_enabled` is added to the existing profile edit endpoint.

```json
{
  "id": 42,
  "week_start": "2026-09-21",
  "week_end": "2026-09-27",
  "headline": "Up 3 spots and a bench PR",
  "body": "You ran 142 minutes this week, 30% above your usual, and your 185 × 5 bench is a new best. That climb put you at #2 in Morning Crew.",
  "highlights": [{"label": "Run minutes", "value": "142 (+30%)"}, {"label": "Morning Crew", "value": "#2"}],
  "suggestion": "3 workouts by Sunday makes it a 7-week streak.",
  "source": "claude",
  "read": false,
  "rating": null
}
```
The frontend shows a small "AI" label when `source == "claude"`.

---

## 7. Delivery

- **v1, in-app card** on Home or ProfilePage (`Web_Back/frontend/src/components/`), with
  unread dot, 👍/👎, and a link to past weeks.
- **v2, email:** a `weekly_insight` template via `send_templated_email`, which respects
  `email_notifications` and includes the unsubscribe link. Send from the poll task once a row is ready.
- **v3, share to feed:** post your insight card to the feed (`posts` app).

---

## 8. Privacy and safety

- **Data minimization:** send only workout facts and the user's own username. Never send email,
  birthday, location, or images. Other users appear as `{rival}` and are substituted after generation.
- **Opt-out:** `ai_insights_enabled`. Mention AI processing of workout data (via Anthropic) on the About or privacy page.
- **Tone guardrails** in the prompt (no medical or body-image talk, no guilt, drinks neutral) plus the
  validator. A 👎 rating goes into a review queue in the admin.
- **Kill switch:** `INSIGHTS_ENABLED` env var checked at the top of both tasks.

---

## 9. Testing and evaluation

- `facts.py` and `activity.py` unit tests (plain `TestCase` via `manage.py test`; never ad-hoc
  scripts against default settings, because `db.sqlite3` is tracked in git):
  - normalization table (`"Strava: Run"`, `"Whoop: Running"`, `"run"` → `run`)
  - delta thresholds (small baseline → absolute, not %), zero-week baselines, 2× display
  - records (first-ever workout isn't a "record"), streak boundaries, Pacific week edges
  - wearable-outage guard drops negative deltas
- `validate.py`: an invented number, an unknown fact id, an over-long headline, and a refusal each
  produce the template.
- Task tests with a mocked `anthropic.Anthropic` (`batches.create`, `retrieve`, `results`):
  idempotent submit, results in shuffled order, expired or errored results, 25 h abandon.
- **Golden set before launch:** 15–20 synthetic user-weeks covering a record week, a lapsed user,
  a first week ever, a heavy-booze week, a tied rank, a single-workout week, and a Whoop outage. Read every
  output by hand. (`/claude-api build-eval` can turn this into a repeatable eval later.)

---

## 10. Cost estimate (rough; re-measure from stored `input_tokens` / `output_tokens`)

Per user per week: ~1.5K input tokens (prompt + ~12 facts) and ≤ ~1K output tokens (JSON + low-effort thinking).

| Model | Standard | **Batch (50%)** | 100 users / month (batch) |
|---|---|---|---|
| `claude-opus-5` ($5 / $25 per MTok) | ~$0.03 | **~$0.016** | **~$7** |
| `claude-sonnet-5` ($2 / $10 per MTok) | ~$0.013 | ~$0.007 | ~$3 |

Skipped users (no facts) and template fallbacks cost $0.

---

## 11. Rollout

| Phase | Scope |
|---|---|
| **0: Dry run** | Tasks run for `INSIGHTS_ALLOWLIST` only; API hidden; review outputs and token usage in the admin for 2 weeks |
| **1: Launch** | All users, in-app card, opt-out toggle, rating, admin regenerate |
| **1.5: Data** | Store `distance_m`, `avg_speed`, `intensity` at wearable sync (migration + 3 sync functions) |
| **2** | Email delivery · pace/distance/strain facts · exercise-name normalization (e1RM, neglected lifts) |
| **3** | Group recaps · share-to-feed · muscle-group balance |

---

## 12. Open questions

1. **Model:** start on `claude-opus-5` and decide on Sonnet 5 after seeing dry-run quality and cost?
2. **Batch vs fan-out:** recommending batch because of the concurrency-1 worker. Roughly how many weekly-active users are there today?
3. **Booze:** include neutrally, leave out of insights entirely, or lean into it playfully?
4. **Rivals:** OK to name group members in someone else's insight (they already appear on leaderboards), or keep them anonymous ("the person above you")?
5. **Email in v1**, or in-app only until the copy proves itself?
6. **Weight units:** is `weight` in `Gym.exercises` always lb? The frontend doesn't say.
7. **Phase 1.5 now?** Storing distance, pace, and strain early means months of history are ready when those insights ship.
8. **Time zone:** Pacific week and time-of-day for everyone (matches the leaderboard), since there's no per-user time zone?
