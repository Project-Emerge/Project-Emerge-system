import json
from offloading_manager.main import model


def _rpc_request(method: str, params: dict, id: int = 1) -> str:
    return json.dumps({"jsonrpc": 2.0, "method": method, "params": params, "id": id})


def _rpc_response(result: dict, id: int = 1) -> str:
    return json.dumps({"jsonrpc": 2.0, "result": result, "id": id})


class TestRobotWebSocket:
    def test_robot_is_registered_on_connect(self, test_client):
        with test_client.websocket_connect("/ws/robots/10"):
            assert model._robotRegistry.get_robot_state(10) is not None

    def test_robot_is_removed_on_disconnect(self, test_client):
        with test_client.websocket_connect("/ws/robots/11"):
            pass
        assert model._robotRegistry.get_robot_state(11) is None

    def test_robot_sends_self_request_accepted(self, test_client):
        """Robot initiates offloading request — manager responds True."""
        with test_client.websocket_connect("/ws/robots/20") as ws:
            payload = _rpc_request(
                "change_status",
                {
                    "id": 20,
                    "type": {"aggregate": True, "aruco": False, "neighbor": False},
                },
                id=5,
            )
            ws.send_text(payload)
            response = ws.receive_json()
            # Manager acknowledges with a SuccessResponse
            assert response["id"] == 5
            assert response["result"] is True

    def test_robot_invalid_json_returns_error(self, test_client):
        with test_client.websocket_connect("/ws/robots/30") as ws:
            ws.send_text("this is not json")
            response = ws.receive_json()
            assert "error" in response

    def test_robot_invalid_rpc_params_returns_error_response(self, test_client):
        with test_client.websocket_connect("/ws/robots/40") as ws:
            # Valid JSON-RPC request but params don't match OffloadingRequest schema
            payload = _rpc_request("change_status", {"bad": "data"}, id=9)
            ws.send_text(payload)
            response = ws.receive_json()
            assert "error" in response
            assert response["id"] == 9

    def test_multiple_robots_isolated(self, test_client):
        with test_client.websocket_connect("/ws/robots/50"):
            with test_client.websocket_connect("/ws/robots/51"):
                assert model._robotRegistry.get_robot_state(50) is not None
                assert model._robotRegistry.get_robot_state(51) is not None
