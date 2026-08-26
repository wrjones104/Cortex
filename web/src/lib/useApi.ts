import { createContext, useContext } from "react";
import type { CortexApi } from "./api";

interface ApiContextValue {
  api: CortexApi;
  baseUrl: string;
  /** Who is signed in, from the stored session. Screens use it to label things. */
  account: { username: string; displayName: string; isOwner: boolean };
  /** Forget the session and return to sign-in. Also fires on any 401. */
  disconnect: () => void;
}

export const ApiContext = createContext<ApiContextValue | null>(null);

export function useApi(): ApiContextValue {
  const value = useContext(ApiContext);
  if (!value) throw new Error("useApi used outside the connected app");
  return value;
}
