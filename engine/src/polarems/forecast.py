"""Quantile forecasting, and the one place AI genuinely earns its place.

Dispatch is a MILP, not a neural network, and we say so. The learned component
does exactly one job: it produces *calibrated uncertainty* for load, PV and wind
at 1-48 h. The optimiser then sizes reserve and battery headroom from the
forecast's own predicted spread (p90 - p50) instead of a fixed rule. The
ablation (point forecast vs quantile forecast, same optimiser) is the evidence
that this matters.

Model choice: LightGBM with a quantile objective. Small tabular data, seconds to
train, feature importances that survive a judge's question. An LSTM here would
be training a sequence model on data the team does not have.

Because ERA5 is a reanalysis (a hindcast), a realistic forecast has to be
manufactured: :class:`NWP` degrades the truth with AR(1) error whose magnitude
grows with lead time, calibrated to typical polar NWP skill. The controller
never sees the truth -- only this degraded field. Perfect foresight is reserved
for the oracle baseline.
"""

from __future__ import annotations

import warnings
import json
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

try:  # optional: the pipeline degrades to a statistical forecaster without it
    import lightgbm as lgb

    HAS_LGB = True
except Exception:  # pragma: no cover
    HAS_LGB = False

from .config import Config

TARGETS = ["load_kw", "pv_kw", "wind_kw"]
QUANTILES = {"p10": 0.1, "p50": 0.5, "p90": 0.9}
TRAIN_LEADS = [1, 2, 3, 6, 9, 12, 18, 24, 30, 36, 42, 48]

# Error growth of the manufactured NWP, as a fraction of field standard
# deviation at 48 h. Wind direction and speed degrade faster than temperature.
NWP_ERROR_48H = {
    "temp_c": 0.35,
    "wind_ms": 0.55,
    "wind_dir_deg": 0.50,
    "ghi_wm2": 0.45,
    "snowfall_cm": 0.70,
    "pressure_hpa": 0.25,
    "rh_pct": 0.40,
}


