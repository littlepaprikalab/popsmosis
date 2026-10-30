'use client';

import { useEffect } from 'react';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/lib/auth';
import { newPageView, setCounting } from '@/lib/plays';

/**
 * Where a page view begins for the play counts (lib/plays.ts): each new page counts its videos afresh, while a
 * player that is shown again on the same page (a card closed and opened, a search) does not count twice. And an
 * admin's plays are not counted at all, so the owner checking their own droplets never reads as a student watching.
 */
export function PlayCountScope() {
  const pathname = usePathname();
  const { user } = useAuth();
  useEffect(() => { newPageView(); }, [pathname]);
  useEffect(() => { setCounting(user?.role !== 'ADMIN'); }, [user]);
  return null;
}
