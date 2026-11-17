"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Shell } from "@/components/shell";
import { Loading } from "@/components/ui";
import { useAuth } from "@/lib/auth";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { status, company } = useAuth();
  const router = useRouter();
  useEffect(() => { if (status === "anonymous") router.replace("/login"); }, [status, router]);
  if (status !== "ready") return <Loading what="Opening" />;
  if (!company) {
    return <div className="p-10 text-center">Your account has no company access yet. Ask an admin to assign a role.</div>;
  }
  // key=company remounts pages on company switch so no data from the previous company lingers on screen.
  return <Shell><div key={company.company_id}>{children}</div></Shell>;
}
