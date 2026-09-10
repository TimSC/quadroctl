import sys
import types
import unittest
from unittest import mock

from quadroctl.transport import TransportError, _hidapi_path, list_devices, open_quadro


class FakeHidDevice:
    opened = None

    def open(self, vendor_id, product_id):
        self.opened = ("ids", vendor_id, product_id)

    def open_path(self, path):
        self.opened = ("path", path)

    def close(self):
        pass


class TransportTests(unittest.TestCase):
    def test_hidapi_paths_are_bytes_on_windows(self):
        with mock.patch("platform.system", return_value="Windows"):
            self.assertEqual(_hidapi_path(r"\\?\hid#vid_0c70"), b"\\\\?\\hid#vid_0c70")

    def test_hidraw_is_linux_only(self):
        with mock.patch("platform.system", return_value="Windows"):
            with self.assertRaisesRegex(TransportError, "only available on Linux"):
                open_quadro(backend="hidraw")

    def test_hidraw_sysfs_without_dev_node_is_reported(self):
        with (
            mock.patch("platform.system", return_value="Linux"),
            mock.patch("quadroctl.transport._list_hidraw_devices", return_value=[]),
            mock.patch("quadroctl.transport._hidraw_candidates_seen_in_sysfs", return_value=["/dev/hidraw4"]),
        ):
            with self.assertRaisesRegex(TransportError, "no /dev node is available"):
                open_quadro(backend="hidraw")

    def test_list_hidapi_devices(self):
        fake_hid = types.SimpleNamespace(
            enumerate=lambda vendor_id, product_id: [
                {
                    "path": b"hid-path",
                    "serial_number": "12345-67890",
                    "product_string": "QUADRO",
                    "manufacturer_string": "Aquacomputer",
                }
            ]
        )
        with mock.patch.dict(sys.modules, {"hid": fake_hid}):
            devices = list_devices("hidapi")
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].path, "hid-path")
        self.assertEqual(devices[0].backend, "hidapi")
        self.assertEqual(devices[0].serial, "12345-67890")

    def test_open_hidapi_by_id_without_path(self):
        fake_device = FakeHidDevice()
        fake_hid = types.SimpleNamespace(device=lambda: fake_device)
        with mock.patch.dict(sys.modules, {"hid": fake_hid}):
            transport = open_quadro(backend="hidapi")
        self.assertEqual(fake_device.opened, ("ids", 0x0C70, 0xF00D))
        transport.close()


if __name__ == "__main__":
    unittest.main()
