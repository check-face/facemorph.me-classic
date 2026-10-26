"""Regression: explicit GPU requests cannot silently become CPU inference."""
import os
import unittest
from unittest.mock import patch
import torch

from runtime_device import check_capability, configure_device


class DeviceTests(unittest.TestCase):
    def test_default_cpu(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(configure_device(torch).type, 'cpu')

    def test_cuda_missing_fails(self):
        with patch.dict(os.environ, {'CHECKFACE_DEVICE': 'cuda'}), patch.object(torch.cuda, 'is_available', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'NVIDIA Container Toolkit'):
                configure_device(torch)

    def test_invalid_device_fails(self):
        with patch.dict(os.environ, {'CHECKFACE_DEVICE': 'typo'}):
            with self.assertRaises(ValueError):
                configure_device(torch)


class CapabilityTests(unittest.TestCase):
    """The startup capability check: a card outside the wheel's kernels stops with a plain message."""

    def check(self, capability, arches):
        with patch.object(torch.cuda, 'get_device_capability', return_value=capability), \
                patch.object(torch.cuda, 'get_arch_list', return_value=arches), \
                patch.object(torch.cuda, 'get_device_name', return_value='NVIDIA GeForce GTX 1050'):
            check_capability(torch)

    def test_listed_kernel_runs(self):
        self.check((7, 5), ['sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_120'])

    def test_ptx_at_or_below_runs(self):
        self.check((12, 1), ['sm_75', 'sm_120', 'compute_120'])

    def test_older_card_names_card_capability_and_range(self):
        with self.assertRaises(RuntimeError) as raised:
            self.check((6, 1), ['sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120', 'compute_120'])
        message = str(raised.exception)
        for part in ('GTX 1050', 'compute capability 6.1', 'supports compute capability 7.5 to 12.0',
                     'docker compose -f self-host/compose.yml up'):
            self.assertIn(part, message)

    def test_cuda_request_runs_the_check(self):
        with patch.dict(os.environ, {'CHECKFACE_DEVICE': 'cuda'}), \
                patch.object(torch.cuda, 'is_available', return_value=True), \
                patch.object(torch.cuda, 'get_device_capability', return_value=(6, 1)), \
                patch.object(torch.cuda, 'get_arch_list', return_value=['sm_75']), \
                patch.object(torch.cuda, 'get_device_name', return_value='NVIDIA GeForce GTX 1050'):
            with self.assertRaisesRegex(RuntimeError, 'compute capability 6.1'):
                configure_device(torch)


if __name__ == '__main__':
    unittest.main()
