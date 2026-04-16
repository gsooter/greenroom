import type { Metadata } from "next";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "Greenroom — DC Concert Calendar",
  description:
    "Washington DC's concert calendar with Spotify-powered recommendations. Aggregates shows from all major DC venues nightly.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
