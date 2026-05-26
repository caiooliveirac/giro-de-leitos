'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import * as Toast from '@radix-ui/react-toast';
import { initOfflineQueue, flush as flushOfflineQueue } from '@/lib/offline-queue';

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      }),
  );

  useEffect(() => {
    initOfflineQueue();
    if (typeof navigator !== 'undefined' && navigator.onLine) {
      // Fire and forget; do not block render.
      void flushOfflineQueue();
    }
  }, []);

  return (
    <QueryClientProvider client={client}>
      <Toast.Provider swipeDirection="down">{children}</Toast.Provider>
    </QueryClientProvider>
  );
}
