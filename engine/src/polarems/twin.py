"""Physics digital twin of the station.

Nothing here is a drawn curve. Loads are built bottom-up from headcount and
outdoor temperature; generation is built from documented device physics. Two
signals that fall out of the model rather than being fitted are used as
validation in the pitch:

  1. demand peaks in winter while occupancy is at its minimum (heat dominates);
  2. wind and solar are seasonally complementary at this latitude.

Both are documented in de Witt, Chung & Lee (2024), Sustainability 16(1), 426.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pvlib

from .availability import apply_availability
from .cleanair import clean_air_flags
from .config import Config

R_AIR = 287.05  # J/(kg K)


# --------------------------------------------------------------------------
# occupancy
# --------------------------------------------------------------------------
def occupancy(index: pd.DatetimeIndex, cfg: Config) -> pd.Series:
    """Headcount, ramped over the ship windows rather than stepped."""
    summer = int(cfg["population.summer_headcount"])
    winter = int(cfg["population.winter_headcount"])
    start_m, start_d = (int(x) for x in str(cfg["population.summer_start_mmdd"]).split("-"))
    end_m, end_d = (int(x) for x in str(cfg["population.summer_end_mmdd"]).split("-"))

    doy = index.dayofyear.to_numpy()
    start_doy = pd.Timestamp(2001, start_m, start_d).dayofyear
    end_doy = pd.Timestamp(2001, end_m, end_d).dayofyear
    # austral summer wraps the new year
    is_summer = (doy >= start_doy) | (doy <= end_doy)

    head = np.where(is_summer, summer, winter).astype(float)
    # 10-day linear ramps at each transition, so thermal/water loads do not step
    ramp = pd.Series(head, index=index).rolling(24 * 10, min_periods=1, center=True).mean()
    return ramp


# --------------------------------------------------------------------------
# loads
# --------------------------------------------------------------------------
def _diurnal(index: pd.DatetimeIndex, peak_hours: tuple[int, int], base: float) -> np.ndarray:
    """Simple occupancy-activity shape: `base` at night, 1.0 across peak hours."""
    hour = index.hour.to_numpy()
    lo, hi = peak_hours
    active = (hour >= lo) & (hour < hi)
    shape = np.where(active, 1.0, base)
    # soften the edges so the MILP does not chase a square wave
    return pd.Series(shape, index=index).rolling(3, min_periods=1, center=True).mean().to_numpy()


def build_loads(weather: pd.DataFrame, cfg: Config, rng: np.random.Generator) -> pd.DataFrame:
    idx = weather.index
    occ = occupancy(idx, cfg)

    ua = float(cfg["thermal.envelope_UA"])
    tset = float(cfg["thermal.indoor_setpoint"])
    elec_frac = float(cfg["thermal.heat_electrified_frac"])
    whr = float(cfg["thermal.waste_heat_recovery_frac"])

    # heating: degree-hours against the envelope, minus recovered genset heat,
    # times the electrified fraction. This is what makes winter the peak.
    heat_thermal_kw = ua * np.clip(tset - weather["temp_c"].to_numpy(), 0.0, None)
    # a small internal-gain credit from people and equipment
    heat_thermal_kw = np.clip(heat_thermal_kw - 0.12 * occ.to_numpy(), 0.0, None)
    heating_kw = heat_thermal_kw * (1.0 - whr) * elec_frac

    # snow melting and water heating: scales with headcount, daytime weighted
    melt_day_kwh = float(cfg["thermal.snowmelt_kwh_per_person_day"]) * occ.to_numpy()
    melt_kw = melt_day_kwh / 24.0 * _diurnal(idx, (6, 22), base=0.35) * 1.35
    # colder snow costs more energy to melt
    melt_kw *= 1.0 + 0.010 * np.clip(-10.0 - weather["temp_c"].to_numpy(), 0.0, None)

    science = float(cfg["loads.science_base_kw"]) * np.ones(len(idx))
    life = float(cfg["loads.life_safety_kw"]) * np.ones(len(idx))

    dom = (
        float(cfg["loads.domestic_kw_per_person"])
        * occ.to_numpy()
        * _diurnal(idx, (7, 23), base=0.45)
    )
    dom *= rng.normal(1.0, 0.08, len(idx)).clip(0.7, 1.35)

    summer_active = (occ.to_numpy() > 0.6 * float(cfg["population.summer_headcount"])).astype(float)
    workshop = (
        float(cfg["loads.workshop_peak_kw"])
        * summer_active
        * _diurnal(idx, (8, 18), base=0.05)
        * rng.uniform(0.4, 1.0, len(idx))
    )

    loads = pd.DataFrame(
        {
            "occupancy": occ.to_numpy(),
            "load_critical_kw": science + life,
            "load_essential_kw": heating_kw + melt_kw,
            "load_deferrable_kw": workshop,
            "load_sheddable_kw": dom,
            "heating_kw": heating_kw,
            "snowmelt_kw": melt_kw,
        },
        index=idx,
    )
    loads["load_kw"] = (
        loads["load_critical_kw"]
        + loads["load_essential_kw"]
        + loads["load_deferrable_kw"]
        + loads["load_sheddable_kw"]
    )
    return loads


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------
def pv_potential(weather: pd.DataFrame, cfg: Config) -> pd.Series:
    """Plane-of-array PV with polar physics: steep tilt, snow albedo, cold gain."""
    lat = float(cfg["station.latitude"])
    lon = float(cfg["station.longitude"])
    alt = float(cfg["station.elevation"])
    idx = weather.index.tz_localize("UTC") if weather.index.tz is None else weather.index

    solpos = pvlib.solarposition.get_solarposition(idx, lat, lon, altitude=alt)
    ghi = weather["ghi_wm2"].to_numpy().clip(0.0)
    decomp = pvlib.irradiance.erbs(ghi, solpos["zenith"].to_numpy(), idx)
    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=float(cfg["pv.tilt"]),
        surface_azimuth=float(cfg["pv.azimuth"]),
        solar_zenith=solpos["zenith"].to_numpy(),
        solar_azimuth=solpos["azimuth"].to_numpy(),
        dni=np.nan_to_num(decomp["dni"]),
        ghi=ghi,
        dhi=np.nan_to_num(decomp["dhi"]),
        albedo=float(cfg["pv.albedo"]),
        model="isotropic",
    )
    poa_global = np.nan_to_num(poa["poa_global"]).clip(0.0)

    t_cell = pvlib.temperature.faiman(
        poa_global, weather["temp_c"].to_numpy(), weather["wind_ms"].to_numpy()
    )
    gain = 1.0 + float(cfg["pv.temp_coeff"]) * (t_cell - 25.0)
    kw = (
        float(cfg["pv.kwp"])
        * (poa_global / 1000.0)
        * gain
        * (1.0 - float(cfg["pv.system_losses"]))
        * (1.0 + float(cfg["pv.bifacial_gain"]))
    )
    return pd.Series(np.clip(kw, 0.0, None), index=weather.index, name="pv_potential_kw")


def wind_potential(weather: pd.DataFrame, cfg: Config) -> pd.Series:
    """Manufacturer power curve with the cold-air density correction applied."""
    alpha = float(cfg["wind.shear_alpha"])
    hub = float(cfg["wind.hub_height"])
    v = weather["wind_ms"].to_numpy() * (hub / 10.0) ** alpha

    rho = (weather["pressure_hpa"].to_numpy() * 100.0) / (
        R_AIR * (weather["temp_c"].to_numpy() + 273.15)
    )
    density_ratio = rho / 1.225  # about 1.2 at -37 C: the documented polar bonus

    cut_in = float(cfg["wind.cut_in"])
    rated_v = float(cfg["wind.rated_speed"])
    cut_out = float(cfg["wind.cut_out"])
    rated_kw = float(cfg["wind.rated_kw"]) * int(cfg["wind.n_turbines"])

    frac = np.zeros_like(v)
    ramp = (v >= cut_in) & (v < rated_v)
    frac[ramp] = (v[ramp] ** 3 - cut_in**3) / (rated_v**3 - cut_in**3)
    frac[(v >= rated_v) & (v < cut_out)] = 1.0
    kw = np.clip(rated_kw * frac * density_ratio, 0.0, rated_kw)
    return pd.Series(kw, index=weather.index, name="wind_potential_kw")


# --------------------------------------------------------------------------
# genset and battery physics used by every controller
# --------------------------------------------------------------------------
def genset_fuel_lph(gen: dict, power_kw: float | np.ndarray) -> float | np.ndarray:
    """Affine fuel curve: litres/h = a * P_rated + b * P_out (zero when off)."""
    rated = float(gen["rated_kw"])
    on = np.asarray(power_kw) > 1e-6
    return np.where(
        on,
        float(gen["fuel_a_lph_per_kw_rated"]) * rated
        + float(gen["fuel_b_lph_per_kw_out"]) * np.asarray(power_kw),
        0.0,
    )


def genset_best_efficiency_point(gen: dict) -> float:
    """Loading (kW) at which L/kWh is minimised, clipped to the allowed band.

    With an affine curve the specific consumption falls monotonically with load,
    so the efficiency point is the top of the band; the min-load limit is what
    makes running a big set on a small load expensive.
    """
    return float(gen["rated_kw"]) * 0.85


def gensets(cfg: Config) -> list[dict]:
    out = []
    for g in cfg["gensets"]:
        out.append(
            {
                "id": g["id"],
                "rated_kw": float(g["rated_kw"]),
                "min_load_frac": float(g["min_load_frac"]),
                "fuel_a_lph_per_kw_rated": float(g["fuel_a_lph_per_kw_rated"]),
                "fuel_b_lph_per_kw_out": float(g["fuel_b_lph_per_kw_out"]),
                "start_cost_l": float(g["start_cost_l"]),
                "min_up_h": int(g["min_up_h"]),
                "min_down_h": int(g["min_down_h"]),
            }
        )
    return out


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------
def build_twin(weather: pd.DataFrame, cfg: Config, seed: int | None = None) -> pd.DataFrame:
    """Weather in, full station state out: loads, potential and available generation."""
    rng = np.random.default_rng(seed if seed is not None else int(cfg["simulation.seed"]))
    df = weather.copy()
    loads = build_loads(weather, cfg, rng)
    df = df.join(loads)
    df["pv_potential_kw"] = pv_potential(weather, cfg).to_numpy()
    df["wind_potential_kw"] = wind_potential(weather, cfg).to_numpy()
    df = apply_availability(df, cfg)
    df["clean_air_window"] = clean_air_flags(df, cfg)
    df["net_load_kw"] = df["load_kw"] - df["pv_kw"] - df["wind_kw"]
    return df
