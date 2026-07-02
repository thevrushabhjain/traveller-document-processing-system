import './globals.css';
import { IBM_Plex_Sans, IBM_Plex_Mono, Karla } from 'next/font/google';
import { Toaster } from 'sonner';

const plexSans = IBM_Plex_Sans({
  weight: ['400', '500', '600', '700'],
  subsets: ['latin'],
  variable: '--font-plex-sans',
  display: 'swap',
});
const plexMono = IBM_Plex_Mono({
  weight: ['400', '500', '600'],
  subsets: ['latin'],
  variable: '--font-plex-mono',
  display: 'swap',
});
const karla = Karla({
  weight: ['400', '500', '600', '700'],
  subsets: ['latin'],
  variable: '--font-karla',
  display: 'swap',
});

export const metadata = {
  title: 'Traveller Document Processing System',
  description:
    'Offline AI-powered passport / Aadhaar / ID extraction. PaddleOCR, OpenCV, and structured JSON output.',
};

export default function RootLayout({ children }) {
  return (
    <html
      lang="en"
      className={`${plexSans.variable} ${plexMono.variable} ${karla.variable}`}
    >
      <body className="min-h-screen bg-appbg text-ink" data-testid="app-root">
        {children}
        <Toaster
          position="bottom-right"
          toastOptions={{
            style: {
              borderRadius: 0,
              border: '1px solid #09090B',
              background: '#FFFFFF',
              color: '#09090B',
              fontFamily: 'var(--font-plex-mono), ui-monospace, monospace',
              fontSize: 12,
            },
          }}
        />
      </body>
    </html>
  );
}
