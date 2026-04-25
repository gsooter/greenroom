/**
 * robots.txt generation — explicitly welcomes AI crawlers.
 *
 * Per CLAUDE.md, the site is designed for AI discoverability. The
 * sitemap URL is pulled from `config.baseUrl` so the value stays
 * consistent with metadata and canonical links.
 */

import type { MetadataRoute } from "next";

import { config } from "@/lib/config";

export default function robots(): MetadataRoute.Robots {
  const base = config.baseUrl.replace(/\/$/, "");
  return {
    rules: [
      { userAgent: "*", allow: "/", disallow: ["/admin", "/admin/"] },
      { userAgent: "GPTBot", allow: "/", disallow: ["/admin", "/admin/"] },
      { userAgent: "ClaudeBot", allow: "/", disallow: ["/admin", "/admin/"] },
      { userAgent: "PerplexityBot", allow: "/", disallow: ["/admin", "/admin/"] },
      { userAgent: "GoogleBot", allow: "/", disallow: ["/admin", "/admin/"] },
    ],
    sitemap: `${base}/sitemap.xml`,
  };
}
