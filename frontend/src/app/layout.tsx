import type { Metadata } from "next";
import { JetBrains_Mono, Space_Grotesk } from "next/font/google";
import "./globals.css";

import { AppShell } from "@/components/shell/AppShell";
import { Providers } from "@/lib/providers";

// Space Grotesk for prose and headings, JetBrains Mono for anything an
// analyst might read back to someone: an id, a probability, a log-odds.
const sans = Space_Grotesk({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "Sentinel — Fraud Investigation Console",
  description: "Agentic fraud investigation on TigerGraph: evidence, uncertainty and next best action.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <body className="min-h-dvh bg-bg text-fg antialiased">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
