import {
  ArrowRight,
  Boxes,
  CloudCog,
  GitBranch,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";
import Link from "next/link";

const platformPlanes = [
  {
    label: "Data plane",
    services: "S3 · Glue · Athena · EMR Serverless",
    target: "AWS",
  },
  {
    label: "Service plane",
    services: "Airflow · Lightdash · SigNoz · ArXiv Lens",
    target: "OCI",
  },
  {
    label: "Elastic compute",
    services: "GLM-OCR GPU execution",
    target: "Modal",
  },
  {
    label: "Delivery",
    services: "Immutable artifacts · short-lived identities",
    target: "GitHub",
  },
] as const;

const entryPoints = [
  {
    description: "Install the toolchain and run the local validation boundary.",
    href: "/docs/getting-started",
    icon: TerminalSquare,
    title: "Build locally",
  },
  {
    description:
      "Trace data, trust, ownership, and delivery across the system.",
    href: "/docs/architecture",
    icon: Boxes,
    title: "Read the architecture",
  },
  {
    description: "Deploy, observe, recover, and diagnose the running platform.",
    href: "/docs/operations/deployment",
    icon: ShieldCheck,
    title: "Operate safely",
  },
] as const;

export default function HomePage() {
  return (
    <main className="relative flex flex-1 flex-col overflow-hidden">
      <div className="platform-grid pointer-events-none absolute inset-x-0 top-0 h-[38rem] opacity-45" />

      <section className="relative mx-auto grid w-full max-w-7xl gap-12 px-6 py-16 md:py-24 lg:grid-cols-[1.15fr_0.85fr] lg:items-center">
        <div className="max-w-3xl">
          <p className="mb-5 font-mono text-sm font-medium text-fd-primary">
            mini-lakehouse / documentation
          </p>
          <h1 className="text-balance text-4xl font-bold tracking-[-0.04em] md:text-6xl">
            One operating manual for the whole platform.
          </h1>
          <p className="mt-6 max-w-2xl text-pretty text-lg leading-8 text-fd-muted-foreground">
            Current architecture, ownership boundaries, delivery paths, and
            day-two procedures—kept next to the code they describe.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              className="inline-flex items-center gap-2 rounded-lg bg-fd-primary px-5 py-2.5 font-medium text-fd-primary-foreground shadow-sm transition-opacity hover:opacity-90"
              href="/docs"
            >
              Open documentation <ArrowRight className="size-4" />
            </Link>
            <Link
              className="inline-flex items-center gap-2 rounded-lg border bg-fd-background/80 px-5 py-2.5 font-medium backdrop-blur transition-colors hover:bg-fd-accent"
              href="https://github.com/ToGiaBaoKDL/mini-lakehouse"
            >
              <GitBranch className="size-4" /> Repository
            </Link>
          </div>
        </div>

        <div className="rounded-2xl border bg-fd-card/90 p-2 shadow-xl shadow-fd-primary/5 backdrop-blur">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <div className="flex items-center gap-2 font-medium">
              <CloudCog className="size-4 text-fd-primary" /> Platform boundary
            </div>
            <span className="font-mono text-xs text-fd-muted-foreground">
              dev
            </span>
          </div>
          <div className="divide-y">
            {platformPlanes.map(({ label, services, target }) => (
              <div className="grid gap-1 px-4 py-4" key={label}>
                <div className="flex items-center justify-between gap-4">
                  <span className="text-sm font-medium">{label}</span>
                  <span className="rounded-md border bg-fd-background px-2 py-0.5 font-mono text-xs text-fd-primary">
                    {target}
                  </span>
                </div>
                <p className="text-sm text-fd-muted-foreground">{services}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="relative border-t bg-fd-background/70">
        <div className="mx-auto w-full max-w-7xl px-6 py-12 md:py-16">
          <div className="mb-7">
            <p className="font-mono text-xs uppercase tracking-[0.18em] text-fd-muted-foreground">
              Start from the task
            </p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">
              Find the shortest path to the answer.
            </h2>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            {entryPoints.map(({ description, href, icon: Icon, title }) => (
              <Link
                className="group rounded-xl border bg-fd-card p-5 transition-all hover:-translate-y-0.5 hover:border-fd-primary/40 hover:shadow-lg hover:shadow-fd-primary/5"
                href={href}
                key={title}
              >
                <Icon className="size-5 text-fd-primary" />
                <h3 className="mt-5 font-semibold">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-fd-muted-foreground">
                  {description}
                </p>
                <span className="mt-5 inline-flex items-center gap-1 text-sm font-medium text-fd-primary">
                  Continue
                  <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-1" />
                </span>
              </Link>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}
