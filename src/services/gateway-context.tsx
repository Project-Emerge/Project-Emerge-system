import { createContext, useContext, useEffect, useMemo, type PropsWithChildren } from "react";
import { GatewayClient } from "./gateway-client";
import { useDashboardStore } from "../store/dashboard-store";

const GatewayContext = createContext<GatewayClient | null>(null);

export function GatewayProvider({ children }: PropsWithChildren): React.JSX.Element {
  const client = useMemo(
    () => new GatewayClient({
      onConnection: (status) => useDashboardStore.getState().setConnectionStatus(status),
      onMqttMessage: (message) => useDashboardStore.getState().ingestMqttMessage(message),
    }),
    [],
  );

  useEffect(() => {
    client.connect();
    return () => client.disconnect();
  }, [client]);

  return <GatewayContext.Provider value={client}>{children}</GatewayContext.Provider>;
}

export function useGatewayClient(): GatewayClient {
  const client = useContext(GatewayContext);
  if (!client) throw new Error("useGatewayClient must be used inside GatewayProvider");
  return client;
}
