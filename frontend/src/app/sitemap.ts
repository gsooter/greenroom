/** Dynamic sitemap generation for all events and venues. */
import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  // TODO: Fetch all events and venues from the API
  return [
    {
      url: `${process.env.NEXT_PUBLIC_BASE_URL}`,
      lastModified: new Date(),
      changeFrequency: "weekly",
      priority: 1.0,
    },
    {
      url: `${process.env.NEXT_PUBLIC_BASE_URL}/events`,
      lastModified: new Date(),
      changeFrequency: "daily",
      priority: 0.8,
    },
    {
      url: `${process.env.NEXT_PUBLIC_BASE_URL}/venues`,
      lastModified: new Date(),
      changeFrequency: "weekly",
      priority: 0.8,
    },
  ];
}
