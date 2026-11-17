# Tayal ERP on the Windows Tally PC — reachable at https://erp.tayaltraders.co.in

The ERP runs on the same PC as Tally. Cloudflare Tunnel makes it reachable from anywhere over HTTPS **without opening
anything on the router**: the PC connects out to Cloudflare, and Cloudflare forwards `erp.tayaltraders.co.in` to it. The database,
the ERP's internal ports and Tally's port 9000 stay private to the PC.

```
phone / browser ──https──▶ Cloudflare ──tunnel──▶ Tally PC: cloudflared ─▶ web :3000 ─▶ server :8000 ─▶ PostgreSQL
                                                                                         └──────────▶ Tally :9000
```

Time needed: about an hour, most of it waiting for installs.

---

## A. Cloudflare (once, in the browser — about 15 minutes)

1. **Put tayaltraders.co.in on Cloudflare** (skip if it already is).
   Sign up free at dash.cloudflare.com → *Add a domain* → `tayaltraders.co.in` → Free plan. Cloudflare copies your existing DNS
   records — **check that the website and email (MX) records it found are all there** before going on. It then shows
   two nameservers: enter them at the company where you bought tayaltraders.co.in (replacing the old ones). It can take from a
   few minutes to a few hours to become *Active*.
2. **Create the tunnel.** Cloudflare dashboard → **Networking** → **Tunnels** → **Create a tunnel** → name it
   `tayal-erp` → **Create Tunnel**. Choose **Windows**. In the install command it shows
   (`cloudflared.exe service install eyJ…`), copy only the long **token** — the text starting `eyJ` — and keep it private.
   You don't need to run that command: `install.ps1` does it. Select **Continue** (it may say "not connected" until
   the PC is set up — that's fine).
3. **Route the address to the ERP.** Open the tunnel → **Routes** tab → **Add route** → **Published application**:
   Subdomain `erp`, Domain `tayaltraders.co.in`, Service URL `http://127.0.0.1:3000` → **Add route**
   (use `127.0.0.1`, not `localhost`: on Windows "localhost" can point to a different address).
4. Under *Security* → *Bots*, leave **Bot Fight Mode off** (it blocks WhatsApp's messages to the ERP).

## B. On the Mac (5 minutes)

```bash
cd ~/CascadeProjects/tayal_erp
./deploy/mac-export-for-windows.sh
```

This makes **TayalERP-for-windows.zip** on the Desktop: the code, a copy of the database and your settings (`.env`,
which holds the WhatsApp token). Copy it to the Windows PC with a USB stick or Google Drive.
It contains passwords and all your data — delete other copies once it is on the PC.

## C. On the Windows PC (about 40 minutes)

1. Right-click the zip → *Extract All…* → to `C:\` so that you have **C:\TayalERP**.
2. Start → type *PowerShell* → right-click **Windows PowerShell** → **Run as administrator**, then:

```powershell
cd C:\TayalERP
powershell -ExecutionPolicy Bypass -File .\deploy\windows\install.ps1 -TunnelToken "PASTE-THE-TOKEN" -DataFile .\data\tayal_erp.dump
```

   If PostgreSQL is already installed on this PC, add `-PgSuperPassword "its postgres password"`.

   It installs PostgreSQL, Python, Node.js, cloudflared and NSSM, restores your data, builds the dashboard, sets up
   the services and the tunnel, points the ERP at Tally on this PC (`http://127.0.0.1:9000`) and stops the PC from
   sleeping. It prints green lines as each step finishes. If it stops with a red message, fix that and run the same
   command again — finished steps are kept.

3. Open **https://erp.tayaltraders.co.in** from your phone (mobile data, not the office Wi-Fi) and sign in as usual.
   Use this address on the office computers too, including the Tally PC itself: sign-in stays secure only over https.

## D. Afterwards

- **Stop the ERP on the Mac** (close the `start-mac.sh` window or restart the Mac) so the two don't both sync with
  Tally and both send WhatsApp messages. Keep the Mac's copy as a fallback for a few days.
- **Phone app:** sign out → on the sign-in screen set the server address to `https://erp.tayaltraders.co.in`.
- **WhatsApp orders from dealers (optional):** Meta webhook callback URL = `https://erp.tayaltraders.co.in/api/v1/whatsapp/webhook`
  (no Tailscale step needed any more). Add `ERP_WHATSAPP_VERIFY_TOKEN` and `ERP_WHATSAPP_APP_SECRET` to
  `C:\TayalERP\backend\.env`, then restart the server: `C:\TayalERP\tools\nssm.exe restart TayalERP-API`.
- **Tally** must be open with its XML server on port 9000 (as now). The ERP talks to it on the same PC.

## Daily running

| What | How |
|------|-----|
| Is everything OK? | `powershell -File C:\TayalERP\deploy\windows\status.ps1` |
| Restart the ERP | `C:\TayalERP\tools\nssm.exe restart TayalERP-API` and `… restart TayalERP-Web` |
| Logs | `C:\TayalERP\logs` |
| Backups | `C:\TayalERP\backups`, every night at 23:30, last 30 days. **Copy them off the PC**: install *Google Drive for desktop* → Preferences → *Add folder* → `C:\TayalERP\backups` → *Sync with Google Drive*. |
| New version | unzip the new bundle over `C:\TayalERP`, then run `deploy\windows\update.ps1` as administrator (takes a backup first) |
| Settings / secrets | `C:\TayalERP\backend\.env` (only administrators can open it) |

Everything starts by itself when the PC boots — no one needs to log in. Keep the PC on a **UPS**, and in
*Settings → Windows Update → Advanced options → Active hours* set office hours so updates restart it only at night.

## If something is wrong

- **https://erp.tayaltraders.co.in shows a Cloudflare error 1033 / 502:** the PC is off, or the tunnel/web service is stopped —
  run `status.ps1`. For 502, check `logs\TayalERP-Web.log`.
- **Signs in but Tally data is missing:** Tally is closed or its port isn't 9000 — `status.ps1` shows "Tally not answering".
- **Restore from a backup:** `"C:\Program Files\PostgreSQL\16\bin\pg_restore.exe" -h 127.0.0.1 -U postgres --clean --no-owner --role=tayal_erp -d tayal_erp C:\TayalERP\backups\<file>.dump`
  (stop both TayalERP services first).
