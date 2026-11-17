"use client";

import { createContext, useCallback, useContext, useState } from "react";

type Toast = { id: number; tone: "ok" | "bad"; text: string };
const Ctx = createContext<(tone: Toast["tone"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, tone, text }]);
    setTimeout(() => setItems((xs) => xs.filter((t) => t.id !== id)), tone === "bad" ? 7000 : 3500);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id}
               className={`max-w-sm rounded-[6px] border-l-4 bg-ink px-4 py-3 text-[13px] text-white shadow-lg ${
                 t.tone === "ok" ? "border-ok" : "border-bad"}`}>
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
