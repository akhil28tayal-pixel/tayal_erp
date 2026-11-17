import "@fontsource-variable/archivo/wdth.css";
import "./globals.css";
import type { Metadata } from "next";
import { Providers } from "./providers";

export const metadata: Metadata = { title: "Tayal ERP", description: "Orders, stock, dispatch and collections" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-IN">
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
