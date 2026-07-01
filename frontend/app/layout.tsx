import type { Metadata } from "next";
import { Providers } from "@/components/providers";
import { withBasePath } from "@/lib/base-path";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hugo — Operator dashboard",
  description: "Operate, approve, measure, and audit autonomous creator campaigns.",
  icons: {
    icon: withBasePath("/brand/hugo-icon.png"),
    apple: withBasePath("/brand/hugo-icon.png"),
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
