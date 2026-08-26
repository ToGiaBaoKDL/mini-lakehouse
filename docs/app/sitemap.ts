import type { MetadataRoute } from "next";

import { source } from "@/lib/source";
import { site } from "@/lib/site";

export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { lastModified: new Date(), url: site.url },
    ...source.getPages().map((page) => ({
      lastModified: page.data.lastModified ?? new Date(),
      url: `${site.url}${page.url}`,
    })),
  ];
}
