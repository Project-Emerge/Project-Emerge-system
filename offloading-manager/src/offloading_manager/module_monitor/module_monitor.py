import asyncio
from typing import Protocol
import aiodocker
from offloading_manager.type import ModuleType, Stats
import logging
from offloading_manager.settings import settings

logger = logging.getLogger("uvicorn.error")


DOCKER_NAME: dict[str, ModuleType] = {
    "project-emerge-aruco-detector": ModuleType.ARUCO,
    "project-emerge-aggregate-runtime": ModuleType.AGGREGATE,
    "project-emerge-neighborhood-system": ModuleType.NEIGHBOR,
}


class ModuleMonitorModel(Protocol):
    def update_module_stats(self, module_type: ModuleType, stats: Stats) -> None: ...
    async def stats_valutation(self) -> None: ...


class ModuleMonitor:
    def __init__(
        self,
        model: ModuleMonitorModel,
        network: str = settings.containers_network,
        interval: float = settings.containers_polling_interval,
    ):
        self.model = model
        self.network = network
        self.interval = interval
        self._running = False
        self._client: aiodocker.Docker

    async def start(self):
        """Starts the Docker monitor, which periodically checks the stats of the relevant containers and updates the system state accordingly."""

        self._running = True
        async with aiodocker.Docker() as client:
            self._client = client
            while self._running:
                await self._tick()
                await asyncio.sleep(self.interval)
        logger.info("ModuleMonitor started")

    def stop(self):
        """Stops the Module monitor."""

        self._running = False
        logger.info("ModuleMonitor stopped")

    async def _get_containers(self):
        """Retrieves the list of Docker containers that are connected to the specified network and are relevant for monitoring.
        Returns:
            list[Container]: a list of containers that are connected to the specified network and are relevant for monitoring
        """

        containers = await self._client.containers.list()
        result = []
        for c in containers:
            info = await c.show()
            if self.network in info["NetworkSettings"]["Networks"]:
                result.append((c, info))
        return result

    async def _tick(self):
        """Operation performed at each tick of the monitoring cycle."""

        containers = await self._get_containers()
        for container, info in containers:
            name = info["Name"].lstrip("/")
            if name in DOCKER_NAME:
                stats = await self._get_stats(container)
                self.model.update_module_stats(DOCKER_NAME[name], stats)
        await self.model.stats_valutation()
        logger.debug(f"DockerMonitor: found {len(containers)} containers")

    async def _get_stats(self, container) -> Stats:
        """Retrieves the current stats of a given container and converts them into a Stats object.
        Args:
            container: the Docker container for which to retrieve the stats
        Returns:
            Stats: the current stats of the container, including CPU and memory usage
        """

        raw = await container.stats(stream=False)
        if isinstance(raw, list):
            raw = raw[0]
        return Stats(
            cpu_usage=self._cpu_percent(raw),
            memory_usage=self._mem_percent(raw),
        )

    @staticmethod
    def _cpu_percent(stats: dict) -> float:
        """Calculates the CPU usage percentage from the raw stats of a container.
        Args:
            stats (dict): the raw stats of the container, as returned by the Docker API
        Returns:
            float: the calculated CPU usage percentage
        """

        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            stats["cpu_stats"]["system_cpu_usage"]
            - stats["precpu_stats"]["system_cpu_usage"]
        )
        num_cpus = stats["cpu_stats"]["online_cpus"]
        if system_delta == 0:
            return 0.0
        return (cpu_delta / system_delta) * num_cpus * 100

    @staticmethod
    def _mem_percent(stats: dict) -> float:
        """Calculates the memory usage percentage from the raw stats of a container.
        Args:
            stats (dict): the raw stats of the container, as returned by the Docker API
        Returns:
            float: the calculated memory usage percentage
        """
        usage = stats["memory_stats"]["usage"]
        cache = stats["memory_stats"].get("stats", {}).get("cache", 0)
        limit = stats["memory_stats"]["limit"]
        return ((usage - cache) / limit) * 100
