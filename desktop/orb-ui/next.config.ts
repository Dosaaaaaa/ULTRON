import type { NextConfig } from "next";

// Static export: the desktop window serves the built `out/` folder from a loopback-only server.
const nextConfig: NextConfig = { output: "export" };

export default nextConfig;
