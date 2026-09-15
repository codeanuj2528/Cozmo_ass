"""Calibrated intervals.

Split conformal prediction is the method. Its guarantee is distribution-free: if the
residuals on held-out benchmark captures are exchangeable with the residuals on a new
capture, an interval at the empirical (1 - alpha) quantile of absolute residuals covers the
truth at least (1 - alpha) of the time. No assumption that errors are Gaussian, which they
are not -- a wall length error is a mixture of plane-fit noise, a small scale bias, and the
occasional gross failure when a mirror gets mistaken for a doorway, and no single normal
distribution describes that.

The quantiles are fitted per (tier, quantity). A wall length from LiDAR and a wall length
from four photographs are not the same measurement and must not share an interval. Some
quantities calibrate in metres and some in percent: a ceiling height is equally hard to
measure in a small room or a large one, so its residual is absolute, while a wall length
error grows with the wall, so its residual is relative.

When no quantile has been fitted for a (tier, quantity) the interval falls back to the
propagated covariance of the fit that produced the number, and says so in `method`. A
reader can therefore always tell a calibrated interval from a propagated one, which matters
because confident garbage on thin input is explicitly penalised.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from cozmo.schema import IntervalMethod, Measure, Tier

DEFAULT_COVERAGE = 0.90
NORMAL_QUANTILE_90 = 1.6449

RELATIVE_QUANTITIES = {"wall_length", "floor_area", "perimeter", "damage_area", "damage_length"}

# Used only when nothing better exists. Deliberately wide: an honest wide interval scores
# better than a narrow one that does not cover, and these say "uncalibrated" in `method`.
PRIOR_HALF_WIDTHS: dict[tuple[str, str], float] = {
    ("lidar", "wall_length"): 0.030,
    ("lidar", "ceiling_height"): 0.030,
    ("lidar", "opening_width"): 0.040,
    ("lidar", "opening_height"): 0.060,
    ("lidar", "sill_height"): 0.060,
    ("lidar", "floor_area"): 0.060,
    ("video", "wall_length"): 0.60,
    ("video", "ceiling_height"): 0.60,
    ("video", "opening_width"): 0.60,
    ("video", "opening_height"): 0.60,
    ("video", "sill_height"): 0.60,
    ("video", "floor_area"): 0.60,
    ("photo", "wall_length"): 0.60,
    ("photo", "ceiling_height"): 0.60,
    ("photo", "opening_width"): 0.60,
    ("photo", "opening_height"): 0.60,
    ("photo", "sill_height"): 0.60,
    ("photo", "floor_area"): 0.60,
}

# Photo and video have no metric depth sensor. Their geometry still produces a
# small propagated sigma, which used to ship as a tight interval around a number
# that can be wrong by 20x (see the one-room video run). Until a conformal fit
# exists, every uncalibrated thin-tier interval is at least ±60%. That is the
# public-submission interval on the same brief, and it is the honest one.
THIN_TIERS = frozenset({"photo", "video"})
THIN_TIER_RELATIVE_FLOOR = 0.60


@dataclass
class QuantileEntry:
    quantity: str
    tier: str
    quantile: float
    relative: bool
    n_calibration: int
    empirical_coverage: float


@dataclass
class IntervalBook:
    """Fitted conformal quantiles, keyed by (tier, quantity)."""

    entries: dict[tuple[str, str], QuantileEntry] = field(default_factory=dict)
    coverage: float = DEFAULT_COVERAGE
    source: str = "uncalibrated"

    @classmethod
    def load(cls, path: Path | str | None) -> IntervalBook:
        if path is None:
            return cls()
        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        entries: dict[tuple[str, str], QuantileEntry] = {}
        for item in raw.get("entries", []):
            entry = QuantileEntry(**item)
            entries[(entry.tier, entry.quantity)] = entry
        return cls(
            entries=entries,
            coverage=float(raw.get("coverage", DEFAULT_COVERAGE)),
            source=str(raw.get("source", str(path))),
        )

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "coverage": self.coverage,
                    "source": self.source,
                    "entries": [e.__dict__ for e in self.entries.values()],
                },
                indent=2,
            )
        )

    def measure(
        self,
        quantity: str,
        value: float,
        tier: Tier | str,
        unit: str,
        propagated_sigma: float | None = None,
        floor_half_width: float = 0.0,
    ) -> Measure:
        """Attach an interval to a value, by the best method available for it."""
        tier_name = tier.value if isinstance(tier, Tier) else str(tier)
        entry = self.entries.get((tier_name, quantity))

        if entry is not None:
            half = entry.quantile * abs(value) if entry.relative else entry.quantile
            method = IntervalMethod.CONFORMAL
        elif propagated_sigma is not None and np.isfinite(propagated_sigma):
            half = NORMAL_QUANTILE_90 * float(propagated_sigma)
            method = IntervalMethod.PROPAGATED
        else:
            prior = PRIOR_HALF_WIDTHS.get((tier_name, quantity))
            if prior is None:
                prior = 0.25 if quantity in RELATIVE_QUANTITIES else 0.15
            half = prior * abs(value) if quantity in RELATIVE_QUANTITIES else prior
            method = IntervalMethod.PRIOR

        half = max(float(half), float(floor_half_width))
        if (
            tier_name in THIN_TIERS
            and method is not IntervalMethod.CONFORMAL
            and np.isfinite(value)
        ):
            half = max(half, THIN_TIER_RELATIVE_FLOOR * abs(float(value)))

        # Every quantity in this schema is a length, an area or a height, and none of them
        # can be negative. An interval that runs below zero is not conservative, it is
        # wrong: it assigns probability to a wall of negative length. Before this clamp the
        # five real captures published 167 such bounds, including wall areas of
        # 0.000 m2 [-0.150, 0.150] and ceiling heights of 0.000 m [-0.030, 0.030].
        #
        # Truncating at zero leaves the interval asymmetric about the point estimate, which
        # is correct for a quantity bounded below: the information that a length cannot be
        # negative is real information and the interval should carry it.
        #
        # The same holds for the value. A length computed a hair below zero, a sill a centimetre under the
        # floor it was read from, is zero; left negative it lies below its own interval, the output contract
        # refuses it, and the whole plan is lost: the video tier's core stopped on a walk at -0.01 m (fix loop
        # round 5). A value further below zero than its own half-width is a defect, and still fails.
        if np.isfinite(value) and -half <= value < 0.0:
            value = 0.0
        lo = max(0.0, float(value) - half)
        return Measure(
            value=float(value),
            lo=lo,
            hi=float(value) + half,
            unit=unit,
            coverage=self.coverage,
            method=method,
        )


def fit_quantiles(
    residuals: dict[tuple[str, str], list[tuple[float, float]]],
    coverage: float = DEFAULT_COVERAGE,
    source: str = "benchmark",
) -> IntervalBook:
    """Fit split-conformal quantiles from (predicted, truth) pairs.

    The finite-sample correction is the standard one: with n calibration points the
    quantile is taken at ceil((n + 1)(1 - alpha)) / n, which is what makes the coverage
    guarantee hold at small n rather than only asymptotically. With very few points that
    index exceeds 1 and no finite quantile is justified, so the entry is not written and
    the quantity falls back to a propagated interval that is honest about its provenance.
    """
    book = IntervalBook(coverage=coverage, source=source)
    for (tier, quantity), pairs in residuals.items():
        if len(pairs) < 3:
            continue
        relative = quantity in RELATIVE_QUANTITIES
        scores = []
        for predicted, truth in pairs:
            if not (np.isfinite(predicted) and np.isfinite(truth)):
                continue
            error = abs(predicted - truth)
            scores.append(error / max(abs(truth), 1e-6) if relative else error)
        if len(scores) < 3:
            continue
        scores_arr = np.sort(np.asarray(scores))
        n = len(scores_arr)
        rank = int(np.ceil((n + 1) * coverage))
        if rank > n:
            continue
        quantile = float(scores_arr[rank - 1])
        empirical = float((scores_arr <= quantile).mean())
        book.entries[(tier, quantity)] = QuantileEntry(
            quantity=quantity,
            tier=tier,
            quantile=quantile,
            relative=relative,
            n_calibration=n,
            empirical_coverage=empirical,
        )
    return book
