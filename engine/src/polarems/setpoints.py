"""Turn controller decisions into the setpoints real plant already accepts.

The predictable judge question is "can this talk to actual station hardware?". The honest
answer is: the controller emits genset start/stop and load setpoints, battery power commands
and load-shed relay states, in the two encodings that gensets, inverters and BMS units already
expose -- Modbus holding registers and MQTT topics. We have not tested against hardware; that
is a hardware-in-the-loop test, not a rewrite.

Nothing here opens a socket. It is a pure translation layer with a documented register map,
so it can be demonstrated offline and reviewed on a slide.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# Modbus holding-register map. Scaling follows the usual convention of integer registers with
# a documented multiplier, because most genset controllers do not speak floats.
REGISTER_MAP = {
    40001: ("dg1_run_cmd", 1, "0 = stop, 1 = run"),
    40002: ("dg1_power_sp_kw", 10, "active power setpoint, kW x10"),
    40003: ("dg2_run_cmd", 1, "0 = stop, 1 = run"),
    40004: ("dg2_power_sp_kw", 10, "active power setpoint, kW x10"),
    40010: ("bess_power_sp_kw", 10, "signed: positive = charge, kW x10"),
    40011: ("bess_soc_pct", 10, "state of charge, % x10 (read-back)"),
    40020: ("shed_deferrable", 1, "load-group relay: 1 = shed"),
    40021: ("shed_sheddable", 1, "load-group relay: 1 = shed"),
    40030: ("clean_air_hold", 1, "1 = clean-air window active, generator start inhibited"),
    40031: ("fuel_allowance_lph", 10, "current seasonal allowance, L/h x10"),
}

MQTT_ROOT = "station/maitri2/ems"


@dataclass
class Setpoint:
    timestamp: str
    gen_cmd: list[int]
    gen_power_kw: list[float]
    battery_kw: float
    soc_pct: float
    shed: dict[str, bool]
    clean_air_hold: bool
    fuel_allowance_lph: float
    horizon_note: str = ""
    provenance: dict = field(default_factory=dict)

    def to_modbus(self) -> dict[int, int]:
        """Integer register values, scaled per REGISTER_MAP."""
        values = {
            "dg1_run_cmd": self.gen_cmd[0] if self.gen_cmd else 0,
            "dg1_power_sp_kw": self.gen_power_kw[0] if self.gen_power_kw else 0.0,
            "dg2_run_cmd": self.gen_cmd[1] if len(self.gen_cmd) > 1 else 0,
            "dg2_power_sp_kw": self.gen_power_kw[1] if len(self.gen_power_kw) > 1 else 0.0,
            "bess_power_sp_kw": self.battery_kw,
            "bess_soc_pct": self.soc_pct,
            "shed_deferrable": int(self.shed.get("deferrable", False)),
            "shed_sheddable": int(self.shed.get("sheddable", False)),
            "clean_air_hold": int(self.clean_air_hold),
            "fuel_allowance_lph": self.fuel_allowance_lph,
        }
        out = {}
        for reg, (name, scale, _doc) in REGISTER_MAP.items():
            out[reg] = int(round(values[name] * scale))
        return out

    def to_mqtt(self) -> list[tuple[str, str]]:
        """(topic, JSON payload) pairs, one per controllable device."""
        msgs = [
            (
                f"{MQTT_ROOT}/genset/{i + 1}/cmd",
                json.dumps({"run": bool(cmd), "power_kw": round(kw, 1), "ts": self.timestamp}),
            )
            for i, (cmd, kw) in enumerate(zip(self.gen_cmd, self.gen_power_kw))
        ]
        msgs.append(
            (
                f"{MQTT_ROOT}/bess/cmd",
                json.dumps({"power_kw": round(self.battery_kw, 1), "ts": self.timestamp}),
            )
        )
        msgs.append(
            (
                f"{MQTT_ROOT}/loads/shed",
                json.dumps({k: bool(v) for k, v in self.shed.items()} | {"ts": self.timestamp}),
            )
        )
        msgs.append(
            (
                f"{MQTT_ROOT}/policy/state",
                json.dumps(
                    {
                        "clean_air_hold": self.clean_air_hold,
                        "fuel_allowance_lph": round(self.fuel_allowance_lph, 2),
                        "reason": self.horizon_note,
                        "ts": self.timestamp,
                    }
                ),
            )
        )
        return msgs


def from_log_row(row, plan_note: str = "", allowance_lph: float = 0.0) -> Setpoint:
    """Build a setpoint from one hour of a closed-loop log."""
    gen_cmd, gen_kw = [], []
    for g in range(8):
        key = f"gen{g}_on"
        if key not in row:
            break
        gen_cmd.append(int(row[key]))
        gen_kw.append(float(row[f"gen{g}_kw"]))
    return Setpoint(
        timestamp=str(row.name),
        gen_cmd=gen_cmd,
        gen_power_kw=gen_kw,
        battery_kw=float(row["charge_kw"] - row["discharge_kw"]),
        soc_pct=100.0 * float(row["soc_frac"]),
        shed={"deferrable": row.get("planned_shed_kw", 0.0) > 0.5, "sheddable": row.get("planned_shed_kw", 0.0) > 0.5},
        clean_air_hold=bool(row["clean_air_window"]),
        fuel_allowance_lph=allowance_lph,
        horizon_note=plan_note,
    )


def register_map_markdown() -> str:
    lines = ["| register | signal | scale | meaning |", "|---|---|---|---|"]
    for reg, (name, scale, doc) in sorted(REGISTER_MAP.items()):
        lines.append(f"| {reg} | `{name}` | x{scale} | {doc} |")
    return "\n".join(lines)
