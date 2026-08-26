"use client";

import { use, useId, useSyncExternalStore } from "react";
import { useTheme } from "next-themes";

const cache = new Map<string, Promise<unknown>>();

function cached<T>(key: string, load: () => Promise<T>): Promise<T> {
  const value = cache.get(key);
  if (value) return value as Promise<T>;

  const promise = load();
  cache.set(key, promise);
  return promise;
}

function Diagram({ chart }: { chart: string }) {
  const id = useId();
  const { resolvedTheme } = useTheme();
  const { default: mermaid } = use(cached("mermaid", () => import("mermaid")));
  const theme = resolvedTheme === "dark" ? "dark" : "default";

  mermaid.initialize({
    fontFamily: "inherit",
    securityLevel: "strict",
    startOnLoad: false,
    theme,
  });

  const { bindFunctions, svg } = use(
    cached(`${chart}-${theme}`, () =>
      mermaid.render(id, chart.replaceAll("\\n", "\n")),
    ),
  );

  return (
    <div
      className="my-6 overflow-x-auto rounded-xl border bg-fd-card p-4 [&_svg]:mx-auto"
      ref={(element) => {
        if (element) bindFunctions?.(element);
      }}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

export function Mermaid({ chart }: { chart: string }) {
  const mounted = useSyncExternalStore(
    () => () => undefined,
    () => true,
    () => false,
  );

  if (!mounted) {
    return (
      <div className="my-6 h-48 animate-pulse rounded-xl border bg-fd-muted" />
    );
  }

  return <Diagram chart={chart} />;
}
