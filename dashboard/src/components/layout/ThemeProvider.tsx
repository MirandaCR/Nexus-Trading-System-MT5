'use client';

import { type ReactNode, useEffect, useState } from 'react';

interface ThemeProviderProps {
  children: ReactNode;
}

export function ThemeProvider({ children }: ThemeProviderProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    document.documentElement.classList.add('dark');
    setMounted(true);
  }, []);

  if (!mounted) {
    return (
      <div className="dark min-h-screen bg-bg-primary">
        <div className="flex h-screen items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-neon-purple border-t-transparent" />
        </div>
      </div>
    );
  }

  return <div className="dark min-h-screen bg-bg-primary">{children}</div>;
}
