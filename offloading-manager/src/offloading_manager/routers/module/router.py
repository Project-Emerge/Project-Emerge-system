from typing import Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from .module import Module
from offloading_manager.type import ModuleType


class ModuleModel(Protocol):
    def add_module(self, module: Module, module_type: ModuleType) -> None: ...

    def remove_module(self, module_type: ModuleType) -> None: ...


class ModuleRouter:
    def __init__(self, module_model: ModuleModel):
        self._module_router = APIRouter()

        @self._module_router.websocket("/ws/position")
        async def position_websoket(websocket: WebSocket):
            position_websoket = Module(websocket)
            await position_websoket.connect()
            module_model.add_module(position_websoket, ModuleType.ARUCO)
            try:
                await position_websoket.listen()
            except WebSocketDisconnect:
                pass
            finally:
                await position_websoket.disconnect()
                module_model.remove_module(ModuleType.ARUCO)

        @self._module_router.websocket("/ws/aggregate")
        async def aggregate_websoket(websocket: WebSocket):
            aggregate_websoket = Module(websocket)
            await aggregate_websoket.connect()
            module_model.add_module(aggregate_websoket, ModuleType.AGGREGATE)
            try:
                await aggregate_websoket.listen()
            except WebSocketDisconnect:
                pass
            finally:
                await aggregate_websoket.disconnect()
                module_model.remove_module(ModuleType.AGGREGATE)

        @self._module_router.websocket("/ws/neighbor")
        async def neighbor_websoket(websocket: WebSocket):
            neighbor_websoket = Module(websocket)
            await neighbor_websoket.connect()
            module_model.add_module(neighbor_websoket, ModuleType.NEIGHBOR)
            try:
                await neighbor_websoket.listen()
            except WebSocketDisconnect:
                pass
            finally:
                await neighbor_websoket.disconnect()
                module_model.remove_module(ModuleType.NEIGHBOR)

    @property
    def router(self) -> APIRouter:
        return self._module_router
