"""Decoders and patch helpers for Quadro HID reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .crc import crc16_usb

VID = 0x0C70
PID = 0xF00D

INPUT_REPORT_ID = 0x01
OUTPUT_REPORT_ID = 0x02
SETTINGS_REPORT_ID = 0x03
NAMES_REPORT_ID = 0x08

INPUT_REPORT_LEN = 220
SETTINGS_REPORT_LEN = 961
SETTINGS_PAYLOAD_LEN = 958
SETTINGS_CRC_OFFSET = 959
NAMES_REPORT_LEN = 1013
NAMES_PAYLOAD_LEN = 1010
NAMES_CRC_OFFSET = 1011

COMMIT_OUTPUT_REPORT = bytes.fromhex("02 00 00 00 02 00 00 00 00 34 c6")

MISSING_TEMP_RAW = 32767


class ReportError(ValueError):
    """Raised when a HID report is malformed or fails validation."""


@dataclass(frozen=True)
class FanLive:
    output_percent: float
    voltage: float
    current_raw: int
    power_raw: int
    rpm: int
    torque_raw: int
    flags: int


@dataclass(frozen=True)
class ControllerLive:
    input_offset: int
    output_offset: int
    output_scale: int
    output_percent: float


@dataclass(frozen=True)
class LiveStatus:
    structure_id: int
    serial_raw: int
    hardware: int
    device_type: int
    bootloader: int
    firmware: int
    system_state: int
    feature_unlock: int
    device_time: int
    power_up_count: int
    runtime_total: int
    adc_raw: tuple[int, ...]
    temperatures: tuple[float | None, ...]
    software_sensors_raw: tuple[int, ...]
    software_sensor_units: tuple[int, ...]
    vcc12: float
    flow_raw: int
    fans: tuple[FanLive, ...]
    controllers: tuple[ControllerLive, ...]
    alarm_state: int
    alarm_state_last: int
    strip_power: int
    strip_power_scale: int
    strip_scale: int
    strip_state: int
    profile_id: int

    @property
    def serial(self) -> str:
        return serial_to_text(self.serial_raw)


@dataclass(frozen=True)
class SensorConfig:
    offset_c: float


@dataclass(frozen=True)
class FanConfig:
    flags: int
    min_power: float
    max_power: float
    fallback_power: float
    max_rpm: int

    @property
    def hold_min_power(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def start_boost(self) -> bool:
        return bool(self.flags & 0x02)


@dataclass(frozen=True)
class CurvePoint:
    temperature_c: float
    power_percent: float


@dataclass(frozen=True)
class ControllerConfig:
    mode: int
    fixed_power: float
    source: int
    pid_setpoint_c: float
    pid_p: int
    pid_i: int
    pid_d: int
    pid_d_tn: int
    pid_hysteresis_c: float
    pid_flags: int
    curve_start_c: float
    curve_points: tuple[CurvePoint, ...]


@dataclass(frozen=True)
class LedControllerConfig:
    strip: int
    led_start: int
    count: int
    mode: int
    flags: int
    src_raw: bytes
    binding1_raw: bytes
    binding2_raw: bytes
    values: tuple[int, ...]
    hsv_raw: bytes


@dataclass(frozen=True)
class Settings:
    structure_id: int
    i2c_address: int
    device_flags: int
    flow_calibration: int
    flow_factor: int
    sensors: tuple[SensorConfig, ...]
    fans: tuple[FanConfig, ...]
    controllers: tuple[ControllerConfig, ...]
    strip_brightness: int
    strip_flags: int
    led_controllers: tuple[LedControllerConfig, ...]
    profile_id: int
    dummy: int


def serial_to_text(serial_raw: int) -> str:
    upper = (serial_raw >> 16) & 0xFFFF
    lower = serial_raw & 0xFFFF
    return f"{upper:05d}-{lower:05d}"


def s16(data: bytes | bytearray, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big", signed=True)


def u16(data: bytes | bytearray, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def u32(data: bytes | bytearray, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def put_s16(data: bytearray, offset: int, value: int) -> None:
    data[offset : offset + 2] = int(value).to_bytes(2, "big", signed=True)


def put_u16(data: bytearray, offset: int, value: int) -> None:
    data[offset : offset + 2] = int(value).to_bytes(2, "big")


def scaled(raw: int) -> float:
    return raw / 100.0


def raw_percent(value: float) -> int:
    return _scaled_int(value, "percent")


def raw_temp(value: float) -> int:
    return _scaled_int(value, "temperature")


def _scaled_int(value: float, name: str) -> int:
    raw = round(float(value) * 100)
    if raw < -32768 or raw > 32767:
        raise ValueError(f"{name} value is outside the signed 16-bit report range")
    return raw


def validate_report(report: bytes | bytearray, report_id: int, length: int, payload_len: int) -> None:
    if len(report) != length:
        raise ReportError(f"report 0x{report_id:02x} has length {len(report)}, expected {length}")
    if report[0] != report_id:
        raise ReportError(f"report id is 0x{report[0]:02x}, expected 0x{report_id:02x}")
    payload = report[1 : 1 + payload_len]
    expected = u16(report, 1 + payload_len)
    actual = crc16_usb(payload)
    if actual != expected:
        raise ReportError(
            f"report 0x{report_id:02x} CRC mismatch: got 0x{expected:04x}, computed 0x{actual:04x}"
        )


def validate_settings_report(report: bytes | bytearray) -> None:
    validate_report(report, SETTINGS_REPORT_ID, SETTINGS_REPORT_LEN, SETTINGS_PAYLOAD_LEN)


def validate_names_report(report: bytes | bytearray) -> None:
    validate_report(report, NAMES_REPORT_ID, NAMES_REPORT_LEN, NAMES_PAYLOAD_LEN)


def update_crc(report: bytearray, payload_len: int) -> int:
    crc = crc16_usb(report[1 : 1 + payload_len])
    put_u16(report, 1 + payload_len, crc)
    return crc


def update_settings_crc(report: bytearray) -> int:
    return update_crc(report, SETTINGS_PAYLOAD_LEN)


def update_names_crc(report: bytearray) -> int:
    return update_crc(report, NAMES_PAYLOAD_LEN)


def parse_live_status(report: bytes | bytearray) -> LiveStatus:
    if len(report) < INPUT_REPORT_LEN:
        raise ReportError(f"input report has length {len(report)}, expected at least {INPUT_REPORT_LEN}")
    report = report[:INPUT_REPORT_LEN]
    if report[0] != INPUT_REPORT_ID:
        raise ReportError(f"input report id is 0x{report[0]:02x}, expected 0x{INPUT_REPORT_ID:02x}")

    temps = []
    for offset in range(52, 60, 2):
        raw = s16(report, offset)
        temps.append(None if raw == MISSING_TEMP_RAW else scaled(raw))

    fans = []
    for idx in range(4):
        base = 112 + idx * 13
        fans.append(
            FanLive(
                output_percent=scaled(s16(report, base)),
                voltage=scaled(s16(report, base + 2)),
                current_raw=s16(report, base + 4),
                power_raw=s16(report, base + 6),
                rpm=s16(report, base + 8),
                torque_raw=s16(report, base + 10),
                flags=report[base + 12],
            )
        )

    controllers = []
    for idx in range(5):
        base = 164 + idx * 8
        controllers.append(
            ControllerLive(
                input_offset=s16(report, base),
                output_offset=s16(report, base + 2),
                output_scale=s16(report, base + 4),
                output_percent=scaled(s16(report, base + 6)),
            )
        )

    return LiveStatus(
        structure_id=u16(report, 1),
        serial_raw=u32(report, 3),
        hardware=u16(report, 7),
        device_type=u16(report, 9),
        bootloader=u16(report, 11),
        firmware=u16(report, 13),
        system_state=u32(report, 15),
        feature_unlock=report[19],
        device_time=u32(report, 20),
        power_up_count=u32(report, 24),
        runtime_total=u32(report, 28),
        adc_raw=tuple(u16(report, offset) for offset in range(32, 52, 2)),
        temperatures=tuple(temps),
        software_sensors_raw=tuple(s16(report, offset) for offset in range(60, 92, 2)),
        software_sensor_units=tuple(report[92:108]),
        vcc12=scaled(s16(report, 108)),
        flow_raw=s16(report, 110),
        fans=tuple(fans),
        controllers=tuple(controllers),
        alarm_state=u32(report, 204),
        alarm_state_last=u32(report, 208),
        strip_power=s16(report, 212),
        strip_power_scale=s16(report, 214),
        strip_scale=report[216],
        strip_state=u16(report, 217),
        profile_id=report[219],
    )


def parse_settings(report: bytes | bytearray) -> Settings:
    validate_settings_report(report)
    payload = report[1 : 1 + SETTINGS_PAYLOAD_LEN]

    sensors = tuple(SensorConfig(offset_c=scaled(s16(payload, sensor_base(i)))) for i in range(4))
    fans = []
    for idx in range(4):
        base = fan_config_base(idx)
        fans.append(
            FanConfig(
                flags=payload[base],
                min_power=scaled(s16(payload, base + 1)),
                max_power=scaled(s16(payload, base + 3)),
                fallback_power=scaled(s16(payload, base + 5)),
                max_rpm=s16(payload, base + 7),
            )
        )

    controllers = []
    for idx in range(4):
        cbase = controller_base(idx)
        pbase = cbase + 5
        curve_base_offset = cbase + 19
        points = []
        for point in range(16):
            points.append(
                CurvePoint(
                    temperature_c=scaled(s16(payload, curve_base_offset + 2 + point * 2)),
                    power_percent=scaled(s16(payload, curve_base_offset + 34 + point * 2)),
                )
            )
        controllers.append(
            ControllerConfig(
                mode=payload[cbase],
                fixed_power=scaled(s16(payload, cbase + 1)),
                source=s16(payload, cbase + 3),
                pid_setpoint_c=scaled(s16(payload, pbase)),
                pid_p=s16(payload, pbase + 2),
                pid_i=s16(payload, pbase + 4),
                pid_d=s16(payload, pbase + 6),
                pid_d_tn=s16(payload, pbase + 8),
                pid_hysteresis_c=scaled(s16(payload, pbase + 10)),
                pid_flags=u16(payload, pbase + 12),
                curve_start_c=scaled(s16(payload, curve_base_offset)),
                curve_points=tuple(points),
            )
        )

    led_controllers = []
    for idx in range(8):
        base = led_controller_base(idx)
        led_controllers.append(
            LedControllerConfig(
                strip=payload[base],
                led_start=payload[base + 1],
                count=payload[base + 2],
                mode=payload[base + 3],
                flags=u16(payload, base + 4),
                src_raw=bytes(payload[base + 6 : base + 10]),
                binding1_raw=bytes(payload[base + 10 : base + 16]),
                binding2_raw=bytes(payload[base + 16 : base + 22]),
                values=tuple(s16(payload, offset) for offset in range(base + 22, base + 46, 2)),
                hsv_raw=bytes(payload[base + 46 : base + 70]),
            )
        )

    return Settings(
        structure_id=u16(payload, 0),
        i2c_address=payload[2],
        device_flags=u16(payload, 3),
        flow_calibration=u16(payload, 5),
        flow_factor=s16(payload, 7),
        sensors=sensors,
        fans=tuple(fans),
        controllers=tuple(controllers),
        strip_brightness=payload[393],
        strip_flags=u16(payload, 394),
        led_controllers=tuple(led_controllers),
        profile_id=payload[956],
        dummy=payload[957],
    )


def parse_names(report: bytes | bytearray) -> list[str]:
    validate_names_report(report)
    payload = report[1 : 1 + NAMES_PAYLOAD_LEN]
    names = []
    for slot in range(42):
        base = name_base(slot)
        raw = payload[base : base + 24].split(b"\0", 1)[0]
        names.append(raw.decode("ascii", errors="replace"))
    return names


def settings_payload_offset(payload_offset: int) -> int:
    return payload_offset + 1


def names_payload_offset(payload_offset: int) -> int:
    return payload_offset + 1


def sensor_base(index0: int) -> int:
    return 9 + index0 * 2


def fan_config_base(index0: int) -> int:
    return 17 + index0 * 9


def controller_base(index0: int) -> int:
    return 53 + index0 * 85


def curve_base(index0: int) -> int:
    return controller_base(index0) + 19


def curve_input(index0: int, point0: int) -> int:
    return curve_base(index0) + 2 + point0 * 2


def curve_output(index0: int, point0: int) -> int:
    return curve_base(index0) + 34 + point0 * 2


def led_controller_base(index0: int) -> int:
    return 396 + index0 * 70


def name_base(slot0: int) -> int:
    return 2 + slot0 * 24


def patch_fixed_power(report: bytearray, fan0: int, percent: float) -> list[int]:
    _require_index(fan0, 4, "fan")
    _require_percent(percent)
    offsets = []
    base = settings_payload_offset(controller_base(fan0))
    report[base] = 0
    put_s16(report, base + 1, raw_percent(percent))
    offsets.extend([base, base + 1, base + 2])
    return offsets


def patch_target_temperature(report: bytearray, fan0: int, source: int, setpoint_c: float) -> list[int]:
    _require_index(fan0, 4, "fan")
    _require_source(source)
    base = settings_payload_offset(controller_base(fan0))
    report[base] = 1
    put_s16(report, base + 3, source)
    put_s16(report, base + 5, raw_temp(setpoint_c))
    return [base, base + 3, base + 4, base + 5, base + 6]


def patch_fan_flag(report: bytearray, fan0: int, mask: int, enabled: bool) -> list[int]:
    _require_index(fan0, 4, "fan")
    offset = settings_payload_offset(fan_config_base(fan0))
    if enabled:
        report[offset] |= mask
    else:
        report[offset] &= ~mask & 0xFF
    return [offset]


def patch_fan_power_field(report: bytearray, fan0: int, field: str, percent: float) -> list[int]:
    _require_index(fan0, 4, "fan")
    _require_percent(percent)
    field_offsets = {"min": 1, "max": 3, "fallback": 5}
    offset = settings_payload_offset(fan_config_base(fan0) + field_offsets[field])
    put_s16(report, offset, raw_percent(percent))
    return [offset, offset + 1]


def patch_sensor_offset(report: bytearray, sensor0: int, offset_c: float) -> list[int]:
    _require_index(sensor0, 4, "sensor")
    offset = settings_payload_offset(sensor_base(sensor0))
    put_s16(report, offset, raw_temp(offset_c))
    return [offset, offset + 1]


def patch_strip_brightness(report: bytearray, brightness: int) -> list[int]:
    if brightness < 0 or brightness > 255:
        raise ValueError("strip brightness must be in range 0..255")
    offset = settings_payload_offset(393)
    report[offset] = brightness
    return [offset]


def patch_strip_enabled(report: bytearray, enabled: bool) -> list[int]:
    offset = settings_payload_offset(394)
    flags = u16(report, offset)
    if enabled:
        flags &= ~0x0002
    else:
        flags |= 0x0002
    put_u16(report, offset, flags)
    return [offset, offset + 1]


def patch_curve(
    report: bytearray,
    fan0: int,
    source: int | None,
    points: Iterable[tuple[float, float]],
    start_value: float | None = None,
) -> list[int]:
    _require_index(fan0, 4, "fan")
    points = list(points)
    if len(points) != 16:
        raise ValueError("curve mode requires exactly 16 points")
    for temp_c, percent in points:
        _require_percent(percent)
        raw_temp(temp_c)
    offsets = []
    cbase = settings_payload_offset(controller_base(fan0))
    report[cbase] = 2
    offsets.append(cbase)
    if source is not None:
        put_s16(report, cbase + 3, source)
        offsets.extend([cbase + 3, cbase + 4])
    if start_value is not None:
        offset = settings_payload_offset(curve_base(fan0))
        put_s16(report, offset, raw_temp(start_value))
        offsets.extend([offset, offset + 1])
    for idx, (temp_c, percent) in enumerate(points):
        input_offset = settings_payload_offset(curve_input(fan0, idx))
        output_offset = settings_payload_offset(curve_output(fan0, idx))
        put_s16(report, input_offset, raw_temp(temp_c))
        put_s16(report, output_offset, raw_percent(percent))
        offsets.extend([input_offset, input_offset + 1, output_offset, output_offset + 1])
    return offsets


def patch_name(report: bytearray, slot0: int, name: str) -> list[int]:
    _require_index(slot0, 42, "name slot")
    encoded = name.encode("ascii")
    if len(encoded) > 23:
        raise ValueError("names must be ASCII and at most 23 bytes")
    offset = names_payload_offset(name_base(slot0))
    report[offset : offset + 24] = encoded + b"\0" * (24 - len(encoded))
    return list(range(offset, offset + 24))


def _require_index(index0: int, limit: int, name: str) -> None:
    if index0 < 0 or index0 >= limit:
        raise ValueError(f"{name} index must be in range 1..{limit}")


def _require_source(source: int) -> None:
    if source < 0 or source > 3:
        raise ValueError("target-temperature source must be temp1..temp4")


def _require_percent(value: float) -> None:
    if value < 0 or value > 100:
        raise ValueError("percent values must be in range 0..100")
