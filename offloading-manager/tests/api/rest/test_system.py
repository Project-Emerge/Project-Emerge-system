import pytest
from unittest.mock import MagicMock
from offloading_manager.main import model
from offloading_manager.type import ModuleType, Stats, OffloadingType
from offloading_manager.routers.robot.robot import Robot


class TestGetSystemStress:
    def test_returns_200(self, test_client):
        response = test_client.get("/stress/system")
        assert response.status_code == 200

    def test_empty_system_returns_empty_stats(self, test_client):
        response = test_client.get("/stress/system")
        data = response.json()
        assert data["cpu_usage"] == {}
        assert data["memory_usage"] == {}
        assert data["offloading_capable_robot"] == 0
        assert data["robot_in_offloading"] == {
            "aggregate": 0,
            "aruco": 0,
            "neighbor": 0,
        }

    def test_module_stats_reported_correctly(self, test_client):
        model._moduleRegistry.update_module_stats(
            ModuleType.ARUCO, Stats(cpu_usage=42.5, memory_usage=33.1)
        )
        response = test_client.get("/stress/system")
        data = response.json()
        assert data["cpu_usage"]["aruco"] == pytest.approx(42.5)
        assert data["memory_usage"]["aruco"] == pytest.approx(33.1)

    def test_robot_count_reflects_registered_robots(self, test_client):
        for i in range(3):
            r = MagicMock(spec=Robot)
            r.robot_id = i
            model._robotRegistry.add_robot_connection(r, i)
        response = test_client.get("/stress/system")
        assert response.json()["offloading_capable_robot"] == 3

    def test_robot_in_offloading_counts_per_module(self, test_client):
        for i in range(2):
            r = MagicMock(spec=Robot)
            r.robot_id = i
            model._robotRegistry.add_robot_connection(r, i)
            model._robotRegistry.change_offloading_state(i, OffloadingType(aruco=True))

        response = test_client.get("/stress/system")
        data = response.json()
        assert data["robot_in_offloading"]["aruco"] == 2
        assert data["robot_in_offloading"]["aggregate"] == 0
