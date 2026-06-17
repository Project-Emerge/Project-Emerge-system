from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from offloading_manager.routers.json_rpc_wrapper import (
    parse_json_rpc_message,
    Notification,
)
from offloading_manager.routers.schemas import ChangeState
import logging

logger = logging.getLogger("uvicorn.error")


class Module:
    def __init__(self, websocket: WebSocket):
        self._ws = websocket
        self._connected = False

    async def connect(self):
        """Accepts the WebSocket connection and marks the module as connected."""

        await self._ws.accept()
        self._connected = True
        logger.info("Module connected")

    async def disconnect(self):
        """Closes the WebSocket connection and marks the module as disconnected."""

        self._connected = False
        try:
            await self._ws.close()
        except RuntimeError:
            pass
        logger.info("Module disconnected")

    async def listen(self):
        """Listens for incoming messages from the module, processes them, and handles disconnections and errors."""

        try:
            while self._connected:
                data = await self._ws.receive_text()
                _ = await self.parse_json(data)

        except WebSocketDisconnect:
            await self.disconnect()
        except Exception:
            pass

    async def change_status_request(self, data: ChangeState):
        """Sends a change status request to the module with the given data.
        Args:
            data (ChangeState): the data for the change status request
        """

        if self._connected:
            await self._ws.send_json(
                Notification(
                    jsonrpc=2.0, method="change_status", params=data
                ).model_dump()
            )

    async def parse_json(self, data: str):
        """Parses an incoming JSON message from the module and validates it as a JSON-RPC message.
        Args:
            data (str): the incoming JSON message from the module
        Returns:
            Notification: the parsed and validated JSON-RPC notification message
        """

        try:
            return parse_json_rpc_message(data)
        except (ValueError, ValidationError) as e:
            await self._ws.send_json({"error": str(e)})
            logger.warning(f"Module received invalid JSON-RPC message: {e}")
