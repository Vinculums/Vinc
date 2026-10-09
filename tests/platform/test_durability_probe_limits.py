"""The actual ACA v1 layout and local v2 layout both retain enforceable caps."""
from pathlib import Path
import tempfile

from scripts.qa.durable_share_latency_probe import resource_limits


def test_aca_v1_limits():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / 'cpu').mkdir()
        (root / 'memory').mkdir()
        (root / 'cpu/cpu.cfs_quota_us').write_text('100000\n')
        (root / 'cpu/cpu.cfs_period_us').write_text('100000\n')
        (root / 'memory/memory.limit_in_bytes').write_text('2147483648\n')
        assert resource_limits(root) == {'cpu_max': '100000 100000', 'memory_max': '2147483648'}


def test_v2_and_unknown_limits():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        assert resource_limits(root) == {'cpu_max': None, 'memory_max': None}
        (root / 'cpu.max').write_text('100000 100000\n')
        (root / 'memory.max').write_text('2147483648\n')
        assert resource_limits(root) == {'cpu_max': '100000 100000', 'memory_max': '2147483648'}
