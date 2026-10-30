"""Regression: explicit GPU requests cannot silently become CPU inference."""
import os
import unittest
from unittest.mock import patch
import torch

from runtime_device import configure_device, unsupported_gpu_message


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


# Real values: the pinned torch 2.9.1+cu128 wheel's arch list as reported on the RTX 2080 SUPER (26 October 2026).
PINNED_ARCH_LIST = ['sm_70', 'sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120']


def message(name, capability, arch_list=PINNED_ARCH_LIST):
    return unsupported_gpu_message(name, capability, arch_list, '2.9.1+cu128', '12.8')


class CapabilityTests(unittest.TestCase):
    """The startup capability check: a pure function of the device capability and the wheel's arch list."""

    def test_rtx_2080_super_runs(self):
        self.assertIsNone(message('NVIDIA GeForce RTX 2080 SUPER', (7, 5)))

    def test_every_listed_generation_and_later_minors_run(self):
        for capability in [(7, 0), (7, 5), (8, 0), (8, 6), (8, 9), (9, 0), (10, 0), (12, 0), (12, 1)]:
            self.assertIsNone(message('card', capability), capability)

    def test_ptx_at_or_below_runs_a_newer_major(self):
        self.assertIsNone(message('card', (13, 0), ['sm_75', 'sm_120', 'compute_120']))
        self.assertIsNotNone(message('card', (13, 0)))

    def test_pascal_cards_get_one_plain_message(self):
        for name in ['NVIDIA GeForce GTX 1050', 'NVIDIA GeForce GTX 1080']:
            text = message(name, (6, 1))
            for part in (name, 'compute capability 6.1', 'supports compute capability 7.0 to 12.x', 'sm_70',
                         'docker compose -f self-host/compose.yml up'):
                self.assertIn(part, text)
            self.assertNotIn('no kernel image', text)

    def test_empty_arch_list_is_let_through(self):
        self.assertIsNone(message('card', (6, 1), []))

    def test_cuda_request_runs_the_check(self):
        with patch.dict(os.environ, {'CHECKFACE_DEVICE': 'cuda'}), \
                patch.object(torch.cuda, 'is_available', return_value=True), \
                patch.object(torch.cuda, 'get_device_capability', return_value=(6, 1)), \
                patch.object(torch.cuda, 'get_arch_list', return_value=PINNED_ARCH_LIST), \
                patch.object(torch.cuda, 'get_device_name', return_value='NVIDIA GeForce GTX 1080'):
            with self.assertRaisesRegex(RuntimeError, 'GTX 1080 has compute capability 6.1'):
                configure_device(torch)


if __name__ == '__main__':
    unittest.main()
