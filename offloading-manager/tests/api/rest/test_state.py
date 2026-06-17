from unittest.mock import MagicMock, AsyncMock
from offloading_manager.main import model
from offloading_manager.type import OffloadingType
from offloading_manager.routers.robot.robot import Robot


def _add_robot(robot_id: int, state: OffloadingType | None = None):
    r = MagicMock(spec=Robot)
    r.robot_id = robot_id
    r.change_status_request = AsyncMock(return_value=False)
    model._robotRegistry.add_robot_connection(r, robot_id)
    if state:
        model._robotRegistry.change_offloading_state(robot_id, state)
    return r


class TestGetAllRobotsState:
    def test_empty_returns_empty_dict(self, test_client):
        response = test_client.get("/")
        assert response.status_code == 200
        assert response.json() == {"robots": {}}

    def test_returns_all_registered_robots(self, test_client):
        _add_robot(1)
        _add_robot(2)
        response = test_client.get("/")
        assert response.status_code == 200
        robots = response.json()["robots"]
        assert "1" in robots
        assert "2" in robots

    def test_robots_have_correct_default_state(self, test_client):
        _add_robot(7)
        response = test_client.get("/")
        state = response.json()["robots"]["7"]
        assert state == {"aggregate": False, "aruco": False, "neighbor": False}


class TestGetSingleRobotState:
    def test_returns_404_for_unknown_robot(self, test_client):
        response = test_client.get("/999")
        assert response.status_code == 404

    def test_returns_state_for_known_robot(self, test_client):
        _add_robot(5, OffloadingType(aggregate=True))
        response = test_client.get("/5")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 5
        assert data["state"]["aggregate"] is True
        assert data["state"]["aruco"] is False


class TestPutRobotOffloadingRequest:
    def test_returns_failure_when_robot_rejects(self, test_client):
        _add_robot(3)
        body = {"id": 3, "type": {"aggregate": True, "aruco": False, "neighbor": False}}
        response = test_client.put("/3", json=body)
        assert response.status_code == 200
        assert response.json() == {"success": False}

    def test_returns_404_equivalent_when_robot_missing(self, test_client):
        body = {
            "id": 99,
            "type": {"aggregate": True, "aruco": False, "neighbor": False},
        }
        response = test_client.put("/99", json=body)
        assert response.status_code == 200
        assert response.json() == {"success": False}
