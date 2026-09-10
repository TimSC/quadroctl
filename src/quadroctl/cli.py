"""Command line interface for quadroctl."""

from __future__ import annotations

import argparse
import difflib
import sys
import time
from pathlib import Path

from .reports import (
    COMMIT_OUTPUT_REPORT,
    INPUT_REPORT_LEN,
    NAMES_REPORT_ID,
    NAMES_REPORT_LEN,
    SETTINGS_REPORT_ID,
    SETTINGS_REPORT_LEN,
    ReportError,
    parse_live_status,
    parse_names,
    parse_settings,
    patch_curve,
    patch_fan_flag,
    patch_fan_power_field,
    patch_fixed_power,
    patch_name,
    patch_sensor_offset,
    patch_strip_brightness,
    patch_strip_enabled,
    patch_target_temperature,
    update_names_crc,
    update_settings_crc,
    u16,
    validate_names_report,
    validate_settings_report,
)
from .transport import TransportError, list_devices, list_unavailable_hidraw_paths, open_quadro


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, TransportError, ReportError, ValueError, UnicodeEncodeError) as exc:
        print(f"quadroctl: error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quadroctl")
    parser.add_argument("--device", help="HID path, such as /dev/hidraw4")
    parser.add_argument("--backend", choices=("auto", "hidapi", "hidraw"), default="auto")
    parser.add_argument("--no-commit", action="store_true", help="skip output report 0x02 after writes")
    parser.add_argument("--dry-run", action="store_true", help="show intended changes without writing")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("."),
        help="directory for automatic backups before writes",
    )
    sub = parser.add_subparsers(required=True)

    list_parser = sub.add_parser("list", help="list matching Quadro HID devices")
    list_parser.set_defaults(func=cmd_list)

    status = sub.add_parser("status", help="print one live status report")
    status.add_argument("--timeout-ms", type=int, default=2000)
    status.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also dump every decoded live, settings, and name field",
    )
    status.set_defaults(func=cmd_status)

    dump_settings = sub.add_parser("dump-settings", help="read and decode settings report 0x03")
    dump_settings.add_argument("--raw", type=Path, help="write raw 961-byte report to this file")
    dump_settings.set_defaults(func=cmd_dump_settings)

    restore_settings = sub.add_parser("restore-settings", help="restore a raw settings report")
    restore_settings.add_argument("file", type=Path)
    restore_settings.set_defaults(func=cmd_restore_settings)

    dump_names = sub.add_parser("dump-names", help="read and decode names report 0x08")
    dump_names.add_argument("--raw", type=Path, help="write raw 1013-byte report to this file")
    dump_names.set_defaults(func=cmd_dump_names)

    fan = sub.add_parser("fan", help="edit fan settings")
    fan.add_argument("fan", metavar="FAN", type=parse_one_based_4)
    fan_sub = fan.add_subparsers(required=True)

    fan_fixed = fan_sub.add_parser("fixed", help="set fixed power mode")
    fan_fixed.add_argument("percent", type=float)
    fan_fixed.add_argument(
        "--keep-start-boost",
        action="store_true",
        help="preserve start boost; by default fixed mode clears it",
    )
    fan_fixed.set_defaults(func=cmd_fan_fixed)

    for command in ("target", "target-temp"):
        fan_target = fan_sub.add_parser(command, help="set target-temperature mode")
        fan_target.add_argument("--source", type=parse_target_source, required=True, help="temp1..temp4")
        fan_target.add_argument("setpoint_c", type=float, metavar="SETPOINT_C")
        fan_target.set_defaults(func=cmd_fan_target)

    fan_curve = fan_sub.add_parser("curve", help="set curve mode with 16 points")
    fan_curve.add_argument("--source", type=parse_source, required=True, help="temp1..temp4 or numeric source")
    fan_curve.add_argument("--start", type=float, help="curve start value in degrees C")
    fan_curve.add_argument(
        "--point",
        action="append",
        required=True,
        metavar="TEMP:PERCENT",
        help="curve point; provide exactly 16, for example --point 30:20",
    )
    fan_curve.set_defaults(func=cmd_fan_curve)

    fan_start_boost = fan_sub.add_parser("start-boost", help="turn start boost on or off")
    fan_start_boost.add_argument("state", type=parse_on_off)
    fan_start_boost.set_defaults(func=cmd_fan_start_boost)

    for command, field in (("min-power", "min"), ("max-power", "max"), ("fallback", "fallback")):
        fan_power = fan_sub.add_parser(command, help=f"set fan {command.replace('-', ' ')} percent")
        fan_power.add_argument("percent", type=float)
        fan_power.set_defaults(func=cmd_fan_power_field, field=field)

    sensor = sub.add_parser("sensor", help="edit temperature sensor settings")
    sensor.add_argument("sensor", metavar="SENSOR", type=parse_one_based_4)
    sensor_sub = sensor.add_subparsers(required=True)
    sensor_offset = sensor_sub.add_parser("offset", help="set temperature offset in degrees C")
    sensor_offset.add_argument("degrees_c", type=float)
    sensor_offset.set_defaults(func=cmd_sensor_offset)

    led = sub.add_parser("led", help="edit known RGBpx/LED strip settings")
    led_sub = led.add_subparsers(required=True)
    led_brightness = led_sub.add_parser("brightness", help="set strip brightness 0..255")
    led_brightness.add_argument("value", type=parse_u8)
    led_brightness.set_defaults(func=cmd_led_brightness)
    led_enable = led_sub.add_parser("enable", help="turn the strip on or off")
    led_enable.add_argument("state", type=parse_on_off)
    led_enable.set_defaults(func=cmd_led_enable)
    led_strip = led_sub.add_parser("strip", help="edit strip settings")
    led_strip_sub = led_strip.add_subparsers(required=True)
    led_strip_brightness = led_strip_sub.add_parser("brightness", help="set strip brightness 0..255")
    led_strip_brightness.add_argument("value", type=parse_u8)
    led_strip_brightness.set_defaults(func=cmd_led_brightness)
    led_strip_enable = led_strip_sub.add_parser("enable", help="turn the strip on or off")
    led_strip_enable.add_argument("state", type=parse_on_off)
    led_strip_enable.set_defaults(func=cmd_led_enable)

    name = sub.add_parser("name", help="rename a known Quadro slot")
    name.add_argument("slot", type=parse_name_slot)
    name.add_argument("name")
    name.set_defaults(func=cmd_name)
    return parser


