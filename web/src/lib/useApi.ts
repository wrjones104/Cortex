import { createContext, useContext } from "react";
import type { CortexApi } from "./api";

interface ApiContextValue {
  api: CortexApi;
  baseUrl: string;
  disconnect: () => void;
}

export const ApiContext = createContext<ApiContextValue | null>(null);

export function useApi(): ApiContextValue {
  const value = useContext(ApiContext);
  if (!value) throw new Error("useApi used outside the connected app");
  return value;
}
