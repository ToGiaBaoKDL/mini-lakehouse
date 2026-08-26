import {
  type FileObject,
  printErrors,
  scanURLs,
  validateFiles,
} from "next-validate-link";
import { register } from "fumadocs-mdx/node";

register();

async function files(
  source: (typeof import("@/lib/source"))["source"],
): Promise<FileObject[]> {
  return Promise.all(
    source.getPages().map(async (page) => {
      if (!page.absolutePath) {
        throw new Error(`Missing source path for ${page.url}`);
      }

      return {
        content: await page.data.getText("raw"),
        data: page.data,
        path: page.absolutePath,
        // Documentation routes are directories in the static export. The
        // trailing slash keeps relative links anchored to the current page.
        url: page.url.endsWith("/") ? page.url : `${page.url}/`,
      };
    }),
  );
}

async function main() {
  const { source } = await import("@/lib/source");
  const scanned = await scanURLs({
    populate: {
      "docs/[[...slug]]": source.getPages().map((page) => ({
        hashes: page.data.toc.map((item) => item.url.slice(1)),
        value: { slug: page.slugs },
      })),
    },
    preset: "next",
  });

  const errors = await validateFiles(await files(source), {
    checkRelativePaths: "as-url",
    markdown: {
      components: {
        Card: { attributes: ["href"] },
      },
    },
    scanned,
  });

  printErrors(errors, true);
}

void main();
