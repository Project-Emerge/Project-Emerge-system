import json
from offloading_manager.main import model
from offloading_manager.type import ModuleType


class TestModuleWebSocket:
    def test_aruco_module_registered_on_connect(self, test_client):
        with test_client.websocket_connect("/ws/position"):
            assert (
                model._moduleRegistry.get_module_connection(ModuleType.ARUCO)
                is not None
            )

    def test_aruco_module_removed_on_disconnect(self, test_client):
        with test_client.websocket_connect("/ws/position"):
            pass
        assert model._moduleRegistry.get_module_connection(ModuleType.ARUCO) is None

    def test_aggregate_module_registered_on_connect(self, test_client):
        with test_client.websocket_connect("/ws/aggregate"):
            assert (
                model._moduleRegistry.get_module_connection(ModuleType.AGGREGATE)
                is not None
            )

    def test_aggregate_module_removed_on_disconnect(self, test_client):
        with test_client.websocket_connect("/ws/aggregate"):
            pass
        assert model._moduleRegistry.get_module_connection(ModuleType.AGGREGATE) is None

    def test_neighbor_module_registered_on_connect(self, test_client):
        with test_client.websocket_connect("/ws/neighbor"):
            assert (
                model._moduleRegistry.get_module_connection(ModuleType.NEIGHBOR)
                is not None
            )

    def test_neighbor_module_removed_on_disconnect(self, test_client):
        with test_client.websocket_connect("/ws/neighbor"):
            pass
        assert model._moduleRegistry.get_module_connection(ModuleType.NEIGHBOR) is None

    def test_module_invalid_json_returns_error(self, test_client):
        with test_client.websocket_connect("/ws/position") as ws:
            ws.send_text("not valid json")
            response = ws.receive_json()
            assert "error" in response

    def test_module_invalid_rpc_returns_error(self, test_client):
        with test_client.websocket_connect("/ws/aggregate") as ws:
            ws.send_text(json.dumps({"totally": "wrong"}))
            response = ws.receive_json()
            assert "error" in response
