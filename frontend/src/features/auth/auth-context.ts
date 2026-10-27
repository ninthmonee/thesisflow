import { createContext, useContext } from "react";
import type {
  AuthSessionStatus,
  AuthLoginRequest,
  AuthRegisterRequest,
  AuthSetupRequest,
  AuthPasswordChange,
} from "../../shared/api/generated/models";

export interface AuthContextValue {
  session: AuthSessionStatus | undefined;
  phase: "checking" | "ready" | "transition" | "error";
  error: string | undefined;
  notice: string | undefined;
  manualLogout: boolean;
  login: (body: AuthLoginRequest) => Promise<void>;
  register: (body: AuthRegisterRequest) => Promise<void>;
  setup: (body: AuthSetupRequest) => Promise<void>;
  logout: () => Promise<void>;
  logoutAll: () => Promise<void>;
  changePassword: (body: AuthPasswordChange) => Promise<void>;
  refresh: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function useAuth() {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("AuthProvider is missing");
  return auth;
}
