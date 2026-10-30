import { Console } from "@/components/console/Console";

/**
 * A deep link to one case. The same console, opened on it.
 *
 * The console is one screen rather than a set of pages, so this route does
 * not render a different thing — it renders the console with a selection, so
 * a link pasted into a chat lands where the sender was looking.
 */
export default async function CasePage({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = await params;
  return <Console initialCase={caseId} />;
}
