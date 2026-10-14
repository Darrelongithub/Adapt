"use client";

import { HomeDashboard } from "@/components/home-dashboard";
import { useSession } from "@/components/ui";
import type { Me } from "@/lib/api";

/** SessionGate owns the non-dismissible signed-out flow on every route. */
export default function Home() {
  const { me } = useSession();
  return me ? <HomeDashboard key={String(me.student_id)} me={me as Me} /> : null;
}
