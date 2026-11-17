"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";

export default function LoginPage() {
  const { login, status } = useAuth();
  const router = useRouter();
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (status === "ready") router.replace("/overview"); }, [status, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(phone.trim(), password);
      router.replace("/overview");
    } catch (err) {
      setError(err instanceof ApiError ? err.problem.detail : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen md:grid-cols-[1fr_440px]">
      <section className="relative hidden overflow-hidden bg-blue p-12 text-white md:flex md:flex-col md:justify-end">
        <span aria-hidden className="absolute left-0 top-0 h-2 w-full"
              style={{ background: "repeating-linear-gradient(-45deg,#FFFFFF 0 18px,#143CA6 18px 36px)" }} />
        <p className="display max-w-md text-[40px] font-bold leading-[1.05]">Order to delivery to payment, in one place.</p>
        <p className="mt-4 max-w-sm text-[15px] text-white/65">
          Ishika Traders Daily — cement and TMT across Delhi and Gurugram godowns.
        </p>
      </section>
      <section className="flex items-center justify-center px-6 py-12">
        <form onSubmit={submit} className="w-full max-w-[340px]" noValidate>
          <h1 className="display text-[24px] font-bold">Sign in</h1>
          <p className="mb-6 mt-1 text-[13.5px] text-steel">Use the phone number your admin registered.</p>
          <label className="label" htmlFor="phone">Phone number</label>
          <input id="phone" className="input mb-4" inputMode="numeric" autoComplete="username" required
                 value={phone} onChange={(e) => setPhone(e.target.value)} />
          <label className="label" htmlFor="password">Password</label>
          <input id="password" type="password" className="input" autoComplete="current-password" required
                 value={password} onChange={(e) => setPassword(e.target.value)} />
          {error && <p role="alert" className="mt-3 text-[13px] text-bad">{error}</p>}
          <button className="btn-primary mt-6 w-full justify-center" disabled={busy || !phone || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </div>
  );
}