def cmd_list(args: argparse.Namespace) -> int:
    devices = list_devices(args.backend)
    if not devices:
        print("No openable Quadro devices found.")
        unavailable = list_unavailable_hidraw_paths()
        if unavailable and args.backend in {"auto", "hidraw"}:
            print(f"Sysfs candidates without /dev nodes: {', '.join(unavailable)}")
        return 1
    for device in devices:
        parts = [device.path, f"backend={device.backend}"]
        if device.serial:
            parts.append(f"serial={device.serial}")
        if device.product:
            parts.append(f"product={device.product}")
        print("  ".join(parts))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with open_quadro(args.device, writable=False, backend=args.backend) as transport:
        status = parse_live_status(transport.read_input_report(INPUT_REPORT_LEN, args.timeout_ms))
        settings = names = names_version = None
        if args.verbose:
            settings_report = transport.get_feature_report(SETTINGS_REPORT_ID, SETTINGS_REPORT_LEN)
            names_report = transport.get_feature_report(NAMES_REPORT_ID, NAMES_REPORT_LEN)
            settings = parse_settings(settings_report)
            names = parse_names(names_report)
            names_version = u16(names_report, 1)
    print_status_summary(status)
    if args.verbose:
        print()
        print_verbose_status(status)
        print()
        print_settings(settings, verbose=True)
        print()
        print_names(names, verbose=True, version=names_version)
    return 0


def cmd_dump_settings(args: argparse.Namespace) -> int:
    with open_quadro(args.device, writable=False, backend=args.backend) as transport:
        report = transport.get_feature_report(SETTINGS_REPORT_ID, SETTINGS_REPORT_LEN)
    validate_settings_report(report)
    if args.raw:
        args.raw.write_bytes(report)
        print(f"Wrote {args.raw}")
    print_settings(parse_settings(report))
    return 0


