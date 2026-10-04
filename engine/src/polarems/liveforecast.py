"""Read-only startup forecaster for the connected demo; no per-request fitting."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .forecast import NWP, NWP_ERROR_48H, QuantileForecaster, QUANTILES, TARGETS
from .twin import occupancy
from .weather import _cache_path


class ForecastService:
    def __init__(self, cfg, root: Path):
        self.cfg = cfg
        self.weather = None
        self.nwp = None
        self.mode = "statistical-persistence"
        self.reason = None
        self.metadata = {}
        directory = root / cfg["forecast.artifact_directory"]
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            if manifest["metadata"]["station_config"] != {k: v for k, v in cfg.values.items()
                                                         if k not in ("reserve", "dispatch", "forecast")}:
                raise ValueError("Station configuration changed; regenerate forecast artifacts")
            self.fc = QuantileForecaster.load(cfg, directory)
            self.mode = "lightgbm-quantile"
            self.metadata = manifest["metadata"]
        except Exception as exc:
            # Native LightGBM also raises its own error type for missing/corrupt
            # model files. Keep the server usable with a labeled fallback.
            self.reason = str(exc)
            self.fc = QuantileForecaster(cfg)
        try:
            path = _cache_path(cfg["station.latitude"], cfg["station.longitude"], cfg["forecast.demo_weather_year"])
            self.weather = pd.read_parquet(path)
            self.nwp = NWP(self.weather, seed=int(cfg["simulation.seed"]) + 7)
        except (OSError, ValueError) as exc:
            self.reason = f"{self.reason or ''}; weather cache unavailable: {exc}"

    def predict(self, options, hours: int):
        start = pd.Timestamp(options.start_time)
        if start.tzinfo is not None:
            start = start.tz_convert("UTC").tz_localize(None)
        if start != start.floor("h"):
            raise ValueError("forecast.start_time must be an hourly timestamp")
        idx = pd.date_range(start, periods=hours, freq="h")
        history = pd.DataFrame([h.model_dump() for h in options.history])
        history.index = pd.to_datetime(history.pop("time"), utc=True).dt.tz_localize(None)
        if not history.index.is_monotonic_increasing or history.index.has_duplicates or (history.index >= start).any():
            raise ValueError("Forecast history must be ordered, unique and strictly before start_time")
        if history.index[-1] != start - pd.Timedelta(hours=1):
            raise ValueError("Forecast history must end one hour before start_time")
        pv_scale = options.solar_capacity_kw / float(self.cfg["pv.kwp"])
        wind_scale = options.wind_capacity_kw / (float(self.cfg["wind.rated_kw"]) * int(self.cfg["wind.n_turbines"]))
        history["pv_kw"] /= pv_scale if pv_scale else 1.0
        history["wind_kw"] /= wind_scale if wind_scale else 1.0
        has_weather = self.nwp is not None and idx.isin(self.weather.index).all()
        if has_weather:
            fields = self.nwp.forecast(start - pd.Timedelta(hours=1), hours)
        else:
            # Calendar + persistence needs no future telemetry. Dummy weather fields
            # only satisfy the shared statistical forecaster feature interface.
            fields = pd.DataFrame({c: np.zeros(hours) for c in NWP_ERROR_48H}, index=idx)
            fields["lead_h"] = np.arange(1, hours + 1)
        fc = self.fc if has_weather else QuantileForecaster(self.cfg)
        if not fc.models:
            fc = QuantileForecaster(self.cfg)
            # Explicit configurable climatological assumptions, not claimed calibration.
            for tgt, fraction, capacity in [
                ("load_kw", self.cfg["forecast.fallback_spread_load_fraction"], history.load_kw.mean()),
                ("pv_kw", self.cfg["forecast.fallback_spread_pv_fraction"], self.cfg["pv.kwp"]),
                ("wind_kw", self.cfg["forecast.fallback_spread_wind_fraction"], self.cfg["wind.rated_kw"] * self.cfg["wind.n_turbines"]),
            ]:
                fc.fallback_spread[tgt] = float(fraction * capacity)
        pred = fc.predict(fields, history, occupancy(idx, self.cfg).to_numpy())
        for q in QUANTILES:
            pred[f"pv_kw_{q}"] *= pv_scale
            pred[f"wind_kw_{q}"] *= wind_scale
        metadata = {"method": self.mode if has_weather else "statistical-persistence",
                    "fallback_reason": self.reason if has_weather else "No cached weather for requested timestamps",
                    "weather_source": "synthetic lead-degraded ERA5 NWP" if has_weather else "unavailable",
                    "operational_telemetry": False, "training_years": self.metadata.get("training_years", []),
                    "history_hours": len(history), "first_hour": "measured state in optimizer; forecast retained for error reporting",
                    "issue_time": str(start - pd.Timedelta(hours=1)), "target_times": [str(t) for t in idx]}
        return pred, fields if has_weather else None, metadata
