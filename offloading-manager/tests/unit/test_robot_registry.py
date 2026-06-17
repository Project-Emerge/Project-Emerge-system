import pytest
from unittest.mock import MagicMock
from offloading_manager.core.robot_registry import RobotRegistry
from offloading_manager.routers.robot.robot import Robot
from offloading_manager.type import OffloadingType, ModuleType


@pytest.fixture
def registry():
    return RobotRegistry()


@pytest.fixture
def robot():
    r = MagicMock(spec=Robot)
    r.robot_id = 42
    return r


@pytest.fixture
def registry_with_robot(registry, robot):
    registry.add_robot_connection(robot, robot.robot_id)
    return registry


class TestAddAndRemove:
    def test_add_robot_initialises_empty_offloading_state(self, registry, robot):
        registry.add_robot_connection(robot, robot.robot_id)
        state = registry.get_robot_state(robot.robot_id)
        assert state == OffloadingType(aggregate=False, aruco=False, neighbor=False)

    def test_remove_robot_clears_entry(self, registry_with_robot, robot):
        registry_with_robot.remove_robot_connection(robot.robot_id)
        assert registry_with_robot.get_robot_state(robot.robot_id) is None

    def test_remove_nonexistent_robot_does_not_raise(self, registry):
        registry.remove_robot_connection(999)

    def test_get_robot_connection_returns_robot(self, registry_with_robot, robot):
        assert registry_with_robot.get_robot_connection(robot.robot_id) is robot

    def test_get_robot_connection_missing_returns_none(self, registry):
        assert registry.get_robot_connection(999) is None


class TestOffloadingState:
    def test_change_offloading_state(self, registry_with_robot, robot):
        new_state = OffloadingType(aggregate=True, aruco=False, neighbor=True)
        registry_with_robot.change_offloading_state(robot.robot_id, new_state)
        assert registry_with_robot.get_robot_state(robot.robot_id) == new_state

    def test_change_state_nonexistent_robot_does_not_raise(self, registry):
        registry.change_offloading_state(999, OffloadingType(aggregate=True))

    def test_get_all_robots_state(self, registry):
        r1, r2 = MagicMock(spec=Robot), MagicMock(spec=Robot)
        r1.robot_id, r2.robot_id = 1, 2
        registry.add_robot_connection(r1, 1)
        registry.add_robot_connection(r2, 2)
        all_states = registry.get_all_robots_state()
        assert set(all_states.keys()) == {1, 2}
        assert all(isinstance(v, OffloadingType) for v in all_states.values())


class TestModuleQueries:
    def test_get_offloading_robots_in_module_empty(self, registry_with_robot):
        assert registry_with_robot.get_offloading_robots_in_module(ModuleType.ARUCO) == []

    def test_get_offloading_robots_in_module_after_state_change(self, registry_with_robot, robot):
        registry_with_robot.change_offloading_state(robot.robot_id, OffloadingType(aruco=True))
        result = registry_with_robot.get_offloading_robots_in_module(ModuleType.ARUCO)
        assert robot.robot_id in result

    def test_get_not_offloading_robots(self, registry_with_robot, robot):
        assert robot.robot_id in registry_with_robot.get_not_offloading_robots()

    def test_get_not_offloading_robots_excludes_active(self, registry_with_robot, robot):
        registry_with_robot.change_offloading_state(robot.robot_id, OffloadingType(aggregate=True))
        assert robot.robot_id not in registry_with_robot.get_not_offloading_robots()

    def test_get_module_for_robot_empty(self, registry_with_robot, robot):
        assert registry_with_robot.get_module_for_robot(robot.robot_id) == []

    def test_get_module_for_robot_with_modules(self, registry_with_robot, robot):
        registry_with_robot.change_offloading_state(
            robot.robot_id, OffloadingType(aggregate=True, neighbor=True)
        )
        modules = registry_with_robot.get_module_for_robot(robot.robot_id)
        assert ModuleType.AGGREGATE in modules
        assert ModuleType.NEIGHBOR in modules
        assert ModuleType.ARUCO not in modules

    def test_get_module_for_nonexistent_robot_returns_empty(self, registry):
        assert registry.get_module_for_robot(999) == []

    def test_get_robot_in_module_offloading_counts(self, registry):
        r1, r2 = MagicMock(spec=Robot), MagicMock(spec=Robot)
        r1.robot_id, r2.robot_id = 1, 2
        registry.add_robot_connection(r1, 1)
        registry.add_robot_connection(r2, 2)
        registry.change_offloading_state(1, OffloadingType(aruco=True))
        registry.change_offloading_state(2, OffloadingType(aruco=True, aggregate=True))
        counts = registry.get_robot_in_module_offloading()
        assert counts[ModuleType.ARUCO] == 2
        assert counts[ModuleType.AGGREGATE] == 1
        assert counts[ModuleType.NEIGHBOR] == 0


class TestCounts:
    def test_get_drone_offloading_capable_counts_all_robots(self, registry):
        for i in range(3):
            r = MagicMock(spec=Robot)
            r.robot_id = i
            registry.add_robot_connection(r, i)
        assert registry.get_drone_offloading_capable() == 3

    def test_get_robots_ids(self, registry):
        for i in [10, 20, 30]:
            r = MagicMock(spec=Robot)
            r.robot_id = i
            registry.add_robot_connection(r, i)
        assert set(registry.get_robots_ids()) == {10, 20, 30}