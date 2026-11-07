"""The typed responses of the API (DL-116).

Each model describes what one endpoint returns, so the interactive documentation shows the exact
fields and a change to a lookup that breaks the shape fails the tests instead of a client. Row
models allow extra fields: a lookup may gain a column without breaking existing clients.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Row(BaseModel):
    """A row of a table: extra columns are kept, so adding one never breaks a client."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Health(BaseModel):
    status: Literal["ok"] = Field(description="Always 'ok' when the service can answer.")
    version: str = Field(description="The API version.")
    data_through: str = Field(description="The latest month in the published warehouse.")
    months_of_data: int
    data_version: str = Field(
        description="A short hash of the input extracts the data came from: the same value on "
        "every forecast of one monthly run, so a deploy can check it serves the latest run."
    )
    data_source: Literal["image", "s3"] = Field(
        description="Where the data came from: the copy in the service's image, or the current "
        "release in the data bucket."
    )
    auth: Literal["api-key", "open"] = Field(
        description="Whether an X-API-Key header is required on /v1 endpoints."
    )


class Status(Row):
    latest_month: str
    latest_share_pct: float = Field(description="Zilretta's share of the three-class visits.")
    peak_month: str
    peak_share_pct: float
    change_since_peak_pct: float
    months_of_data: int
    freshness: str = Field(
        description="'fresh' or 'stale' (the newest month is over 100 days old)."
    )
    age_days: int | None = None
    stale_banner: str | None = None
    last_run_status: str | None = None
    monitoring: str | None = None
    limits: list[str] = Field(description="What the data cannot say. Read these before quoting.")
    next_month: str | None = None
    next_month_forecast_pct: float | None = None
    next_month_range90_pct: list[float] | None = None


class ShareMonth(Row):
    month: str
    month_id: int = Field(description="YYYYMM, for example 202507.")
    zilretta_visits: int
    share_pct: float | None = None
    in_dip: bool = Field(description="True for a month in the March to July 2024 capture dip.")


class ShareMonthly(BaseModel):
    by: Literal["month"]
    rows: list[ShareMonth]
    note: str


class ShareYear(Row):
    year: int
    zilretta_visits: int
    months: int
    share_pct: float | None = None
    partial_year: bool


class ShareYearly(BaseModel):
    by: Literal["year"]
    rows: list[ShareYear]
    note: str


class SegmentRow(Row):
    group: str
    zilretta_visits: int
    category_visits: int = Field(description="Visits of Zilretta, generic steroids and NSAID/OTC.")
    share_pct: float
    small_segment: bool = Field(description="True for a group under 5,000 visits: read it loosely.")


class Segments(BaseModel):
    by: str
    period: str
    rows: list[SegmentRow]
    note: str


class NationalRow(Row):
    months_ahead: int
    month: str
    forecast_pct: float
    low_90_pct: float
    high_90_pct: float
    model: str


class NationalForecast(BaseModel):
    view: Literal["national"]
    rows: list[NationalRow]
    coverage_in_testing: str | None = None
    note: str


class SpecialtyRow(Row):
    specialty: str
    one_month_pct: float | None = Field(default=None, alias="1_month_pct")
    three_month_pct: float | None = Field(default=None, alias="3_month_pct")
    six_month_pct: float | None = Field(default=None, alias="6_month_pct")
    visits_at_origin: float


class SpecialtyForecast(BaseModel):
    view: Literal["specialty"]
    rows: list[SpecialtyRow]
    bias: str | None = Field(default=None, description="The bias recorded in testing. State it.")
    note: str


class AlertRow(Row):
    specialty: str
    age_band: str
    gender: str
    alert_score: float
    alert_flag: float
    origin_visits: float


class AlertSummary(Row):
    scored: int
    flagged: int
    precision: float | None = None


class Alerts(BaseModel):
    view: Literal["alerts"]
    summary: AlertSummary
    rows: list[AlertRow]
    note: str


class ScoreRow(Row):
    task: str
    horizon: int
    measure: str
    simple_rule: str
    simple_rule_score: float | None = None
    chosen_model: str
    chosen_model_score: float | None = None
    adopted: bool | None = None
    adopted_model: str | None = None
    verdict: str


class UseRow(Row):
    what: str
    status: str
    color: str
    use: str
    dont: str


class BiasRow(Row):
    horizon: int
    forecast_above_actual_pct: float


class PostHocRow(Row):
    horizon: int
    decline_share: float | None = None
    registered_simple_rule: float | None = None
    corrected_simple_rule: float | None = None
    model: float | None = None
    gain_over_corrected: float | None = None
    reliable: bool | None = None


class ModelResults(BaseModel):
    scoreboard: list[ScoreRow]
    use_and_dont: list[UseRow]
    segment_bias: list[BiasRow]
    alert_post_hoc_check: list[PostHocRow]
    decision: str


class Finding(Row):
    id: int
    finding: str
    evidence: str
    impact: str
    source: str


class Findings(BaseModel):
    count: int
    findings: list[Finding]


class Problem(BaseModel):
    """The body of an error response."""

    detail: str


class ModelInfo(Row):
    model_id: str = Field(description="For example 'segment:1' or 'alert:1'.")
    task: Literal["segment", "alert"]
    horizon: int
    model: str = Field(description="'logistic', or a rule such as 'last_month'.")
    model_version: str
    run_month_id: int = Field(description="The last month of data the model was fitted on.")
    trained_on_rows: int
    data_version: str
    library: str = Field(description="What fitted it; 'none (a rule)' for a rule.")
    n_parameters: int
    artifact_sha256: str = Field(description="A fingerprint of the saved numbers.")
    known_bias_pct: float | None = Field(
        default=None, description="How far its forecasts sat above what happened in testing."
    )


class Models(BaseModel):
    count: int
    models: list[ModelInfo]
    note: str


class SegmentKey(BaseModel):
    specialty: str
    age_band: str
    gender: str


class SegmentList(BaseModel):
    count: int
    segments: list[SegmentKey]
    note: str


class SegmentPrediction(Row):
    specialty: str
    age_band: str
    gender: str
    horizon: int
    target_month_id: int
    target_month: str
    model: str
    model_version: str
    forecast_pct: float = Field(description="The model's forecast of the share, in percent.")
    last_month_pct: float = Field(description="The 'same as last month' value, for comparison.")
    change_pp: float
    origin_visits: int
    small: bool = Field(description="True for a segment under 100 visits last month: noisy.")
    known_bias_pct: float | None = None
    stored_forecast_pct: float | None = Field(
        default=None, description="The batch job's stored forecast for the same segment."
    )
    matches_stored: bool | None = Field(
        default=None, description="Whether the live forecast equals the stored one."
    )
    note: str


class AlertPrediction(Row):
    specialty: str
    age_band: str
    gender: str
    model: str
    model_version: str
    origin_visits: int
    eligible: bool = Field(description="False when the segment is too small or has no share.")
    reason: str | None = None
    alert_score: float | None = None
    threshold: float | None = None
    flagged: bool | None = None
    stored_alert_score: float | None = None
    matches_stored: bool | None = None
    note: str
