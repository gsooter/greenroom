import type { Metadata } from "next";

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
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
