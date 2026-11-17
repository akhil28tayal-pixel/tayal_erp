"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, call, refreshAccess, session } from "./api";
import type { components } from "@/api/schema";

type CompanyAccess = components["schemas"]["CompanyAccess"];
type UserOut = components["schemas"]["UserOut"];

type AuthState = {
  status: "loading" | "anonymous" | "ready";
  user: UserOut | null;
  companies: CompanyAccess[];
  company: CompanyAccess | null;
  can: (perm: string) => boolean;
  login: (phone: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchCompany: (id: string) => void;
};

const Ctx = createContext<AuthState | null>(null);
const COMPANY_KEY = "erp.company";

function remembered(): string | null {
  try { return localStorage.getItem(COMPANY_KEY); } catch { return null; }
}
function remember(id: string) {
  try { localStorage.setItem(COMPANY_KEY, id); } catch { /* private mode: fine */ }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [user, setUser] = useState<UserOut | null>(null);
  const [companies, setCompanies] = useState<CompanyAccess[]>([]);
  const [companyId, setCompanyId] = useState<string | null>(null);

  const apply = useCallback((u: UserOut, list: CompanyAccess[]) => {
    setUser(u);
    setCompanies(list);
    const pick = list.find((c) => c.company_id === remembered()) ?? list[0] ?? null;
    session.setCompany(pick?.company_id ?? null);
    setCompanyId(pick?.company_id ?? null);
    setStatus("ready");
  }, []);

  // On load: try the HttpOnly refresh cookie to restore the session.
  useEffect(() => {
    session.onLost(() => { session.setToken(null); setStatus("anonymous"); router.replace("/login"); });
    (async () => {
      if (await refreshAccess()) {
        try {
          const me = await call(api.GET("/api/v1/auth/me"));
          apply(me.user, me.companies);
          return;
        } catch { /* fall through */ }
      }
      setStatus("anonymous");
    })();
  }, [apply, router]);

  const login = useCallback(async (phone: string, password: string) => {
    const res = await call(api.POST("/api/v1/auth/login", { body: { phone, password, use_cookie: true } }));
    session.setToken(res.access_token);
    apply(res.user, res.companies);
  }, [apply]);

  const logout = useCallback(async () => {
    try { await api.POST("/api/v1/auth/logout", {}); } finally {
      session.setToken(null);
      setUser(null);
      setStatus("anonymous");
      router.replace("/login");
    }
  }, [router]);

  const switchCompany = useCallback((id: string) => {
    remember(id);
    session.setCompany(id);
    setCompanyId(id);
  }, []);

  const company = companies.find((c) => c.company_id === companyId) ?? null;
  const value = useMemo<AuthState>(() => ({
    status, user, companies, company,
    can: (perm) => !!company?.permissions.includes(perm),
    login, logout, switchCompany,
  }), [status, user, companies, company, login, logout, switchCompany]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
