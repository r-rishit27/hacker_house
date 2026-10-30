"use client";

import { useState } from "react";

import { ApprovalsDrawer } from "@/components/console/ApprovalsDrawer";
import { Console } from "@/components/console/Console";

/**
 * A deep link to the inbox: the console, with the drawer already open.
 *
 * Approving is something you do while looking at a case, so the inbox is a
 * drawer over the console rather than a page beside it. This route exists so
 * the link a 403 hands you still works when it is pasted somewhere else.
 */
export default function ApprovalsPage() {
  const [open, setOpen] = useState(true);
  return (
    <>
      <Console />
      <ApprovalsDrawer open={open} onClose={() => setOpen(false)} />
    </>
  );
}
