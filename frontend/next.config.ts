import type { NextConfig } from "next";

// Duplicates lib/base-path.ts on purpose: this file runs in Next's config
// loader before the app bundle exists, so it cannot share that module.
const configuredBasePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
const basePath = configuredBasePath
  ? `/${configuredBasePath.replace(/^\/+|\/+$/g, "")}`
  : "";

const nextConfig: NextConfig = {
  output: "standalone",
  basePath,
  experimental: { optimizePackageImports: ["lucide-react", "recharts"] },
};

export default nextConfig;
