import type { Metadata } from "next"
import { Newsreader, Source_Sans_3 } from "next/font/google"
import "./globals.css"

const sans = Source_Sans_3({
  subsets: ["latin"],
  variable: "--font-source",
})

const serif = Newsreader({
  subsets: ["latin"],
  variable: "--font-serif",
})

export const metadata: Metadata = {
  title: "Sky Scout",
  description: "US airport capacity desk. Answers only from loaded public aviation files.",
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${sans.variable} ${serif.variable} font-[family-name:var(--font-source)] antialiased`}>
        {children}
      </body>
    </html>
  )
}
