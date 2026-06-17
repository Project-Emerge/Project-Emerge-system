import asyncio
import logging
from typing import Protocol
from offloading_manager.settings import settings
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from offloading_manager.routers.schemas import OffloadingRequest, RequestResponse
from offloading_manager.routers.json_rpc_wrapper import (
    parse_json_rpc_message,
    Request,
    SuccessResponse,
    ErrorResponse,
)
from offloading_manager.type import OffloadingType, RobotID

logger = logging.getLogger("uvicorn.error")


class RobotModel(Protocol):
    async def self_offloading_request_consideration(
        self, robot_id: RobotID, offloading_type: OffloadingType, message_id: int
    ) -> None: ...


class Robot:
    def __init__(
        self, websocket: WebSocket, robot_id: RobotID, robot_model: RobotModel
    ):
        self._ws = websocket
        self.robot_id = robot_id
        self._connected = False
        self._send_request_id = 0
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._robot_model = robot_model

    async def connect(self):
        """Accepts the WebSocket connection, registers the robot in the state, and initializes its offloading state."""

        await self._ws.accept()
        self._connected = True
        logger.info(f"Robot {self.robot_id} connected")

    async def disconnect(self):
        """Closes the WebSocket connection, removes the robot from the state, and cancels any pending requests."""

        self._connected = False
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()
        logger.info(f"Robot {self.robot_id} disconnected")

    async def listen(self):
        """Listens for incoming messages from the robot, processes them, and handles disconnections and errors."""

        try:
            while True:
                data = await self._ws.receive_text()
                mesage = await self._parse_json(data)
                if isinstance(mesage, Request):
                    await self._arrived_request_valutation(mesage)
                elif isinstance(mesage, SuccessResponse):
                    await self._arrived_response_valutation(mesage)
                elif isinstance(mesage, ErrorResponse):
                    await self._arrived_error_valutation(mesage)
        except WebSocketDisconnect:
            logger.info(f"Robot {self.robot_id} disconnected")
            await self.disconnect()
        except Exception as e:
            logger.error(f"Robot {self.robot_id} unexpected error: {e}", exc_info=True)

    async def change_status_request(self, data: OffloadingRequest) -> bool | None:
        """Sends a request to change the offloading status to the robot and waits for a response.
        Args:
            data (OffloadingRequest): the new offloading status to set for the robot
        Returns:
            bool | None: the result of the request, or None if the request failed or timed out
        """

        if self._connected:
            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()
            current_id = self._send_request_id
            self._send_request_id += 1
            self._pending_requests[current_id] = future
            await self._ws.send_json(
                Request(
                    jsonrpc=2.0, method="change_status", params=data, id=current_id
                ).model_dump()
            )
            logger.info(
                f"Robot {self.robot_id} sending change_status request id={current_id}"
            )
            try:
                result = await asyncio.wait_for(
                    future, timeout=settings.robot_response_timeout
                )  
                return result.success
            except asyncio.TimeoutError:
                logger.warning(
                    f"Robot {self.robot_id} request id={current_id} timed out"
                )
                self._pending_requests.pop(current_id, None)
                return False

    async def respond(self, data: bool, message_id: int):
        """Sends a response to the robot.
        Args:
            data (bool): the result of the operation
            message_id (int): the ID of the message to respond to
        """

        if self._connected:
            await self._ws.send_json(
                SuccessResponse(jsonrpc=2.0, result=data, id=message_id).model_dump()
            )

    async def _arrived_request_valutation(self, mesage: Request):
        """Processes an incoming request from the robot, validates it, and triggers the appropriate offloading consideration.
        Args:
            mesage (Request): the incoming request message from the robot
        """

        try:
            offloading_request = OffloadingRequest.model_validate(mesage.params)
            await self._robot_model.self_offloading_request_consideration(
                self.robot_id, offloading_request.type, mesage.id
            )
        except ValidationError:
            await self._ws.send_json(
                ErrorResponse(
                    jsonrpc=2.0,
                    error={"code": -32700, "message": "Parse error"},
                    id=mesage.id,
                ).model_dump()
            )

    async def _arrived_response_valutation(self, mesage: SuccessResponse):
        """Processes an incoming response from the robot, matches it to the corresponding pending request, and sets the result of the future.
        Args:
            mesage (SuccessResponse): the incoming response message from the robot
        """

        future = self._pending_requests.pop(mesage.id, None)
        if future and not future.done():
            future.set_result(RequestResponse.model_validate(mesage.result))

    async def _arrived_error_valutation(self, mesage: ErrorResponse):
        """Processes an incoming error response from the robot, matches it to the corresponding pending request, and sets the exception for the future.
        Args:
            mesage (ErrorResponse): the incoming error response message from the robot
        """

        if mesage.id in self._pending_requests:
            future = self._pending_requests.pop(mesage.id, None)
            if future and not future.done():
                future.set_exception(
                    Exception(mesage.error.get("message", "Unknown error"))
                )

    async def _parse_json(self, data: str):
        """Parses an incoming JSON message from the robot and validates it as a JSON-RPC message.
        Args:
            data (str): the incoming JSON message from the robot
        Returns:
            Request | SuccessResponse | ErrorResponse: the parsed and validated JSON-RPC message
        """

        try:
            return parse_json_rpc_message(data)
        except (ValueError, ValidationError) as e:
            await self._ws.send_json({"error": str(e)})
