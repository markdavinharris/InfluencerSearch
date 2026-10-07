import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: { unoptimized: true }, // avatars come from many CDNs; plain <img> is fine internally
};

export default nextConfig;
