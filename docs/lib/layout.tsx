import { DatabaseZap } from "lucide-react";
import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";

import { githubUrl, site } from "@/lib/site";

export function baseOptions(): BaseLayoutProps {
  return {
    githubUrl,
    nav: {
      title: (
        <span className="flex items-center gap-2 font-semibold">
          <DatabaseZap className="size-5" />
          {site.name}
        </span>
      ),
      url: "/",
    },
    themeSwitch: {
      enabled: true,
      mode: "light-dark-system",
    },
  };
}
