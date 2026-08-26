import type { Metadata } from "next";
import type { ReactNode } from "react";
import { RootProvider } from "fumadocs-ui/provider/next";

import Search from "@/components/search";
import { site } from "@/lib/site";

import "./globals.css";

export const metadata: Metadata = {
  description: site.description,
  metadataBase: new URL(site.url),
  title: {
    default: `${site.name} Documentation`,
    template: `%s | ${site.name}`,
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link href="/icon.svg" rel="icon" type="image/svg+xml" />
      </head>
      <body className="flex min-h-screen flex-col">
        <RootProvider search={{ SearchDialog: Search }}>
          {children}
        </RootProvider>
      </body>
    </html>
  );
}
