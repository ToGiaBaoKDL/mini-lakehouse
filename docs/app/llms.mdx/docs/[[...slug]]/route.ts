import { notFound } from "next/navigation";

import { llmText, markdownUrl, source } from "@/lib/source";

export const revalidate = false;

type RouteProperties = {
  params: Promise<{ slug?: string[] }>;
};

export async function GET(_request: Request, { params }: RouteProperties) {
  const { slug } = await params;
  const page = source.getPage(slug?.slice(0, -1));
  if (!page) notFound();

  return new Response(await llmText(page), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
}

export function generateStaticParams() {
  return source.getPages().map((page) => ({
    slug: markdownUrl(page).segments,
  }));
}
