import type { ReactNode } from "react";
import { DocsLayout } from "fumadocs-ui/layouts/docs";

import { baseOptions } from "@/lib/layout";
import { source } from "@/lib/source";

export default function Documentation({ children }: { children: ReactNode }) {
  return (
    <DocsLayout
      {...baseOptions()}
      sidebar={{ defaultOpenLevel: 0, prefetch: false }}
      tree={source.getPageTree()}
    >
      {children}
    </DocsLayout>
  );
}
