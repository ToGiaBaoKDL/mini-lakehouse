import type { Metadata } from "next";
import { notFound } from "next/navigation";
import {
  DocsBody,
  DocsDescription,
  DocsPage,
  DocsTitle,
  MarkdownCopyButton,
  PageLastUpdate,
  ViewOptionsPopover,
} from "fumadocs-ui/layouts/docs/page";
import { createRelativeLink } from "fumadocs-ui/mdx";

import { getMDXComponents } from "@/components/mdx";
import { markdownUrl, source } from "@/lib/source";
import { githubUrl, site } from "@/lib/site";

type PageProperties = {
  params: Promise<{ slug?: string[] }>;
};

export default async function Page({ params }: PageProperties) {
  const { slug } = await params;
  const page = source.getPage(slug);
  if (!page) notFound();

  const Content = page.data.body;
  const rawUrl = markdownUrl(page).url;

  return (
    <DocsPage toc={page.data.toc} full={page.data.full}>
      <DocsTitle>{page.data.title}</DocsTitle>
      <DocsDescription className="mb-0">
        {page.data.description}
      </DocsDescription>
      <div className="flex items-center gap-2 border-b pb-6">
        <MarkdownCopyButton markdownUrl={rawUrl} />
        <ViewOptionsPopover
          githubUrl={`${githubUrl}/blob/${site.githubBranch}/docs/content/docs/${page.path}`}
          markdownUrl={rawUrl}
        />
      </div>
      <DocsBody>
        <Content
          components={getMDXComponents({
            a: createRelativeLink(source, page),
          })}
        />
      </DocsBody>
      {page.data.lastModified ? (
        <PageLastUpdate date={page.data.lastModified} />
      ) : null}
    </DocsPage>
  );
}

export function generateStaticParams() {
  return source.generateParams();
}

export async function generateMetadata({
  params,
}: PageProperties): Promise<Metadata> {
  const { slug } = await params;
  const page = source.getPage(slug);
  if (!page) notFound();

  return {
    description: page.data.description,
    title: page.data.title,
  };
}
