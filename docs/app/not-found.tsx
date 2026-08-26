import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-[70vh] max-w-xl flex-col items-center justify-center px-6 text-center">
      <p className="text-sm font-medium text-fd-muted-foreground">404</p>
      <h1 className="mt-2 text-3xl font-bold">Page not found</h1>
      <p className="mt-3 text-fd-muted-foreground">
        This page may have moved as the documentation was consolidated.
      </p>
      <Link
        className="mt-6 rounded-full bg-fd-primary px-5 py-2.5 text-fd-primary-foreground"
        href="/docs"
      >
        Open documentation
      </Link>
    </main>
  );
}
