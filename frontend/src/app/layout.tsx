import type { Metadata, Viewport } from "next";

import AppShell from "@/components/layout/AppShell";
import { AppProviders } from "@/components/providers/AppProviders";
import { brandColors } from "@/lib/brand";
import { config } from "@/lib/config";
import "@/styles/globals.css";

const DESCRIPTION =
  "The DMV's concert calendar with Spotify-powered recommendations. Shows across DC, Maryland, and Virginia, updated nightly.";

export const metadata: Metadata = {
  metadataBase: new URL(config.baseUrl),
  title: {
    default: "Greenroom — DMV Concert Calendar",
    template: "%s · Greenroom",
  },
  description: DESCRIPTION,
  appleWebApp: {
    capable: true,
    title: "Greenroom",
    statusBarStyle: "default",
  },
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180" }],
  },
};

export const viewport: Viewport = {
  themeColor: brandColors.greenDark,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <AppProviders>
          <AppShell>{children}</AppShell>
        </AppProviders>
      </body>
    </html>
  );
}
