import pytest
from starlette.testclient import TestClient
from unittest.mock import MagicMock
from offloading_manager.core.decision_module import OnlyDeleteDecisionModule
from offloading_manager.core.model import Model
from offloading_manager.main import app, model
from offloading_manager.type import ModuleType, Stats


@pytest.fixture
def test():
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture
def model_fixture():
    return Model(OnlyDeleteDecisionModule)


@pytest.fixture
def populated_model(model):
    """Model with two robots and one module already registered."""
    from offloading_manager.routers.robot.robot import Robot
    from offloading_manager.routers.module.module import Module

    robot1 = MagicMock(spec=Robot)
    robot1.robot_id = 1
    robot2 = MagicMock(spec=Robot)
    robot2.robot_id = 2

    model._robotRegistry.add_robot_connection(robot1, 1)
    model._robotRegistry.add_robot_connection(robot2, 2)

    module = MagicMock(spec=Module)
    module._connected = True
    model._moduleRegistry.add_module_connection(ModuleType.ARUCO, module)
    model._moduleRegistry.update_module_stats(ModuleType.ARUCO, Stats(cpu_usage=10.0, memory_usage=20.0))

    return model, robot1, robot2, module


@pytest.fixture
def test_client():
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture(autouse=True)
def clean_model():
    """Reset model state between tests."""
    model._robotRegistry._robots_state.clear()
    model._moduleRegistry._module_connections.clear()
    model._moduleRegistry._module_stats.clear()
    yield
    model._robotRegistry._robots_state.clear()
    model._moduleRegistry._module_connections.clear()
    model._moduleRegistry._module_stats.clear()


