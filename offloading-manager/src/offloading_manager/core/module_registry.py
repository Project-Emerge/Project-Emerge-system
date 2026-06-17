from offloading_manager.type import ModuleType, Stats
from offloading_manager.routers.module.module import Module


class ModuleRegistry:
    def __init__(self):
        self._module_connections: dict[ModuleType, Module] = {}
        self._module_stats: dict[ModuleType, Stats] = {}

    def add_module_connection(self, module_type: ModuleType, module: Module) -> None:
        """Add a module connection to the state
        Args:
            module_type (ModuleType): the type of the module
            module (Module): the module connection to add
        """

        self._module_connections[module_type] = module

    def get_module_connection(self, module_type: ModuleType) -> Module | None:
        """Get a module connection from the state
        Args:
            module_type (ModuleType): the type of the module
        Returns:
            Optional[Module]: the module connection, or None if not found
        """

        return self._module_connections.get(module_type)

    def remove_module_connection(self, module_type: ModuleType) -> None:
        """Remove a module connection from the state
        Args:
            module_type (ModuleType): the type of the module
        """

        self._module_connections.pop(module_type, None)

    # ------------------- STATS ------------------- #

    def update_module_stats(self, module_type: ModuleType, stats: Stats) -> None:
        """Update the saved stats of a module
        Args:
            module_type (ModuleType): the type of the module
            stats (Stats): the new stats to save for the module
        """

        self._module_stats[module_type] = stats

    def get_modules_stats(self) -> dict[ModuleType, Stats]:
        """Get the saved stats of all modules
        Returns:
            dict[ModuleType, Stats]: a dictionary mapping module types to their saved stats
        """

        return self._module_stats