class NWP:
    """Turns reanalysis truth into a lead-time-degraded forecast field."""

    def __init__(self, truth: pd.DataFrame, seed: int = 26061):
        self.truth = truth
        self.seed = seed
        self._sigma = {c: float(truth[c].std()) for c in NWP_ERROR_48H if c in truth}

    def _noise(self, key: int, n: int, col: str, leads: np.ndarray) -> np.ndarray:
        rng = np.random.default_rng((self.seed * 1_000_003 + key) % (2**63))
        # AR(1) so the error is smooth in time rather than white
        eps = rng.standard_normal(n)
        for i in range(1, n):
            eps[i] = 0.85 * eps[i - 1] + np.sqrt(1 - 0.85**2) * eps[i]
        scale = self._sigma.get(col, 1.0) * NWP_ERROR_48H[col] * np.sqrt(leads / 48.0)
        return eps * scale

    def rebase(self, truth: pd.DataFrame) -> "NWP":
        """Same forecaster, different truth (a season slice, or a stress scenario)."""
        return NWP(truth, self.seed)

    def forecast(self, issue_time: pd.Timestamp, horizon_h: int) -> pd.DataFrame:
        """Forecast weather for issue_time+1h .. issue_time+horizon_h."""
        start = issue_time + pd.Timedelta(hours=1)
        idx = pd.date_range(start, periods=horizon_h, freq="h")
        idx = idx[idx.isin(self.truth.index)]
        if len(idx) == 0:
            return pd.DataFrame(columns=self.truth.columns)
        out = self.truth.loc[idx].copy()
        leads = np.arange(1, len(idx) + 1, dtype=float)
        key = int(issue_time.value // 3_600_000_000_000)
        for col in NWP_ERROR_48H:
            if col not in out:
                continue
            out[col] = out[col].to_numpy() + self._noise(key, len(idx), col, leads)
        out["ghi_wm2"] = out["ghi_wm2"].clip(lower=0.0)
        out["snowfall_cm"] = out["snowfall_cm"].clip(lower=0.0)
        out["wind_ms"] = out["wind_ms"].clip(lower=0.0)
        out["rh_pct"] = out["rh_pct"].clip(0.0, 100.0)
        out["wind_dir_deg"] = out["wind_dir_deg"] % 360.0
        out["lead_h"] = leads
        return out


def _calendar_features(idx: pd.DatetimeIndex) -> pd.DataFrame:
    doy = idx.dayofyear.to_numpy().astype(float)
    hour = idx.hour.to_numpy().astype(float)
    return pd.DataFrame(
        {
            "hour_sin": np.sin(2 * np.pi * hour / 24),
            "hour_cos": np.cos(2 * np.pi * hour / 24),
            "doy_sin": np.sin(2 * np.pi * doy / 365.25),
            "doy_cos": np.cos(2 * np.pi * doy / 365.25),
        },
        index=idx,
    )


def build_feature_frame(
    nwp_fields: pd.DataFrame,
    lead: np.ndarray,
    last_actual: dict[str, np.ndarray],
    occupancy: np.ndarray,
) -> pd.DataFrame:
    """Features available at issue time, for targets at issue time + lead."""
    idx = nwp_fields.index
    feats = _calendar_features(idx)
    feats["lead_h"] = lead
    feats["occupancy"] = occupancy
    for col in ["temp_c", "wind_ms", "ghi_wm2", "snowfall_cm", "rh_pct", "pressure_hpa"]:
        feats[f"nwp_{col}"] = nwp_fields[col].to_numpy()
    feats["nwp_wind_dir_sin"] = np.sin(np.deg2rad(nwp_fields["wind_dir_deg"].to_numpy()))
    feats["nwp_wind_dir_cos"] = np.cos(np.deg2rad(nwp_fields["wind_dir_deg"].to_numpy()))
    feats["nwp_hdh"] = np.clip(20.0 - nwp_fields["temp_c"].to_numpy(), 0.0, None)
    feats["nwp_wind_cube"] = nwp_fields["wind_ms"].to_numpy() ** 3
    for name, values in last_actual.items():
        feats[f"last_{name}"] = values
    return feats


@dataclass
class QuantileForecaster:
    """One LightGBM model per (target, quantile), sharing a direct-multi-horizon design."""

    cfg: Config
    models: dict = field(default_factory=dict)
    fallback_spread: dict = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=list)
    train_report: dict = field(default_factory=dict)

    # ---------------- training ----------------
    def _training_matrix(self, twin: pd.DataFrame, nwp: NWP) -> tuple[pd.DataFrame, pd.DataFrame]:
        rows_x, rows_y = [], []
        idx = twin.index
        for lead in TRAIN_LEADS:
            target_idx = idx[lead + 24 :]  # need 24 h of history before the issue time
            issue_idx = target_idx - pd.Timedelta(hours=lead)
            fields = twin.loc[target_idx, list(NWP_ERROR_48H)].copy()
            # degrade the fields exactly as the online NWP would at this lead
            rng = np.random.default_rng(int(self.cfg["simulation.seed"]) + lead)
            for col in NWP_ERROR_48H:
                sigma = float(twin[col].std()) * NWP_ERROR_48H[col] * np.sqrt(lead / 48.0)
                fields[col] = fields[col].to_numpy() + rng.standard_normal(len(fields)) * sigma
            fields["ghi_wm2"] = fields["ghi_wm2"].clip(lower=0)
            fields["wind_ms"] = fields["wind_ms"].clip(lower=0)

            last_actual = {}
            for tgt in TARGETS:
                series = twin[tgt]
                last_actual[tgt] = series.loc[issue_idx].to_numpy()
                last_actual[f"{tgt}_24h"] = (
                    series.rolling(24, min_periods=1).mean().loc[issue_idx].to_numpy()
                )
            feats = build_feature_frame(
                fields,
                np.full(len(fields), float(lead)),
                last_actual,
                twin.loc[target_idx, "occupancy"].to_numpy(),
            )
            rows_x.append(feats)
            rows_y.append(twin.loc[target_idx, TARGETS])
        return pd.concat(rows_x), pd.concat(rows_y)

    def fit(self, twin_train: pd.DataFrame, nwp: NWP) -> "QuantileForecaster":
        X, Y = self._training_matrix(twin_train, nwp)
        self.feature_names = list(X.columns)
        for tgt in TARGETS:
            self.fallback_spread[tgt] = float(Y[tgt].std())
        if not HAS_LGB:
            warnings.warn("lightgbm unavailable; using statistical fallback forecaster")
            return self
        for tgt in TARGETS:
            for qname, q in QUANTILES.items():
                model = lgb.LGBMRegressor(
                    objective="quantile",
                    alpha=q,
                    n_estimators=300,
                    learning_rate=0.06,
                    num_leaves=48,
                    min_child_samples=40,
                    subsample=0.85,
                    subsample_freq=1,
                    colsample_bytree=0.85,
                    verbose=-1,
                    n_jobs=4,
                    random_state=int(self.cfg["simulation.seed"]),
                )
                model.fit(X, Y[tgt])
                self.models[(tgt, qname)] = model
        self.train_report = {"n_rows": int(len(X)), "n_features": len(self.feature_names)}
        return self

    def save(self, directory: Path, metadata: dict | None = None) -> None:
        """Persist native model text and JSON; loading never executes pickled code."""
        directory.mkdir(parents=True, exist_ok=True)
        for (target, q), model in self.models.items():
            model.booster_.save_model(str(directory / f"{target}_{q}.txt"))
        manifest = {"schema": 1, "feature_names": self.feature_names,
                    "fallback_spread": self.fallback_spread,
                    "train_report": self.train_report, "metadata": metadata or {},
                    "models": [f"{t}_{q}" for t, q in self.models]}
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, cfg: Config, directory: Path) -> "QuantileForecaster":
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["schema"] != 1:
            raise ValueError("Unsupported forecast artifact schema")
        fc = cls(cfg, feature_names=manifest["feature_names"],
                 fallback_spread=manifest["fallback_spread"], train_report=manifest["train_report"])
        if not HAS_LGB or len(manifest["models"]) != len(TARGETS) * len(QUANTILES):
            raise ValueError("Complete LightGBM artifacts and runtime are required")
        for tgt in TARGETS:
            for q in QUANTILES:
                fc.models[(tgt, q)] = lgb.Booster(model_file=str(directory / f"{tgt}_{q}.txt"))
        return fc

    # ---------------- inference ----------------
    def predict(
        self, nwp_fields: pd.DataFrame, history: pd.DataFrame, occupancy: np.ndarray
    ) -> pd.DataFrame:
        """Return p10/p50/p90 for every target over the forecast window."""
        if len(nwp_fields) == 0:
            return pd.DataFrame()
        last_actual = {}
        for tgt in TARGETS:
            series = history[tgt]
            last_actual[tgt] = np.full(len(nwp_fields), float(series.iloc[-1]))
            last_actual[f"{tgt}_24h"] = np.full(len(nwp_fields), float(series.tail(24).mean()))
        X = build_feature_frame(
            nwp_fields, nwp_fields["lead_h"].to_numpy(), last_actual, occupancy
        )
        X = X[self.feature_names] if self.feature_names else X

        out = pd.DataFrame(index=nwp_fields.index)
        for tgt in TARGETS:
            if HAS_LGB and (tgt, "p50") in self.models:
                for qname in QUANTILES:
                    # Tiny online batches are faster without an OpenMP thread team.
                    out[f"{tgt}_{qname}"] = self.models[(tgt, qname)].predict(
                        X, num_threads=int(self.cfg.get("forecast.inference_threads", 1)))
            else:  # persistence + climatological spread
                base = last_actual[f"{tgt}_24h"]
                spread = self.fallback_spread.get(tgt, 1.0) * np.sqrt(
                    nwp_fields["lead_h"].to_numpy() / 24.0
                )
                out[f"{tgt}_p50"] = base
                out[f"{tgt}_p10"] = base - 0.8 * spread
                out[f"{tgt}_p90"] = base + 0.8 * spread
            lo = 0.0 if tgt != "load_kw" else 0.0
            for qname in QUANTILES:
                out[f"{tgt}_{qname}"] = out[f"{tgt}_{qname}"].clip(lower=lo)
        # enforce monotone quantiles
        for tgt in TARGETS:
            cols = [f"{tgt}_p10", f"{tgt}_p50", f"{tgt}_p90"]
            out[cols] = np.sort(out[cols].to_numpy(), axis=1)
        return out


def pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, q: float) -> float:
    diff = y_true - y_pred
    return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))


def evaluate_forecaster(
    fc: QuantileForecaster, twin: pd.DataFrame, nwp: NWP, n_issues: int = 120, horizon_h: int = 48
) -> dict:
    """Walk-forward skill on unseen data: MAE, MAPE and pinball loss per target."""
    idx = twin.index
    issues = idx[48 : len(idx) - horizon_h : max(1, (len(idx) - horizon_h - 48) // n_issues)]
    acc: dict[str, list] = {}
    for issue in issues:
        fields = nwp.forecast(issue, horizon_h)
        if len(fields) < horizon_h:
            continue
        hist = twin.loc[:issue].tail(48)
        pred = fc.predict(fields, hist, twin.loc[fields.index, "occupancy"].to_numpy())
        truth = twin.loc[fields.index]
        for tgt in TARGETS:
            acc.setdefault(tgt, []).append(
                {
                    "mae": float(np.mean(np.abs(truth[tgt] - pred[f"{tgt}_p50"]))),
                    "pinball10": pinball_loss(truth[tgt].to_numpy(), pred[f"{tgt}_p10"].to_numpy(), 0.1),
                    "pinball50": pinball_loss(truth[tgt].to_numpy(), pred[f"{tgt}_p50"].to_numpy(), 0.5),
                    "pinball90": pinball_loss(truth[tgt].to_numpy(), pred[f"{tgt}_p90"].to_numpy(), 0.9),
                    "cover90": float(np.mean(truth[tgt].to_numpy() <= pred[f"{tgt}_p90"].to_numpy())),
                    "cover10": float(np.mean(truth[tgt].to_numpy() >= pred[f"{tgt}_p10"].to_numpy())),
                    "mean": float(truth[tgt].mean()),
                }
            )
    report = {}
    for tgt, rows in acc.items():
        frame = pd.DataFrame(rows)
        report[tgt] = {
            "mae": float(frame["mae"].mean()),
            "mape_pct": float(100 * frame["mae"].mean() / max(frame["mean"].mean(), 1e-6)),
            "pinball_mean": float(frame[["pinball10", "pinball50", "pinball90"]].mean().mean()),
            "p90_coverage": float(frame["cover90"].mean()),
            "p10_coverage": float(frame["cover10"].mean()),
            "n_issues": int(len(frame)),
        }
    return report