def cmd_restore_settings(args: argparse.Namespace) -> int:
    report = args.file.read_bytes()
    validate_settings_report(report)
    if args.dry_run:
        print(f"Would restore {args.file} ({len(report)} bytes), CRC valid.")
        return 0
    with open_quadro(args.device, writable=True, backend=args.backend) as transport:
        before = transport.get_feature_report(SETTINGS_REPORT_ID, SETTINGS_REPORT_LEN)
        validate_settings_report(before)
        write_backup(args.backup_dir, "settings", before)
        transport.set_feature_report(report)
        commit(args, transport)
        after = transport.get_feature_report(SETTINGS_REPORT_ID, SETTINGS_REPORT_LEN)
    validate_settings_report(after)
    if after != report:
        raise ReportError("device readback did not match restored settings")
    print(f"Restored settings from {args.file}")
    return 0


def cmd_dump_names(args: argparse.Namespace) -> int:
    with open_quadro(args.device, writable=False, backend=args.backend) as transport:
        report = transport.get_feature_report(NAMES_REPORT_ID, NAMES_REPORT_LEN)
    validate_names_report(report)
    if args.raw:
        args.raw.write_bytes(report)
        print(f"Wrote {args.raw}")
    print_names(parse_names(report))
    return 0


def cmd_fan_fixed(args: argparse.Namespace) -> int:
    def mutate(report: bytearray) -> list[int]:
        offsets = patch_fixed_power(report, args.fan, args.percent)
        if not args.keep_start_boost:
            offsets += patch_fan_flag(report, args.fan, 0x02, False)
        return offsets

    return modify_settings(args, mutate, f"Set fan{args.fan + 1} fixed power to {args.percent:.2f}%")


def cmd_fan_curve(args: argparse.Namespace) -> int:
    points = [parse_curve_point(point) for point in args.point]

    def mutate(report: bytearray) -> list[int]:
        return patch_curve(report, args.fan, args.source, points, args.start)

    return modify_settings(args, mutate, f"Set fan{args.fan + 1} curve from temp{args.source + 1}")


def cmd_fan_target(args: argparse.Namespace) -> int:
    def mutate(report: bytearray) -> list[int]:
        return patch_target_temperature(report, args.fan, args.source, args.setpoint_c)

    return modify_settings(
        args,
        mutate,
        f"Set fan{args.fan + 1} target temperature to {args.setpoint_c:.2f} C from temp{args.source + 1}",
    )


def cmd_fan_start_boost(args: argparse.Namespace) -> int:
    def mutate(report: bytearray) -> list[int]:
        return patch_fan_flag(report, args.fan, 0x02, args.state)

    state = "on" if args.state else "off"
    return modify_settings(args, mutate, f"Set fan{args.fan + 1} start boost {state}")


def cmd_fan_power_field(args: argparse.Namespace) -> int:
    def mutate(report: bytearray) -> list[int]:
        return patch_fan_power_field(report, args.fan, args.field, args.percent)

    return modify_settings(args, mutate, f"Set fan{args.fan + 1} {args.field} power to {args.percent:.2f}%")


def cmd_sensor_offset(args: argparse.Namespace) -> int:
    def mutate(report: bytearray) -> list[int]:
        return patch_sensor_offset(report, args.sensor, args.degrees_c)

    return modify_settings(args, mutate, f"Set temp{args.sensor + 1} offset to {args.degrees_c:.2f} C")


def cmd_led_brightness(args: argparse.Namespace) -> int:
    def mutate(report: bytearray) -> list[int]:
        return patch_strip_brightness(report, args.value)

    return modify_settings(args, mutate, f"Set LED strip brightness to {args.value}")


def cmd_led_enable(args: argparse.Namespace) -> int:
    def mutate(report: bytearray) -> list[int]:
        return patch_strip_enabled(report, args.state)

    state = "on" if args.state else "off"
    return modify_settings(args, mutate, f"Turned LED strip {state}")


def cmd_name(args: argparse.Namespace) -> int:
    def mutate(report: bytearray) -> list[int]:
        return patch_name(report, args.slot, args.name)

    label = slot_label(args.slot)
    return modify_names(args, mutate, f"Renamed {label} to {args.name!r}")


def modify_settings(args: argparse.Namespace, mutate, summary: str) -> int:
    with open_quadro(args.device, writable=not args.dry_run, backend=args.backend) as transport:
        before = transport.get_feature_report(SETTINGS_REPORT_ID, SETTINGS_REPORT_LEN)
        validate_settings_report(before)
        after = bytearray(before)
        changed_offsets = sorted(set(mutate(after)))
        update_settings_crc(after)
        validate_settings_report(after)
        return finish_write(args, transport, before, bytes(after), changed_offsets, summary, SETTINGS_REPORT_ID, "settings")


