import pytest
from unittest.mock import MagicMock
from offloading_manager.core.module_registry import ModuleRegistry
from offloading_manager.routers.module.module import Module
from offloading_manager.type import ModuleType, Stats


@pytest.fixture
def registry():
    return ModuleRegistry()


@pytest.fixture
def module():
    return MagicMock(spec=Module)


class TestModuleConnections:
    def test_add_and_get_module_connection(self, registry, module):
        registry.add_module_connection(ModuleType.ARUCO, module)
        assert registry.get_module_connection(ModuleType.ARUCO) is module

    def test_get_missing_module_returns_none(self, registry):
        assert registry.get_module_connection(ModuleType.ARUCO) is None

    def test_remove_module_connection(self, registry, module):
        registry.add_module_connection(ModuleType.ARUCO, module)
        registry.remove_module_connection(ModuleType.ARUCO)
        assert registry.get_module_connection(ModuleType.ARUCO) is None

    def test_remove_nonexistent_module_does_not_raise(self, registry):
        registry.remove_module_connection(ModuleType.AGGREGATE)

    def test_overwrite_module_connection(self, registry):
        m1, m2 = MagicMock(spec=Module), MagicMock(spec=Module)
        registry.add_module_connection(ModuleType.ARUCO, m1)
        registry.add_module_connection(ModuleType.ARUCO, m2)
        assert registry.get_module_connection(ModuleType.ARUCO) is m2


class TestStats:
    def test_update_and_get_stats(self, registry):
        stats = Stats(cpu_usage=55.0, memory_usage=30.0)
        registry.update_module_stats(ModuleType.AGGREGATE, stats)
        result = registry.get_modules_stats()
        assert result[ModuleType.AGGREGATE] == stats

    def test_get_modules_stats_empty(self, registry):
        assert registry.get_modules_stats() == {}

    def test_update_stats_overwrites(self, registry):
        registry.update_module_stats(ModuleType.ARUCO, Stats(cpu_usage=10.0, memory_usage=10.0))
        registry.update_module_stats(ModuleType.ARUCO, Stats(cpu_usage=90.0, memory_usage=85.0))
        stats = registry.get_modules_stats()[ModuleType.ARUCO]
        assert stats.cpu_usage == 90.0
        assert stats.memory_usage == 85.0

    def test_stats_for_multiple_modules(self, registry):
        registry.update_module_stats(ModuleType.ARUCO, Stats(cpu_usage=10.0, memory_usage=20.0))
        registry.update_module_stats(ModuleType.AGGREGATE, Stats(cpu_usage=50.0, memory_usage=60.0))
        all_stats = registry.get_modules_stats()
        assert len(all_stats) == 2
        assert ModuleType.ARUCO in all_stats
        assert ModuleType.AGGREGATE in all_stats