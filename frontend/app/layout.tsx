import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Letterwise — Email intelligence",
  description: "A clearer inbox, powered by thoughtful AI.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="dark-theme">{children}</body>
    </html>
  );
}
