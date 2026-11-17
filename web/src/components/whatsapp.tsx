"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useToast } from "@/components/toast";

/** Opens WhatsApp (app or WhatsApp Web) with the dealer's balance message filled in; the user presses send.
 *  The window is opened inside the click itself so the browser does not block it as a pop-up. */
export function WhatsAppBalanceButton({ dealerId, dealerName, number, disabled, compact }: {
  dealerId: string; dealerName: string; number?: string | null; disabled?: boolean; compact?: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  // CLOUD_API sends from the server (nothing to open); CLICK_TO_CHAT opens WhatsApp with the text typed in.
  const summary = useQuery({ queryKey: ["collections", "summary"], queryFn: () => call(api.GET("/api/v1/collections/summary")) });
  const automatic = summary.data?.whatsapp_provider === "CLOUD_API";
  const send = useMutation({
    mutationFn: ({ confirm }: { confirm: boolean; win: Window | null }) =>
      call(api.POST("/api/v1/dealers/{dealer_id}/whatsapp/balance", { params: { path: { dealer_id: dealerId } },
        body: { number: number ?? null, confirm_repeat: confirm } })),
    onSuccess: (r, { win }) => {
      qc.invalidateQueries({ queryKey: ["collections"] });
      if (!r.url) {
        win?.close();
        toast("ok", `Balance sent to ${dealerName} on WhatsApp (+${r.number})`);
        return;
      }
      if (win && !win.closed) win.location.href = r.url;
      else window.open(r.url, "_blank");
      toast("ok", `WhatsApp opened for ${dealerName} — press send there`);
    },
    onError: (e, { win }) => {
      if (e instanceof ApiError && e.problem.code === "RECENTLY_REMINDED" && window.confirm(e.problem.detail)) {
        send.mutate({ confirm: true, win });
        return;
      }
      win?.close();
      toast("bad", e instanceof ApiError ? e.problem.detail : "Could not prepare the message");
    },
  });
  const click = () => {
    if (automatic) return send.mutate({ confirm: false, win: null });
    const win = window.open("", "_blank");
    if (win) win.document.write("<p style='font:15px sans-serif;padding:24px'>Opening WhatsApp…</p>");
    send.mutate({ confirm: false, win });
  };
  return (
    <button type="button" onClick={click} disabled={disabled || send.isPending}
            className={`inline-flex items-center gap-1.5 rounded-[6px] bg-[#1f8f4e] px-3 font-semibold text-white hover:bg-[#187a42] disabled:opacity-40 ${compact ? "h-8 text-[12.5px]" : "h-9 text-[13.5px]"}`}
            title={disabled ? "No mobile number" : automatic ? `Send balance to ${dealerName} now (automatic)` : `Open WhatsApp with ${dealerName}'s balance`}>
      <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="currentColor"><path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2Zm0 18.2a8.2 8.2 0 0 1-4.2-1.2l-.3-.2-3 .8.8-2.9-.2-.3A8.2 8.2 0 1 1 12 20.2Zm4.5-6.1c-.2-.1-1.5-.7-1.7-.8-.2-.1-.4-.1-.6.1l-.8 1c-.1.2-.3.2-.5.1a6.7 6.7 0 0 1-3.3-2.9c-.2-.4.2-.4.7-1.3.1-.2 0-.3 0-.4l-.8-1.8c-.2-.5-.4-.4-.6-.4h-.5a1 1 0 0 0-.7.3 3 3 0 0 0-.9 2.2 5.2 5.2 0 0 0 1.1 2.7 11.8 11.8 0 0 0 4.5 4c1.7.7 2.3.8 3.2.6.5-.1 1.5-.6 1.7-1.2.2-.6.2-1.1.1-1.2l-.4-.2Z"/></svg>
      {send.isPending ? (automatic ? "Sending…" : "Opening…") : compact ? "WhatsApp" : "Send balance on WhatsApp"}
    </button>
  );
}
