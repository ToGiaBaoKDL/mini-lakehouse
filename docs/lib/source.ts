import { docs } from "collections/server";
import { loader } from "fumadocs-core/source";

import { site } from "@/lib/site";

export const source = loader({
  baseUrl: site.docsPath,
  source: docs.toFumadocsSource(),
});

export function markdownUrl(page: (typeof source)["$inferPage"]) {
  const segments = [...page.slugs, "content.md"];

  return {
    segments,
    url: `/llms.mdx/docs/${segments.join("/")}`,
  };
}

export async function llmText(page: (typeof source)["$inferPage"]) {
  const processed = await page.data.getText("processed");

  return `# ${page.data.title} (${site.url}${page.url})\n\n${processed}`;
}
