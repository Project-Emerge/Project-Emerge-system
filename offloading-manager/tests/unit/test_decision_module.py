import pytest
from unittest.mock import MagicMock, AsyncMock
from offloading_manager.core.decision_module import OnlyDeleteDecisionModule
from offloading_manager.core.robot_registry import RobotRegistry
from offloading_manager.core.module_registry import ModuleRegistry
from offloading_manager.routers.robot.robot import Robot
from offloading_manager.routers.module.module import Module
from offloading_manager.type import ModuleType, OffloadingType, Stats


@pytest.fixture
def robot_registry():
    return RobotRegistry()


@pytest.fixture
def module_registry():
    return ModuleRegistry()


@pytest.fixture
def decision(robot_registry, module_registry):
    return OnlyDeleteDecisionModule(robot_registry, module_registry)


def _make_robot(robot_id: int, accept: bool = True) -> MagicMock:
    r = MagicMock(spec=Robot)
    r.robot_id = robot_id
    r.change_status_request = AsyncMock(return_value=accept)
    r.respond = AsyncMock()
    return r


def _make_module() -> MagicMock:
    m = MagicMock(spec=Module)
    m.change_status_request = AsyncMock()
    return m


class TestOffloadingRequestConsideration:
    @pytest.mark.asyncio
    async def test_returns_false_when_robot_not_registered(self, decision):
        result = await decision.offloading_request_consideration(999, OffloadingType())
        assert result.success is False

    @pytest.mark.asyncio
    async def test_returns_true_when_robot_accepts(self, decision, robot_registry):
        robot = _make_robot(1, accept=True)
        robot_registry.add_robot_connection(robot, 1)
        result = await decision.offloading_request_consideration(1, OffloadingType(aggregate=True))
        assert result.success is True

    @pytest.mark.asyncio
    async def test_returns_false_when_robot_rejects(self, decision, robot_registry):
        robot = _make_robot(2, accept=False)
        robot_registry.add_robot_connection(robot, 2)
        result = await decision.offloading_request_consideration(2, OffloadingType(aggregate=True))
        assert result.success is False

    @pytest.mark.asyncio
    async def test_updates_registry_state_on_accept(self, decision, robot_registry):
        robot = _make_robot(3, accept=True)
        robot_registry.add_robot_connection(robot, 3)
        await decision.offloading_request_consideration(3, OffloadingType(aggregate=True))
        assert robot_registry.get_robot_state(3).aggregate is True

    @pytest.mark.asyncio
    async def test_notifies_modules_on_accept(self, decision, robot_registry, module_registry):
        robot = _make_robot(4, accept=True)
        robot_registry.add_robot_connection(robot, 4)
        module = _make_module()
        module_registry.add_module_connection(ModuleType.AGGREGATE, module)
        await decision.offloading_request_consideration(4, OffloadingType(aggregate=True))
        module.change_status_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_notify_modules_on_reject(self, decision, robot_registry, module_registry):
        robot = _make_robot(5, accept=False)
        robot_registry.add_robot_connection(robot, 5)
        module = _make_module()
        module_registry.add_module_connection(ModuleType.AGGREGATE, module)
        await decision.offloading_request_consideration(5, OffloadingType(aggregate=True))
        module.change_status_request.assert_not_awaited()


class TestSelfOffloadingRequestConsideration:
    @pytest.mark.asyncio
    async def test_responds_true_to_robot(self, decision, robot_registry):
        robot = _make_robot(10)
        robot_registry.add_robot_connection(robot, 10)
        await decision.offloading_self_request_consideration(10, OffloadingType(aruco=True), message_id=7)
        robot.respond.assert_awaited_once_with(True, 7)

    @pytest.mark.asyncio
    async def test_updates_state_on_self_request(self, decision, robot_registry):
        robot = _make_robot(11)
        robot_registry.add_robot_connection(robot, 11)
        await decision.offloading_self_request_consideration(11, OffloadingType(neighbor=True), message_id=1)
        assert robot_registry.get_robot_state(11).neighbor is True


class TestStatsValutation:
    @pytest.mark.asyncio
    async def test_no_action_when_stats_below_threshold(self, decision, robot_registry, module_registry):
        robot = _make_robot(20, accept=True)
        robot_registry.add_robot_connection(robot, 20)
        robot_registry.change_offloading_state(20, OffloadingType(aruco=True))
        module_registry.update_module_stats(ModuleType.ARUCO, Stats(cpu_usage=50.0, memory_usage=50.0))
        await decision.stats_valutation()
        # still offloading — no removal attempted
        assert robot_registry.get_robot_state(20).aruco is True

    @pytest.mark.asyncio
    async def test_removes_robot_when_cpu_over_threshold(self, decision, robot_registry, module_registry):
        robot = _make_robot(21, accept=True)
        robot_registry.add_robot_connection(robot, 21)
        robot_registry.change_offloading_state(21, OffloadingType(aruco=True))
        module_registry.update_module_stats(ModuleType.ARUCO, Stats(cpu_usage=95.0, memory_usage=10.0))
        await decision.stats_valutation()
        assert robot_registry.get_robot_state(21).aruco is False

    @pytest.mark.asyncio
    async def test_removes_robot_when_memory_over_threshold(self, decision, robot_registry, module_registry):
        robot = _make_robot(22, accept=True)
        robot_registry.add_robot_connection(robot, 22)
        robot_registry.change_offloading_state(22, OffloadingType(aggregate=True))
        module_registry.update_module_stats(ModuleType.AGGREGATE, Stats(cpu_usage=10.0, memory_usage=95.0))
        await decision.stats_valutation()
        assert robot_registry.get_robot_state(22).aggregate is False

    @pytest.mark.asyncio
    async def test_only_one_robot_removed_per_stressed_module(self, decision, robot_registry, module_registry):
        for i in [30, 31]:
            r = _make_robot(i, accept=True)
            robot_registry.add_robot_connection(r, i)
            robot_registry.change_offloading_state(i, OffloadingType(aruco=True))
        module_registry.update_module_stats(ModuleType.ARUCO, Stats(cpu_usage=99.0, memory_usage=99.0))
        await decision.stats_valutation()
        states = [robot_registry.get_robot_state(i).aruco for i in [30, 31]]
        # exactly one was removed
        assert states.count(False) == 1
        assert states.count(True) == 1