def modify_names(args: argparse.Namespace, mutate, summary: str) -> int:
    with open_quadro(args.device, writable=not args.dry_run, backend=args.backend) as transport:
        before = transport.get_feature_report(NAMES_REPORT_ID, NAMES_REPORT_LEN)
        validate_names_report(before)
        after = bytearray(before)
        changed_offsets = sorted(set(mutate(after)))
        update_names_crc(after)
        validate_names_report(after)
        return finish_write(args, transport, before, bytes(after), changed_offsets, summary, NAMES_REPORT_ID, "names")


def finish_write(
    args: argparse.Namespace,
    transport,
    before: bytes,
    after: bytes,
    changed_offsets: list[int],
    summary: str,
    report_id: int,
    backup_kind: str,
) -> int:
    changed = byte_diff_offsets(before, after)
    if not changed:
        print("No changes needed.")
        return 0
    print(summary)
    print(f"Changed offsets: {format_offsets(changed)}")
    unexpected = sorted(set(changed) - set(changed_offsets) - {len(after) - 2, len(after) - 1})
    if unexpected:
        raise ReportError(f"internal safety check found unexpected changed offsets: {format_offsets(unexpected)}")
    if args.dry_run:
        print("Dry run only; no write performed.")
        return 0

    write_backup(args.backup_dir, backup_kind, before)
    transport.set_feature_report(after)
    commit(args, transport)
    readback = transport.get_feature_report(report_id, len(after))
    if readback != after:
        diff = format_offsets(byte_diff_offsets(after, readback))
        raise ReportError(f"device readback did not match intended report; differing offsets: {diff}")
    print("Write verified by readback.")
    return 0


def commit(args: argparse.Namespace, transport) -> None:
    if not args.no_commit:
        transport.write_output_report(COMMIT_OUTPUT_REPORT)


