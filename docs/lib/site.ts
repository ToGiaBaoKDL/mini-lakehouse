export const site = {
  description:
    "Architecture, development, delivery, and operations for the Mini Lakehouse data platform.",
  docsPath: "/docs",
  githubBranch: "main",
  githubOwner: "ToGiaBaoKDL",
  githubRepository: "mini-lakehouse",
  name: "Mini Lakehouse",
  url: "https://mini-lakehouse-docs.tgblab.io.vn",
} as const;

export const githubUrl =
  `https://github.com/${site.githubOwner}/${site.githubRepository}` as const;
