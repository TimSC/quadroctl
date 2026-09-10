import unittest

from quadroctl.crc import crc16_usb
from quadroctl.reports import (
    NAMES_REPORT_ID,
    NAMES_REPORT_LEN,
    SETTINGS_REPORT_ID,
    SETTINGS_REPORT_LEN,
    curve_input,
    curve_output,
    parse_names,
    parse_settings,
    patch_curve,
    patch_fan_flag,
    patch_fan_power_field,
    patch_fixed_power,
    patch_name,
    patch_sensor_offset,
    patch_target_temperature,
    settings_payload_offset,
    update_names_crc,
    update_settings_crc,
    validate_names_report,
    validate_settings_report,
)


class ReportTests(unittest.TestCase):
    def make_settings(self):
        report = bytearray(SETTINGS_REPORT_LEN)
        report[0] = SETTINGS_REPORT_ID
        update_settings_crc(report)
        return report

    def make_names(self):
        report = bytearray(NAMES_REPORT_LEN)
        report[0] = NAMES_REPORT_ID
        update_names_crc(report)
        return report

    def test_crc_check_value(self):
        self.assertEqual(crc16_usb(b"123456789"), 0xB4C8)

    def test_settings_patch_offsets_and_parse(self):
        report = self.make_settings()
        patch_fixed_power(report, 0, 30)
        patch_fan_flag(report, 0, 0x02, False)
        patch_fan_power_field(report, 0, "min", 20)
        patch_fan_power_field(report, 0, "max", 80)
        patch_fan_power_field(report, 0, "fallback", 100)
        patch_sensor_offset(report, 0, 1.5)
        update_settings_crc(report)
        validate_settings_report(report)

        settings = parse_settings(report)
        self.assertEqual(settings.controllers[0].mode, 0)
        self.assertEqual(settings.controllers[0].fixed_power, 30)
        self.assertEqual(settings.fans[0].min_power, 20)
        self.assertEqual(settings.fans[0].max_power, 80)
        self.assertEqual(settings.fans[0].fallback_power, 100)
        self.assertEqual(settings.sensors[0].offset_c, 1.5)

    def test_curve_requires_16_points(self):
        report = self.make_settings()
        points = [(25 + idx, 20 + idx) for idx in range(16)]
        patch_curve(report, 0, 0, points)
        update_settings_crc(report)
        validate_settings_report(report)
        settings = parse_settings(report)
        self.assertEqual(settings.controllers[0].mode, 2)
        self.assertEqual(settings.controllers[0].source, 0)
        self.assertEqual(settings.controllers[0].curve_points[15].temperature_c, 40)
        self.assertEqual(settings.controllers[0].curve_points[15].power_percent, 35)

        with self.assertRaises(ValueError):
            patch_curve(report, 0, 0, points[:15])

    def test_target_temperature_preserves_pid_gains(self):
        report = self.make_settings()
        # Absolute report offsets include the leading HID report ID.
        report[61:69] = b"\x09\xc4\x07\xd0\x01\xf4\x00\x0a"
        patch_target_temperature(report, 0, 2, 45.5)
        update_settings_crc(report)
        validate_settings_report(report)

        settings = parse_settings(report)
        controller = settings.controllers[0]
        self.assertEqual(controller.mode, 1)
        self.assertEqual(controller.source, 2)
        self.assertEqual(controller.pid_setpoint_c, 45.5)
        self.assertEqual(controller.pid_p, 2500)
        self.assertEqual(controller.pid_i, 2000)
        self.assertEqual(controller.pid_d, 500)
        self.assertEqual(controller.pid_d_tn, 10)

    def test_target_temperature_rejects_no_source(self):
        report = self.make_settings()
        with self.assertRaises(ValueError):
            patch_target_temperature(report, 0, -1, 45)

    def test_curve_offsets_match_documentation(self):
        self.assertEqual(settings_payload_offset(curve_input(0, 7)), 89)
        self.assertEqual(settings_payload_offset(curve_output(0, 7)), 121)

    def test_names_patch(self):
        report = self.make_names()
        patch_name(report, 1, "Radiator")
        update_names_crc(report)
        validate_names_report(report)
        names = parse_names(report)
        self.assertEqual(names[1], "Radiator")

    def test_name_too_long(self):
        report = self.make_names()
        with self.assertRaises(ValueError):
            patch_name(report, 1, "x" * 24)


if __name__ == "__main__":
    unittest.main()