def write_backup(directory: Path, kind: str, report: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / f"quadro-{kind}-backup-{stamp}.bin"
    path.write_bytes(report)
    print(f"Backup written to {path}")
    return path


def print_status_summary(status) -> None:
    print(f"Quadro {status.serial} firmware={status.firmware} profile={status.profile_id}")
    print(f"system_state=0x{status.system_state:08x} alarm_state=0x{status.alarm_state:08x}")
    print(f"12V={status.vcc12:.2f}V flow_raw={status.flow_raw}")
    print("Temperatures:")
    for idx, temp in enumerate(status.temperatures, start=1):
        value = "disconnected" if temp is None else f"{temp:.2f} C"
        print(f"  temp{idx}: {value}")
    print("Fans:")
    for idx, fan in enumerate(status.fans, start=1):
        print(
            f"  fan{idx}: output={fan.output_percent:.2f}% voltage={fan.voltage:.2f}V "
            f"rpm={fan.rpm} flags=0x{fan.flags:02x}"
        )
    print("Controllers:")
    for idx, controller in enumerate(status.controllers, start=1):
        print(f"  controller{idx}: output={controller.output_percent:.2f}%")


def print_verbose_status(status) -> None:
    print("Live Report:")
    print(f"  structure_id={status.structure_id}")
    print(f"  serial_raw=0x{status.serial_raw:08x}")
    print(f"  serial={status.serial}")
    print(f"  hardware={status.hardware}")
    print(f"  device_type={status.device_type}")
    print(f"  bootloader={status.bootloader}")
    print(f"  firmware={status.firmware}")
    print(f"  system_state=0x{status.system_state:08x}")
    print(f"  feature_unlock={status.feature_unlock}")
    print(f"  device_time={status.device_time}")
    print(f"  power_up_count={status.power_up_count}")
    print(f"  runtime_total={status.runtime_total}")
    print(f"  adc_raw={format_sequence(status.adc_raw)}")
    print(f"  software_sensors_raw={format_sequence(status.software_sensors_raw)}")
    print(f"  software_sensor_units={format_sequence(status.software_sensor_units)}")
    print(f"  vcc12={status.vcc12:.2f} V")
    print(f"  flow_raw={status.flow_raw}")
    print(f"  alarm_state=0x{status.alarm_state:08x}")
    print(f"  alarm_state_last=0x{status.alarm_state_last:08x}")
    print(f"  strip_power={status.strip_power}")
    print(f"  strip_power_scale={status.strip_power_scale}")
    print(f"  strip_scale={status.strip_scale}")
    print(f"  strip_state=0x{status.strip_state:04x}")
    print(f"  profile_id={status.profile_id}")
    print("  temperatures:")
    for idx, temp in enumerate(status.temperatures, start=1):
        value = "disconnected" if temp is None else f"{temp:.2f} C"
        print(f"    temp{idx}: {value}")
    print("  fans:")
    for idx, fan in enumerate(status.fans, start=1):
        print(
            f"    fan{idx}: output={fan.output_percent:.2f}% voltage={fan.voltage:.2f}V "
            f"current_raw={fan.current_raw} power_raw={fan.power_raw} rpm={fan.rpm} "
            f"torque_raw={fan.torque_raw} flags=0x{fan.flags:02x}"
        )
    print("  controllers:")
    for idx, controller in enumerate(status.controllers, start=1):
        print(
            f"    controller{idx}: input_offset={controller.input_offset} "
            f"output_offset={controller.output_offset} output_scale={controller.output_scale} "
            f"output={controller.output_percent:.2f}%"
        )


def print_settings(settings, verbose: bool = False) -> None:
    print(f"Settings structure={settings.structure_id} profile={settings.profile_id}")
    if verbose:
        print(f"  i2c_address={settings.i2c_address}")
        print(f"  device_flags=0x{settings.device_flags:04x}")
        print(f"  flow_calibration={settings.flow_calibration}")
        print(f"  flow_factor={settings.flow_factor}")
        print(f"  strip_brightness={settings.strip_brightness}")
        print(f"  strip_flags=0x{settings.strip_flags:04x}")
        print(f"  dummy={settings.dummy}")
        print("LED Controllers:")
        for idx, led in enumerate(settings.led_controllers, start=1):
            print(
                f"  led{idx}: strip={led.strip} led_start={led.led_start} count={led.count} "
                f"mode={led.mode} flags=0x{led.flags:04x}"
            )
            print(f"    src_raw={led.src_raw.hex()}")
            print(f"    binding1_raw={led.binding1_raw.hex()}")
            print(f"    binding2_raw={led.binding2_raw.hex()}")
            print(f"    values={format_sequence(led.values)}")
            print(f"    hsv_raw={led.hsv_raw.hex()}")
    print("Sensors:")
    for idx, sensor in enumerate(settings.sensors, start=1):
        print(f"  temp{idx}: offset={sensor.offset_c:.2f} C")
    print("Fans:")
    for idx, fan in enumerate(settings.fans, start=1):
        print(
            f"  fan{idx}: min={fan.min_power:.2f}% max={fan.max_power:.2f}% "
            f"fallback={fan.fallback_power:.2f}% rpm_max={fan.max_rpm} "
            f"start_boost={'on' if fan.start_boost else 'off'} "
            f"hold_min={'on' if fan.hold_min_power else 'off'} flags=0x{fan.flags:02x}"
        )
    print("Controllers:")
    for idx, controller in enumerate(settings.controllers, start=1):
        print(
            f"  fan{idx}: mode={mode_name(controller.mode)} fixed={controller.fixed_power:.2f}% "
            f"source={source_name(controller.source)} target={controller.pid_setpoint_c:.2f} C"
        )
        if verbose:
            print(
                f"    pid: p={controller.pid_p} i={controller.pid_i} d={controller.pid_d} "
                f"d_tn={controller.pid_d_tn} hysteresis={controller.pid_hysteresis_c:.2f} C "
                f"flags=0x{controller.pid_flags:04x}"
            )
            print(f"    curve_start={controller.curve_start_c:.2f} C")
            print("    curve_points:")
            for point_idx, point in enumerate(controller.curve_points, start=1):
                print(
                    f"      {point_idx:02d}: temp={point.temperature_c:.2f} C "
                    f"power={point.power_percent:.2f}%"
                )


def print_names(names: list[str], verbose: bool = False, version: int | None = None) -> None:
    print("Names:")
    if version is not None:
        print(f"  structure_id={version}")
    for slot, name in enumerate(names):
        if verbose or name or slot in KNOWN_NAME_SLOTS.values():
            print(f"  {slot_label(slot):>7} ({slot:02d}): {name!r}")


def mode_name(mode: int) -> str:
    return {0: "fixed", 1: "target-temp", 2: "curve", 4: "mirror"}.get(mode, f"unknown({mode})")


def source_name(source: int) -> str:
    if source == -1:
        return "none"
    if 0 <= source <= 3:
        return f"temp{source + 1}"
    return str(source)


def format_sequence(values) -> str:
    return "[" + ", ".join(str(value) for value in values) + "]"


def parse_one_based_4(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be 1, 2, 3, or 4") from exc
    if number < 1 or number > 4:
        raise argparse.ArgumentTypeError("must be 1, 2, 3, or 4")
    return number - 1


def parse_on_off(value: str) -> bool:
    lowered = value.lower()
    if lowered in {"on", "yes", "true", "1"}:
        return True
    if lowered in {"off", "no", "false", "0"}:
        return False
    raise argparse.ArgumentTypeError("must be on or off")


def parse_u8(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer in range 0..255") from exc
    if number < 0 or number > 255:
        raise argparse.ArgumentTypeError("must be an integer in range 0..255")
    return number


def parse_source(value: str) -> int:
    lowered = value.lower()
    if lowered in {"none", "off", "-1"}:
        return -1
    if lowered.startswith("temp"):
        return parse_one_based_4(lowered[4:])
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("source must be temp1..temp4, none, or a numeric source") from exc
    if number < -1 or number > 3:
        raise argparse.ArgumentTypeError("numeric source must be -1..3")
    return number


def parse_target_source(value: str) -> int:
    source = parse_source(value)
    if source < 0:
        raise argparse.ArgumentTypeError("target-temperature source must be temp1..temp4")
    return source


def parse_curve_point(value: str) -> tuple[float, float]:
    if ":" not in value:
        raise argparse.ArgumentTypeError("curve points must look like TEMP:PERCENT")
    left, right = value.split(":", 1)
    try:
        return float(left), float(right)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("curve point temperature and percent must be numbers") from exc


KNOWN_NAME_SLOTS = {
    "fan1": 1,
    "fan2": 2,
    "fan3": 3,
    "fan4": 4,
    "led1": 9,
    "led2": 10,
    "led3": 11,
    "led4": 12,
    "led5": 13,
    "led6": 14,
    "led7": 15,
    "led8": 16,
    "flow": 17,
    "temp1": 18,
    "temp2": 19,
    "temp3": 20,
    "temp4": 21,
    "strip": 24,
}
for idx in range(1, 17):
    KNOWN_NAME_SLOTS[f"soft{idx}"] = 24 + idx


def parse_name_slot(value: str) -> int:
    key = value.lower().replace("-", "")
    if key in KNOWN_NAME_SLOTS:
        return KNOWN_NAME_SLOTS[key]
    try:
        slot = int(value)
    except ValueError as exc:
        choices = ", ".join(sorted(KNOWN_NAME_SLOTS))
        close = difflib.get_close_matches(key, KNOWN_NAME_SLOTS, n=1)
        suggestion = f"; did you mean {close[0]}?" if close else ""
        raise argparse.ArgumentTypeError(f"unknown name slot; use one of {choices} or 0..41{suggestion}") from exc
    if slot < 0 or slot > 41:
        raise argparse.ArgumentTypeError("name slot must be in range 0..41")
    return slot


def slot_label(slot: int) -> str:
    for label, value in KNOWN_NAME_SLOTS.items():
        if value == slot:
            return label
    return f"slot{slot}"


def byte_diff_offsets(left: bytes, right: bytes) -> list[int]:
    return [idx for idx, (a, b) in enumerate(zip(left, right)) if a != b]


def format_offsets(offsets: list[int]) -> str:
    if not offsets:
        return "none"
    ranges = []
    start = prev = offsets[0]
    for offset in offsets[1:]:
        if offset == prev + 1:
            prev = offset
            continue
        ranges.append((start, prev))
        start = prev = offset
    ranges.append((start, prev))
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges)


if __name__ == "__main__":
    raise SystemExit(main())
